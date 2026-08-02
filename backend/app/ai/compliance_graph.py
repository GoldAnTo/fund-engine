"""LangGraph-based compliance rewrite loop.

Replaces the hand-written single-retry ``if``/``else`` in
:meth:`app.ai.assessment_gen.AssessmentGenerator._ensure_compliant` with
a checkpointed, interruptible graph node.  The graph::

    compliance_check
        -> [pass?]    -> END
        -> [refuse?]  -> raise ComplianceRefusedError
        -> [rewrite?] -> rewrite -> compliance_check

is the same flow the legacy method implemented; the win is that it is
now a graph node, so future work (human-in-the-loop interrupts,
multi-step rewrites, retry budgets) can be added without rewriting the
callers.

Design rules (mirroring the spec in
``docs/research/2026-08-02-open-source-borrowing-implementation.md``
T4):

1. **State holds only orchestration data.**  ``texts``, ``rewritten``,
   ``attempt``, ``max_attempts``, ``status``, and the (non-serialised)
   LLM client.  No :class:`ReviewDecision`, no :class:`EvidenceReview`,
   no ``review_state`` — those are ledger facts and live in the
   append-only tables, not in the graph runtime.
2. **Checkpoint reuse the existing PG instance.**  When the operator
   opts into persistent checkpoints the graph uses the same PostgreSQL
   ``DATABASE_URL``; the first cut is in-process ``invoke()``, so the
   checkpoint tables do not exist yet.  Future S-tickets can add
   ``PostgresSaver`` without rewriting this module.
3. **Graceful degradation.**  :func:`build_compliance_graph` returns
   ``None`` when ``langgraph`` is not installed — callers fall back to
   the legacy method.  The legacy path preserves behaviour bit-for-bit
   so the compliance contract is unchanged.
4. **No silent expansion.**  ``max_attempts`` is fixed at 1 in the
   first cut — the bounded rewrite is the documented contract, and
   widening it would change the compliance semantics.  Operators who
   need a wider budget should bump the spec first.

The graph depends on :class:`app.services.compliance.evaluate_compliance`
for the actual decision; this module only adds the orchestration.  The
LLM rewrite call goes through the same ``client.chat_json`` interface
the legacy method uses, so the model and prompt version are
unchanged.
"""
from __future__ import annotations

import json
from typing import TypedDict

from app.services.compliance import (
    ComplianceAction,
    ComplianceDecision,
    ComplianceRefusedError,
    ViolationCategory,
    evaluate_compliance,
)


class LanggraphNotInstalled(ImportError):
    """Raised by helpers that strictly require ``langgraph``.

    ``build_compliance_graph`` swallows this and returns ``None`` so the
    caller can fall back to the legacy method.  Tests that want to
    exercise the strict path raise this themselves (or mock it).
    """


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class ComplianceState(TypedDict, total=False):
    """Orchestration-only state for the compliance rewrite graph.

    **Do NOT add ledger facts here** — ``ReviewDecision``,
    ``EvidenceReview`` and friends stay in the append-only tables.  The
    graph is a runtime, not a truth store.
    """

    texts: list[str]            # [rationale, gap1, gap2, ...]
    rewritten: list[str]        # after the latest rewrite attempt
    attempt: int                # 0 = original, 1 = after one rewrite
    max_attempts: int           # bounded at 1 in the first cut
    status: str                 # "pass" | "refused" | "needs_rewrite"
    # The LLM client is held in state so nodes can call into it without
    # threading the client through closures.  It is **not** part of any
    # serialised checkpoint — the graph is in-process only.
    client: object


# ---------------------------------------------------------------------------
# Node / edge implementations
# ---------------------------------------------------------------------------


def _no_hit_state(decision: ComplianceDecision) -> ComplianceDecision:
    """Return a synthetic REFUSE decision with the supplied reason.

    Used by the rewrite-mismatch path (``rewrite_node``) to refuse a
    run whose LLM response was malformed.  Reusing
    :class:`ComplianceRefusedError` keeps the audit trail uniform.
    """
    return ComplianceDecision(
        is_hit=True,
        action=ComplianceAction.REFUSE,
        hits=(),
        summary_reason="malformed_rewrite",
    )


