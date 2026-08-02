"""Tests for the LangGraph-based compliance rewrite loop
(``app.ai.compliance_graph``) and the assessment path that uses it.

The ``langgraph`` package is an **optional** dependency — the test
suite splits accordingly:

- **Always-run tests** — exercise the lazy-import / fallback
  (``LanggraphNotInstalled``) and the node / edge logic via
  in-process function calls; no graph machinery needed.
- **Conditional tests** — install a fake ``langgraph`` module in
  ``sys.modules`` and run the full ``invoke()`` path.  This proves
  the orchestration behaves as documented without requiring the real
  package on the test runner.
- **Integration with the legacy fallback** — drive
  :meth:`AssessmentGenerator._ensure_compliant` with no ``langgraph``
  installed; the method must produce the same outcomes the legacy
  path produced before T4.
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.compliance_graph import (
    ComplianceState,
    LanggraphNotInstalled,
    build_compliance_graph,
    compliance_check,
    rewrite_node,
    should_rewrite,
)
from app.services.compliance import (
    ComplianceAction,
    ComplianceDecision,
    ComplianceRefusedError,
    ViolationCategory,
    evaluate_compliance,
)


# ---------------------------------------------------------------------------
# Always-run: build_compliance_graph when langgraph is missing
# ---------------------------------------------------------------------------


def test_build_compliance_graph_raises_when_langgraph_missing(monkeypatch):
    """Hiding ``langgraph`` from ``sys.modules`` must surface as
    :class:`LanggraphNotInstalled` (an ``ImportError`` subclass) so
    the caller can route to the legacy method via a single
    ``except ImportError``."""
    hidden = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "langgraph" or name.startswith("langgraph.")
    }
    for name in list(hidden):
        monkeypatch.delitem(sys.modules, name)
    sys.modules["langgraph"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(LanggraphNotInstalled) as ei:
            build_compliance_graph(MagicMock())
        assert "langgraph" in str(ei.value).lower()
    finally:
        sys.modules.pop("langgraph", None)
        for name, mod in hidden.items():
            sys.modules[name] = mod


def test_langgraph_not_installed_is_import_error():
    """The exception type is the same family as :class:`AkshareError`
    and :class:`DoclingNotInstalled`: ``ImportError`` so callers can
    use a single ``except ImportError`` for "optional dep missing"."""
    assert issubclass(LanggraphNotInstalled, ImportError)


# ---------------------------------------------------------------------------
# Always-run: compliance_check / should_rewrite logic
# ---------------------------------------------------------------------------


def test_compliance_check_passes_when_no_hits():
    """No hits → status='pass', rewritten mirrors the input texts."""
    state: ComplianceState = {
        "texts": ["营业收入 50 亿元，同比增长 20%"],
        "rewritten": ["营业收入 50 亿元，同比增长 20%"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": MagicMock(),
    }
    out = compliance_check(state)
    assert out["status"] == "pass"
    assert out["rewritten"] == state["texts"]


def test_compliance_check_routes_to_rewrite_on_rewrite_hits():
    """A REWRITE-category hit (target price / return promise) must
    route to the rewrite branch, NOT raise."""
    state: ComplianceState = {
        "texts": ["目标价 120 元"],
        "rewritten": ["目标价 120 元"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": MagicMock(),
    }
    out = compliance_check(state)
    assert out["status"] == "needs_rewrite"


def test_compliance_check_raises_on_refuse_hit():
    """A REFUSE-category hit must raise ComplianceRefusedError
    immediately, even if other texts are clean."""
    state: ComplianceState = {
        "texts": ["营业收入 50 亿元", "建议买入该股票"],
        "rewritten": ["营业收入 50 亿元", "建议买入该股票"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": MagicMock(),
    }
    with pytest.raises(ComplianceRefusedError) as ei:
        compliance_check(state)
    assert ei.value.decision.action is ComplianceAction.REFUSE


def test_should_rewrite_routes_done_on_pass():
    """``status='pass'`` routes to END ('done')."""
    state: ComplianceState = {"status": "pass", "attempt": 0, "max_attempts": 1}
    assert should_rewrite(state) == "done"


def test_should_rewrite_routes_rewrite_on_remaining_budget():
    """``status='needs_rewrite'`` with ``attempt < max_attempts`` routes to rewrite."""
    state: ComplianceState = {"status": "needs_rewrite", "attempt": 0, "max_attempts": 1}
    assert should_rewrite(state) == "rewrite"


def test_should_rewrite_raises_on_exhausted_budget():
    """``status='needs_rewrite'`` with ``attempt == max_attempts`` must
    refuse the run — the bounded rewrite is the documented contract."""
    state: ComplianceState = {"status": "needs_rewrite", "attempt": 1, "max_attempts": 1}
    with pytest.raises(ComplianceRefusedError) as ei:
        should_rewrite(state)
    assert "exhausted" in ei.value.decision.summary_reason


def test_should_rewrite_raises_on_unknown_status():
    """An empty / unknown status must refuse rather than silently pass
    — a missing state key is one bug away from a real audit gap."""
    state: ComplianceState = {"status": "", "attempt": 0, "max_attempts": 1}
    with pytest.raises(ComplianceRefusedError):
        should_rewrite(state)


# ---------------------------------------------------------------------------
# rewrite_node — LLM interaction
# ---------------------------------------------------------------------------


def _make_rewrite_state(texts: list[str], *, chat_response: dict) -> ComplianceState:
    """Build a ComplianceState whose ``client.chat_json`` returns the
    supplied dict (so we can drive rewrite_node in isolation)."""
    client = MagicMock()
    client.chat_json.return_value = chat_response
    return ComplianceState(
        texts=texts,
        rewritten=texts,
        attempt=0,
        max_attempts=1,
        status="needs_rewrite",
        client=client,
    )


def test_rewrite_node_returns_rewritten_texts_on_valid_response():
    """A response whose ``texts`` list matches the input length must
    return the rewritten texts and bump ``attempt`` to 1."""
    state = _make_rewrite_state(
        ["目标价 120 元"],
        chat_response={"texts": ["未来增长可期"]},
    )
    out = rewrite_node(state)
    assert out["rewritten"] == ["未来增长可期"]
    assert out["attempt"] == 1


def test_rewrite_node_raises_on_length_mismatch():
    """A rewrite response whose length differs from the input is
    malformed — refuse with the malformed-rewrite reason."""
    state = _make_rewrite_state(
        ["目标价 120 元", "稳赚不赔"],
        chat_response={"texts": ["only one item"]},
    )
    with pytest.raises(ComplianceRefusedError) as ei:
        rewrite_node(state)
    assert ei.value.decision.summary_reason == "malformed_rewrite"


def test_rewrite_node_raises_on_non_list_response():
    """A non-list ``texts`` field (e.g. a string) is malformed."""
    state = _make_rewrite_state(
        ["目标价 120 元"],
        chat_response={"texts": "not a list"},
    )
    with pytest.raises(ComplianceRefusedError):
        rewrite_node(state)


# ---------------------------------------------------------------------------
# Conditional: full graph invoke() with a fake langgraph
# ---------------------------------------------------------------------------


def _install_fake_langgraph(monkeypatch):
    """Inject a minimal ``langgraph.graph`` module that records the
    graph construction and provides a working ``StateGraph`` /
    ``END``.  The compiled graph's ``invoke`` runs the nodes in
    topological order — good enough to exercise the orchestration
    end-to-end without the real package.

    The fake supports:

    - ``add_node(name, fn)``
    - ``set_entry_point(name)``
    - ``add_edge(from, to)``
    - ``add_conditional_edges(from, route_fn, mapping)``
    - ``compile()`` returns a CompiledGraph whose ``invoke(state)``
      runs the entry node, then dispatches via the conditional
      edges until a terminal state is reached.
    """
    graph_module = types.ModuleType("langgraph.graph")
    root_module = types.ModuleType("langgraph")
    root_module.graph = graph_module
    graph_module.END = "__END__"

    class StateGraph:
        def __init__(self, _state_type):
            self._nodes: dict[str, Any] = {}
            self._entry: str | None = None
            self._edges: dict[str, str] = {}
            self._conditional: dict[str, tuple[Any, dict[str, str]]] = {}

        def add_node(self, name: str, fn):
            self._nodes[name] = fn

        def set_entry_point(self, name: str):
            self._entry = name

        def add_edge(self, from_: str, to: str):
            self._edges[from_] = to

        def add_conditional_edges(self, from_, route_fn, mapping):
            self._conditional[from_] = (route_fn, mapping)

        def compile(self):
            outer = self

            class CompiledGraph:
                def invoke(self, state):
                    return self._run(state)

                def _run(self, state):
                    current = outer._entry
                    # The state is mutated by nodes; the loop also
                    # detects a missing current (route to END) and
                    # bails.  We cap at 16 iterations to avoid an
                    # infinite loop on a buggy graph definition.
                    for _ in range(16):
                        if current is None or current == graph_module.END:
                            return state
                        state = outer._nodes[current](state)
                        if current in outer._conditional:
                            route_fn, mapping = outer._conditional[current]
                            # The route function may raise (e.g.
                            # should_rewrite raises on exhausted
                            # budget) — propagate so the caller sees
                            # the same error the legacy path would.
                            target = route_fn(state)
                            if target not in mapping:
                                raise RuntimeError(
                                    f"route {target!r} not in mapping {mapping!r}"
                                )
                            current = mapping[target]
                        elif current in outer._edges:
                            current = outer._edges[current]
                        else:
                            # No outgoing edge — assume terminal.
                            return state
                    raise RuntimeError("graph iteration limit reached")

            return CompiledGraph()

    graph_module.StateGraph = StateGraph
    monkeypatch.setitem(sys.modules, "langgraph", root_module)
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph_module)


def test_graph_path_passes_when_no_hits(monkeypatch):
    """No compliance hits → graph runs compliance_check once and ends,
    returning the original texts with ``rewritten=False``."""
    _install_fake_langgraph(monkeypatch)
    monkeypatch.setattr(
        "app.ai.compliance_graph.evaluate_compliance",
        lambda _t: ComplianceDecision(is_hit=False, action=ComplianceAction.ALLOW),
    )
    graph = build_compliance_graph(MagicMock())
    state: ComplianceState = {
        "texts": ["营业收入 50 亿元"],
        "rewritten": ["营业收入 50 亿元"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": MagicMock(),
    }
    out = graph.invoke(state)
    assert out["status"] == "pass"
    assert out["rewritten"] == state["texts"]
    # No rewrite attempt was made.
    assert out["attempt"] == 0


def test_graph_path_refuses_on_refuse_hit(monkeypatch):
    """A REFUSE-category hit must raise ComplianceRefusedError, never
    fall through to the rewrite branch."""
    _install_fake_langgraph(monkeypatch)
    decisions = iter([
        ComplianceDecision(is_hit=True, action=ComplianceAction.REFUSE,
                          summary_reason="buy_sell_advice"),
    ])
    monkeypatch.setattr(
        "app.ai.compliance_graph.evaluate_compliance",
        lambda _t: next(decisions),
    )
    graph = build_compliance_graph(MagicMock())
    state: ComplianceState = {
        "texts": ["建议买入该股票"],
        "rewritten": ["建议买入该股票"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": MagicMock(),
    }
    with pytest.raises(ComplianceRefusedError) as ei:
        graph.invoke(state)
    assert ei.value.decision.action is ComplianceAction.REFUSE


def test_graph_path_rewrites_on_rewrite_hit(monkeypatch):
    """A REWRITE-category hit triggers the rewrite branch, the LLM
    returns clean text, the second compliance_check pass returns no
    hits, and the graph ends with ``attempt=1``."""
    _install_fake_langgraph(monkeypatch)
    # First call (compliance_check on the original text) sees the
    # REWRITE hit; subsequent calls (compliance_check after rewrite)
    # see clean text.  The fake's evaluate_compliance is called once
    # per invoke; use a counter to switch behaviour.
    calls = {"n": 0}

    def fake_evaluate(_t):
        calls["n"] += 1
        if calls["n"] == 1:
            return ComplianceDecision(
                is_hit=True, action=ComplianceAction.REWRITE,
                summary_reason="target_price",
            )
        return ComplianceDecision(is_hit=False, action=ComplianceAction.ALLOW)

    monkeypatch.setattr("app.ai.compliance_graph.evaluate_compliance", fake_evaluate)
    # The LLM client returns a clean rewrite.
    client = MagicMock()
    client.chat_json.return_value = {"texts": ["未来增长可期"]}
    graph = build_compliance_graph(client)
    state: ComplianceState = {
        "texts": ["目标价 120 元"],
        "rewritten": ["目标价 120 元"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": client,
    }
    out = graph.invoke(state)
    assert out["status"] == "pass"
    assert out["rewritten"] == ["未来增长可期"]
    assert out["attempt"] == 1
    # The LLM was called exactly once.
    assert client.chat_json.call_count == 1


def test_graph_path_refuses_when_rewrite_still_hits(monkeypatch):
    """If the LLM's rewrite still trips the compliance gate, the
    second compliance_check pass sees the hit but the attempt budget
    is exhausted — ``should_rewrite`` must refuse."""
    _install_fake_langgraph(monkeypatch)
    # First call: REWRITE hit (route to rewrite branch).  Second
    # call (after rewrite): REWRITE hit again, but the budget is
    # exhausted so should_rewrite raises.
    def fake_evaluate(_t):
        return ComplianceDecision(
            is_hit=True, action=ComplianceAction.REWRITE,
            summary_reason="return_promise",
        )

    monkeypatch.setattr("app.ai.compliance_graph.evaluate_compliance", fake_evaluate)
    client = MagicMock()
    client.chat_json.return_value = {"texts": ["稳赚不赔"]}
    graph = build_compliance_graph(client)
    state: ComplianceState = {
        "texts": ["稳赚不赔的机会"],
        "rewritten": ["稳赚不赔的机会"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": client,
    }
    with pytest.raises(ComplianceRefusedError) as ei:
        graph.invoke(state)
    assert "exhausted" in ei.value.decision.summary_reason


def test_graph_path_refuses_on_malformed_rewrite(monkeypatch):
    """A rewrite response whose length does not match the input is
    malformed — the rewrite_node must raise ComplianceRefusedError."""
    _install_fake_langgraph(monkeypatch)

    def fake_evaluate(_t):
        return ComplianceDecision(
            is_hit=True, action=ComplianceAction.REWRITE,
            summary_reason="target_price",
        )

    monkeypatch.setattr("app.ai.compliance_graph.evaluate_compliance", fake_evaluate)
    client = MagicMock()
    client.chat_json.return_value = {"texts": ["only one"]}  # length mismatch (input has 2)
    graph = build_compliance_graph(client)
    state: ComplianceState = {
        "texts": ["目标价 120 元", "稳赚不赔"],
        "rewritten": ["目标价 120 元", "稳赚不赔"],
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": client,
    }
    with pytest.raises(ComplianceRefusedError) as ei:
        graph.invoke(state)
    assert ei.value.decision.summary_reason == "malformed_rewrite"


# ---------------------------------------------------------------------------
# AssessmentGenerator integration — fallback when langgraph is missing
# ---------------------------------------------------------------------------


def test_assessment_generator_falls_back_to_legacy_when_no_langgraph(monkeypatch):
    """With ``langgraph`` not installed, ``_ensure_compliant`` must
    produce the same outcomes the legacy path produced before T4."""

    def fake_evaluate(t):
        # Returns REFUSE on any text containing the trigger keyword.
        if "建议买入" in t:
            return ComplianceDecision(
                is_hit=True, action=ComplianceAction.REFUSE,
                summary_reason="buy_sell_advice",
            )
        return ComplianceDecision(is_hit=False, action=ComplianceAction.ALLOW)

    monkeypatch.setattr(
        "app.services.compliance.evaluate_compliance", fake_evaluate
    )
    # Force build_compliance_graph to fail as it does when langgraph
    # is not installed.
    def fake_build(_client):
        return None
    monkeypatch.setattr(
        "app.ai.compliance_graph.build_compliance_graph", fake_build
    )

    gen = AssessmentGenerator(MagicMock())
    # No hits → returns originals, rewritten=False.
    rationale, gaps, rewritten = gen._ensure_compliant(
        "营业收入 50 亿元", ["研发投入持续加大"]
    )
    assert rewritten is False
    assert rationale == "营业收入 50 亿元"
    # REFUSE hit → raises.
    with pytest.raises(ComplianceRefusedError):
        gen._ensure_compliant("建议买入该股票", [])


def test_assessment_generator_via_graph_when_available(monkeypatch):
    """When ``langgraph`` is importable, the generator routes through
    the graph path and surfaces the rewritten text + ``rewritten=True``."""
    _install_fake_langgraph(monkeypatch)

    def fake_evaluate(t):
        if "目标价" in t:
            return ComplianceDecision(
                is_hit=True, action=ComplianceAction.REWRITE,
                summary_reason="target_price",
            )
        return ComplianceDecision(is_hit=False, action=ComplianceAction.ALLOW)

    monkeypatch.setattr("app.ai.compliance_graph.evaluate_compliance", fake_evaluate)

    client = MagicMock()
    client.chat_json.return_value = {"texts": ["未来增长可期"]}
    gen = AssessmentGenerator(client)
    rationale, gaps, rewritten = gen._ensure_compliant("目标价 120 元", [])
    assert rewritten is True
    assert rationale == "未来增长可期"
    assert gaps == []


# ---------------------------------------------------------------------------
# State — ledger facts must not leak into orchestration
# ---------------------------------------------------------------------------


def test_state_keys_are_orchestration_only():
    """``ComplianceState`` is a TypedDict — its declared keys must
    only describe orchestration data, not ledger facts.  A future
    contributor adding a ``ReviewDecision`` field would silently
    re-introduce the dual-truth problem this module was built to
    avoid; this test pins the contract."""
    declared = set(ComplianceState.__annotations__.keys())
    # The 6 declared fields, all orchestration.
    assert declared == {
        "texts", "rewritten", "attempt", "max_attempts", "status", "client",
    }
    # Belt-and-braces: none of the ledger-model names appear.
    forbidden = {"ReviewDecision", "EvidenceReview", "review_state", "AIRun"}
    leaked = forbidden & declared
    assert not leaked, f"ledger fact leaked into state: {leaked}"
