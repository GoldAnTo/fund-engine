"""AssessmentGenerator: LLM-driven AIAssessment from a frozen evidence snapshot.

Gathers the visible EvidenceLinks with their statement texts at the given
cutoff, asks the LLM to produce a three-valued conclusion (supported /
contradicted / insufficient_evidence), runs the compliance gate, and only
THEN freezes an EvidenceSnapshot and writes the assessment through
``AssessmentService.create_ai_assessment`` with
``displayed_as_provisional=True``.  Compliance-before-persistence: a refusal
leaves nothing in the ledger except the failed ``AIRun`` (the ledger's
immutability guard forbids deleting a half-frozen snapshot).

The compliance gate has one bounded repair loop: REFUSE-category hits
(investment advice, recommendations, position guidance) are refused
immediately; when every hit is a REWRITE-category expression (target price
or return promise), the model gets exactly one chance to neutralize the
text, the rewritten text is re-evaluated, and any residual hit refuses the
whole run.  The loop never iterates more than once.

Every completed assessment operation writes exactly one ``AIRun`` audit record
(``kind=assess``); a run whose text was repaired carries
``rewritten_for_compliance`` in its output summary.  A cooperative scope
cancellation before persistence deliberately writes neither an assessment nor
an audit row for the superseded run.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.client import LLMClient
from app.ai.error_safety import (
    AI_COMPLIANCE_ERROR_MESSAGE,
    AI_OPERATION_ERROR_MESSAGE,
    AI_PROTOCOL_ERROR_MESSAGE,
)
from app.ai.prompts import (
    ASSESS_PROMPT_VERSION,
    ASSESS_SYSTEM,
    REWRITE_SYSTEM,
)
from app.ai.runs import record_run
from app.models.ledger import (
    AIAssessment,
    SourceStatement,
    Thesis,
    ValidationError,
)
from app.repositories.research import ResearchRepository
from app.services.assessment import AssessmentService
from app.services.event_research_scope_evidence import lock_event_scope_case
from app.services.research_protocol import ResearchProtocolService
from app.services.compliance import (
    ComplianceAction,
    ComplianceRefusedError,
    evaluate_compliance,
)


class AssessmentGenerator:
    """Generates a provisional AIAssessment for a Thesis at a cutoff."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def generate(
        self,
        thesis_id: uuid.UUID,
        cutoff: datetime,
        session: Session,
        *,
        before_persist: Callable[[], bool] | None = None,
    ) -> AIAssessment | None:
        started_at = datetime.now(timezone.utc)
        repo = ResearchRepository(session)
        assessment_service = AssessmentService(repo, session)
        input_ref = {
            "thesis_id": str(thesis_id),
            "cutoff": cutoff.isoformat(),
            "initial_protocol_status": None,
            "research_protocol_status": None,
            "effective_binding_id": None,
            "mechanism_template_version_id": None,
            "verification_rule_ids": None,
            "evidence_link_ids": None,
            "link_count": None,
        }

        try:
            thesis = session.get(Thesis, thesis_id)
            if thesis is None:
                raise ValueError(f"thesis {thesis_id} not found")
            initial_gate = ResearchProtocolService(session).check_researchability(
                thesis_id
            )
            input_ref["initial_protocol_status"] = (
                initial_gate.status if thesis.research_protocol_required else None
            )
            if thesis.research_protocol_required:
                self._capture_protocol_footprint(input_ref, initial_gate)
            if thesis.research_protocol_required and initial_gate.status == "blocked":
                raise ValidationError(
                    "researchability gate blocked: "
                    + ", ".join(initial_gate.reason_codes)
                )
            # Compliance BEFORE persistence: gather the visible links and
            # run the model + non-investment-advice gate first.  Only when
            # the text passes do we freeze the snapshot and append the
            # assessment — a refusal therefore leaves nothing behind except
            # the failed AIRun recorded below (the ledger's immutability
            # guard forbids deleting a half-frozen snapshot).
            links = repo.visible_links(thesis_id=thesis_id, cutoff=cutoff)
            prompt_link_ids = [link.id for link in links]
            input_ref["evidence_link_ids"] = [
                str(link_id) for link_id in prompt_link_ids
            ]
            input_ref["link_count"] = len(prompt_link_ids)
            links_data: list[dict] = []
            for link in links:
                statement = session.get(SourceStatement, link.source_statement_id)
                links_data.append(
                    {
                        "role": link.role,
                        "reason": link.reason,
                        "statement_text": statement.normalized_text
                        if statement
                        else "",
                    }
                )

            user_data = {
                "thesis": thesis.statement,
                "links": links_data,
            }
            messages = [
                {"role": "system", "content": ASSESS_SYSTEM},
                {"role": "user", "content": json.dumps(user_data, ensure_ascii=False)},
            ]

            # Only reads have occurred so far.  Do not retain an idle
            # database transaction while waiting on an external provider.
            session.commit()
            result = self._client.chat_json(messages, schema_hint="assess")
            conclusion = result["conclusion"]
            rationale = result["rationale"]
            gaps = result.get("gaps", [])
            # Non-investment-advice gate (with one bounded rewrite attempt
            # for REWRITE-category hits): refused text never reaches the
            # ledger; the failure is recorded on the AIRun below.
            rationale, gaps, rewritten = self._ensure_compliant(rationale, gaps)

            # Auto research supplies a case/run/task output slot here.  If a
            # scope replacement committed while the provider was in flight,
            # leave no immutable snapshot, assessment, or AI audit output.
            if before_persist is not None and not before_persist():
                return None

            # Provider/compliance work can outlive a protocol change. Serialize
            # the final protocol footprint and immutable ledger writes with all
            # protocol mutations. Lock order is Case -> protocol rows; the
            # caller retains this lock through its outer commit.
            lock_event_scope_case(session, thesis.research_case_id)
            session.expire(thesis)
            thesis = session.get(Thesis, thesis_id)
            if thesis is None:
                raise ValidationError("thesis not found")
            final_gate = ResearchProtocolService(session).check_researchability(
                thesis_id
            )
            input_ref["final_protocol_status"] = (
                final_gate.status if thesis.research_protocol_required else None
            )
            frozen_rule_ids = sorted(
                str(rule_id) for rule_id in final_gate.verification_rule_ids
            )
            if thesis.research_protocol_required:
                self._capture_protocol_footprint(input_ref, final_gate)
            if thesis.research_protocol_required and final_gate.status == "blocked":
                raise ValidationError(
                    "researchability gate blocked: "
                    + ", ".join(final_gate.reason_codes)
                )
            if thesis.research_protocol_required:
                allowed_conclusions = (
                    ResearchProtocolService.allowed_assessment_conclusions(final_gate)
                )
                if conclusion not in allowed_conclusions:
                    if final_gate.status == "single_metric_monitoring":
                        conclusion = "insufficient_evidence"
                    else:
                        raise ValidationError(
                            f"researchability gate disallows conclusion: {conclusion}"
                        )
            if (
                thesis.research_protocol_required
                and final_gate.status == "single_metric_monitoring"
            ):
                # This protocol-derived machine reason is intentionally
                # independent of model prose and compliance rewrites.
                gaps = [
                    gap for gap in gaps if gap != "insufficient_primary_metrics"
                ]
                gaps.append("insufficient_primary_metrics")

            snapshot = assessment_service.freeze_snapshot(
                thesis_id,
                cutoff=cutoff,
                evidence_link_ids=prompt_link_ids,
            )
            assessment = assessment_service.create_ai_assessment(
                snapshot.id,
                conclusion=conclusion,
                rationale=rationale,
                gaps=gaps,
                research_protocol_status=(
                    final_gate.status if thesis.research_protocol_required else None
                ),
                effective_binding_id=(
                    final_gate.effective_binding_id
                    if thesis.research_protocol_required
                    else None
                ),
                mechanism_template_version_id=(
                    final_gate.mechanism_template_version_id
                    if thesis.research_protocol_required
                    else None
                ),
                verification_rule_ids=(
                    frozen_rule_ids if thesis.research_protocol_required else None
                ),
            )

            input_ref["snapshot_id"] = str(snapshot.id)
            summary = f"conclusion={conclusion}, links={len(links)}"
            if rewritten:
                summary += ", rewritten_for_compliance"
            record_run(
                session,
                kind="assess",
                model_version=self._client.model_version,
                prompt_version=ASSESS_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary=summary,
                status="success",
                started_at=started_at,
            )
            return assessment

        except Exception as exc:
            # A failed assessment is a separate clean unit of work: discard
            # every partial immutable domain write, then append exactly one
            # durable audit row for the caller to commit with its failure
            # handling. Worker task state was committed before provider work.
            session.rollback()
            if isinstance(exc, ComplianceRefusedError):
                safe_error = AI_COMPLIANCE_ERROR_MESSAGE
            elif isinstance(exc, ValidationError):
                safe_error = AI_PROTOCOL_ERROR_MESSAGE
            else:
                safe_error = AI_OPERATION_ERROR_MESSAGE
            record_run(
                session,
                kind="assess",
                model_version=self._client.model_version,
                prompt_version=ASSESS_PROMPT_VERSION,
                input_ref=input_ref,
                output_summary="",
                status="failed",
                error=safe_error,
                started_at=started_at,
            )
            raise

    @staticmethod
    def _capture_protocol_footprint(input_ref: dict, gate) -> None:
        """Attach one strict gate footprint to the mutable AIRun input."""
        input_ref["research_protocol_status"] = gate.status
        input_ref["effective_binding_id"] = (
            str(gate.effective_binding_id)
            if gate.effective_binding_id is not None
            else None
        )
        input_ref["mechanism_template_version_id"] = (
            str(gate.mechanism_template_version_id)
            if gate.mechanism_template_version_id is not None
            else None
        )
        input_ref["verification_rule_ids"] = sorted(
            str(rule_id) for rule_id in gate.verification_rule_ids
        )

    # ------------------------------------------------------------------ compliance

    def _ensure_compliant(
        self, rationale: str, gaps: list
    ) -> tuple[str, list, bool]:
        """Non-investment-advice gate with one bounded rewrite attempt.

        Returns ``(rationale, gaps, rewritten)``.  REFUSE-category hits are
        refused immediately and never reach the rewrite stage.  When every
        hit is a REWRITE-category expression (target price / return
        promise), the model gets exactly one chance to neutralize the text;
        the rewritten text is re-evaluated through the same gate and any
        residual hit refuses the whole run.  A malformed rewrite response
        (wrong shape or length) refuses with the original decision.

        Implementation: tries the LangGraph-based compliance graph first
        (see :mod:`app.ai.compliance_graph`); when ``langgraph`` is not
        installed, falls back to the original hand-written loop.  Both
        paths produce identical outcomes for the same inputs — the
        legacy method is preserved verbatim under
        :meth:`_ensure_compliant_legacy` so the contract is bit-for-bit
        stable.
        """
        from app.ai.compliance_graph import build_compliance_graph

        try:
            graph = build_compliance_graph(self._client)
        except Exception:  # pragma: no cover — ImportError, etc.
            graph = None
        if graph is not None:
            return self._ensure_compliant_via_graph(rationale, gaps, graph)
        return self._ensure_compliant_legacy(rationale, gaps)

    def _ensure_compliant_via_graph(
        self, rationale: str, gaps: list, graph
    ) -> tuple[str, list, bool]:
        """LangGraph path: invoke the compiled graph and extract results.

        The graph is built with one bounded rewrite attempt (``max_attempts=1``)
        so the contract is the same as :meth:`_ensure_compliant_legacy`.  The
        client is held in state (not serialised); this method is therefore
        only safe to call in-process — operators who need persistent
        checkpoints should refactor the client to be injected via
        ``RunnableConfig`` rather than state.
        """
        from app.ai.compliance_graph import ComplianceState

        texts = [rationale, *[str(g) for g in gaps]]
        initial_state: ComplianceState = {
            "texts": texts,
            "rewritten": texts,
            "attempt": 0,
            "max_attempts": 1,
            "status": "",
            "client": self._client,
        }
        final_state = graph.invoke(initial_state)
        rewritten = final_state.get("rewritten", texts)
        was_rewritten = final_state.get("attempt", 0) > 0
        if not rewritten:
            # Defensive: should never happen (the graph raises on
            # failure) but a typed-state hole is one missing key away
            # from a real bug, so keep the contract explicit.
            return rationale, gaps, False
        return rewritten[0], rewritten[1:], was_rewritten

    def _ensure_compliant_legacy(
        self, rationale: str, gaps: list
    ) -> tuple[str, list, bool]:
        """Original hand-written loop, preserved as the fallback path.

        Behaviour is identical to the pre-T4 ``_ensure_compliant``; the
        graph path in :meth:`_ensure_compliant_via_graph` is the only
        change.  Kept verbatim so existing tests in
        ``test_ai_engine.py`` / ``test_compliance.py`` remain green
        when ``langgraph`` is missing.
        """
        texts = [rationale, *[str(g) for g in gaps]]
        decisions = [evaluate_compliance(t) for t in texts]
        first_hit = next((d for d in decisions if d.is_hit), None)
        if first_hit is None:
            return rationale, gaps, False
        for decision in decisions:
            if decision.is_hit and decision.action is ComplianceAction.REFUSE:
                raise ComplianceRefusedError(decision)

        messages = [
            {"role": "system", "content": REWRITE_SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"texts": texts}, ensure_ascii=False),
            },
        ]
        result = self._client.chat_json(messages, schema_hint="rewrite")
        rewritten = result.get("texts", [])
        if not isinstance(rewritten, list) or len(rewritten) != len(texts):
            raise ComplianceRefusedError(first_hit)
        rewritten = [str(t) for t in rewritten]
        for text in rewritten:
            decision = evaluate_compliance(text)
            if decision.is_hit:
                raise ComplianceRefusedError(decision)
        return rewritten[0], rewritten[1:], True