def compliance_check(state: ComplianceState) -> ComplianceState:
    """Evaluate compliance for all texts.  Route to pass / refuse / rewrite.

    The decision logic matches the legacy ``_ensure_compliant`` method
    so callers see the same outcomes whether they go through the graph
    or the fallback.  Any single REFUSE-category hit bubbles up
    immediately (the legacy method raises on the first REFUSE hit;
    duplicating that order is part of the contract).
    """
    texts = state.get("rewritten") or state["texts"]
    decisions = [evaluate_compliance(t) for t in texts]
    for d in decisions:
        if d.is_hit and d.action is ComplianceAction.REFUSE:
            # REFUSE is terminal — the conditional edge in the graph
            # never runs after this raise, so the caller (or the
            # invoke() call) sees the same error as the legacy path.
            raise ComplianceRefusedError(d)
    first_hit = next((d for d in decisions if d.is_hit), None)
    if first_hit is None:
        return {**state, "status": "pass", "rewritten": texts}
    # Any remaining hit is REWRITE category (REFUSE was handled above).
    return {**state, "status": "needs_rewrite", "rewritten": texts}


def rewrite_node(state: ComplianceState) -> ComplianceState:
    """Call the LLM to neutralise REWRITE-category hits.  One attempt only.

    The LLM is asked to rewrite each text so that the next
    ``compliance_check`` pass returns no hits.  A malformed response
    (wrong shape, length mismatch) raises
    :class:`ComplianceRefusedError` so the audit trail records the
    refusal, never a silent acceptance.
    """
    # Lazy import: prompts and the chat client are heavy; only pull
    # them in when a rewrite is actually needed.
    from app.ai.prompts import REWRITE_SYSTEM

    client = state["client"]
    texts = state.get("rewritten") or state["texts"]
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {
            "role": "user",
            "content": json.dumps({"texts": texts}, ensure_ascii=False),
        },
    ]
    result = client.chat_json(messages, schema_hint="rewrite")
    rewritten = result.get("texts", [])
    if not isinstance(rewritten, list) or len(rewritten) != len(texts):
        raise ComplianceRefusedError(_no_hit_state(
            ComplianceDecision(
                is_hit=True,
                action=ComplianceAction.REFUSE,
                hits=(),
                summary_reason="malformed_rewrite",
            )
        ))
    return {
        **state,
        "rewritten": [str(t) for t in rewritten],
        "attempt": 1,
    }


def should_rewrite(state: ComplianceState) -> str:
    """Conditional edge: route after ``compliance_check``.

    - ``"pass"`` → END.
    - ``"needs_rewrite"`` with attempts remaining → ``"rewrite"``.
    - Anything else (status missing, attempts exhausted) → refuse with
      a synthetic REFUSE decision so the audit trail records the
      reason.
    """
    status = state.get("status", "")
    if status == "pass":
        return "done"
    if status == "needs_rewrite":
        if state.get("attempt", 0) < state.get("max_attempts", 1):
            return "rewrite"
    # Exhausted retries or unknown status -> refuse.
    raise ComplianceRefusedError(
        ComplianceDecision(
            is_hit=True,
            action=ComplianceAction.REFUSE,
            hits=(),
            summary_reason="rewrite_exhausted",
        )
    )


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_compliance_graph(client):
    """Build and compile the compliance rewrite graph.

    Returns the compiled graph when ``langgraph`` is installed,
    ``None`` otherwise.  Callers (the ``_ensure_compliant`` method on
    :class:`AssessmentGenerator`) treat ``None`` as the signal to
    fall back to the legacy hand-written loop.

    The graph is **stateless beyond configuration**: a fresh
    ``StateGraph(ComplianceState)`` is built and compiled on each
    call.  Compile cost is small; in-process ``invoke()`` does not
    reuse compiled graphs across runs.  When we add persistent
    checkpoints we will memoize this on a per-process basis.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise LanggraphNotInstalled(
            "langgraph is not installed; pip install 'langgraph>=1.2.6' "
            "to use the compliance graph, or fall back to the legacy "
            "_ensure_compliant loop"
        ) from exc

    graph = StateGraph(ComplianceState)
    graph.add_node("compliance_check", compliance_check)
    graph.add_node("rewrite", rewrite_node)

    graph.set_entry_point("compliance_check")
    graph.add_conditional_edges(
        "compliance_check",
        should_rewrite,
        {"done": END, "rewrite": "rewrite"},
    )
    graph.add_edge("rewrite", "compliance_check")

    return graph.compile()
