"""Lease-fenced professional DAG execution; no transaction spans provider work."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import (
    LLMAttempt,
    LLMClient,
    LLMDeadlineError,
    LLMInputBudgetError,
    LLMMalformedResponseError,
    LLMProviderError,
)
from app.ai.llm_config import bounded_number
from app.api.v1.tenant_context import ResearchActor
from app.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.operational import ResearchRun
from app.models.research_gateway import ResearchRunSpec
from app.models.research_team import (
    PROFESSIONAL_ROLES,
    ProfessionalAttempt,
    ProfessionalDependency,
    ProfessionalOutput,
    ProfessionalTask,
    ResearchTeam,
    utcnow,
)
from app.schemas.v1.research_team import ProfessionalResult
from app.services.research_gateway_content import ResearchGatewayContent
from app.services.research_team import append_event, latest_tasks
from app.services.research_team_generation import QUALITY_CHECKS, generate_role

_PARENTS = {"industry": set(), "finance": set(), "strategy": {"industry", "finance"},
            "quality": {"industry", "finance", "strategy"}}

_AUTH_ERRORS = (NotFoundError, PermissionDeniedError, ConflictError, ValueError, TypeError, KeyError)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _manifest(evidence: list[dict]) -> list[dict]:
    return [{"evidence_link_id": item["evidence_link_id"],
             "content_sha256": item["content_sha256"],
             "quote_sha256": hashlib.sha256(item["quote"].encode()).hexdigest()}
            for item in evidence]


def _check_contract(role: str) -> dict:
    return {
        "requires_checks": role == "quality",
        "required_check_kinds": sorted(QUALITY_CHECKS) if role == "quality" else [],
        "task_ids_must_be_nonempty": role == "quality",
        "task_ids_must_cover_dependency_ids": role == "quality",
    }


class _Blocked(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class _Claim:
    run_spec_id: UUID
    task_id: UUID
    token: UUID
    task_attempt: int
    role: str
    payload: dict
    input_sha256: str


class ProfessionalWorker:
    """Execute one newest eligible role per call and meter each SDK attempt.

    Production commands inject ``fatal_handler=os._exit`` so a failed lease
    heartbeat terminates the dedicated worker before a stale lease is reclaimed.
    Library use never terminates its host: heartbeat failure fences publication.
    A remote provider may still consume an abandoned request after a crash;
    this worker guarantees fenced output acceptance, not remote exactly-once.
    """

    def __init__(
        self, session_factory, *, client_factory=LLMClient.from_env,
        lease_seconds: float = 240, clock: Callable[[], datetime] = utcnow,
        fatal_handler: Callable[[int], None] | None = None,
    ) -> None:
        bounded_number("lease_seconds", lease_seconds, 1, 3600)
        self._session_factory, self._client_factory = session_factory, client_factory
        self._lease_seconds, self._clock = lease_seconds, clock
        self._fatal_handler = fatal_handler

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self._session_factory() as session:
            # SQLite has no row locks; acquire its writer before the first read.
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def run_once(self) -> bool:
        claim, changed = self._claim()
        if claim is None:
            return changed
        attempts: list[LLMAttempt] = []
        result = None
        reason = None
        heartbeat = _LeaseHeartbeat(self, claim)
        try:
            client = self._client_factory()
            heartbeat.start()
            with client.capture_attempts(attempts.append):
                result = generate_role(client, role=claim.role, payload=claim.payload)
        except LLMInputBudgetError:
            reason = "input_budget_exceeded"
        except LLMMalformedResponseError:
            reason = "invalid_model_result"
        except LLMDeadlineError:
            reason = "llm_deadline_exceeded"
        except LLMProviderError:
            reason = "llm_provider_error"
        except ValueError:
            reason = "invalid_model_result"
        except Exception:  # noqa: BLE001 - stable persisted failure; never raw provider/SQL text
            reason = "worker_error"
        finally:
            heartbeat.stop()
        if heartbeat.lost:
            reason = "lease_renewal_failed"
            result = None
        self._finish(claim, result, attempts, reason)
        return True

    def _claim(self) -> tuple[_Claim | None, bool]:
        with self._session_factory() as session:
            run_ids = list(session.scalars(select(ResearchTeam.run_spec_id)
                                          .where(ResearchTeam.status == "active")
                                          .order_by(ResearchTeam.updated_at, ResearchTeam.run_spec_id)))
        changed = False
        for run_spec_id in run_ids:
            # Release each team promptly if none of its current tasks can run.
            with self._transaction() as session:
                team = session.scalar(select(ResearchTeam).where(
                    ResearchTeam.run_spec_id == run_spec_id, ResearchTeam.status == "active",
                ).with_for_update(skip_locked=True))
                if team is None:
                    continue
                current = latest_tasks(session, run_spec_id)
                for role in PROFESSIONAL_ROLES:
                    task = current.get(role)
                    now = _utc(self._clock())
                    if task is None or not self._eligible(task, now):
                        continue
                    spec = session.get(ResearchRunSpec, run_spec_id)
                    try:
                        if spec is None:
                            raise _Blocked("input_not_authorized")
                        self._native_ready(session, spec)
                        payload = self._load_payload(session, spec, task, current)
                    except _Blocked as exc:
                        state = "cancelled" if exc.reason == "native_cancelled" else "blocked"
                        changed |= self._set_state(session, team, task, state, exc.reason)
                        continue
                    except _AUTH_ERRORS:
                        changed |= self._set_state(session, team, task, "blocked", "input_not_authorized")
                        continue
                    stale = task.status == "running"
                    token = uuid4()
                    task.status, task.reason_code = "running", None
                    task.attempt += 1
                    task.lease_token = token
                    task.lease_expires_at = _utc(self._clock()) + timedelta(seconds=self._lease_seconds)
                    task.updated_at = _utc(self._clock())
                    append_event(session, team, "task_started", task=task,
                                 reason="lease_recovered" if stale else None)
                    session.flush()
                    return _Claim(run_spec_id, task.id, token, task.attempt, role,
                                  payload, _hash(payload)), True
        return None, changed

    @staticmethod
    def _eligible(task: ProfessionalTask, now: datetime) -> bool:
        return task.status in {"queued", "blocked"} or (
            task.status == "running" and (task.lease_expires_at is None or _utc(task.lease_expires_at) <= now)
        )

    def _set_state(self, session, team, task, status, reason) -> bool:
        if task.status == status and task.reason_code == reason:
            return False
        task.status, task.reason_code = status, reason
        task.lease_token = task.lease_expires_at = None
        task.updated_at = _utc(self._clock())
        append_event(session, team, f"task_{status}", task=task, reason=reason)
        # Content reads expire the identity map: flush operational transitions first.
        session.flush()
        return True

    @staticmethod
    def _native_ready(session, spec) -> None:
        run = session.get(ResearchRun, spec.native_run_id)
        if run is None or run.research_case_id != spec.native_case_id:
            raise _Blocked("input_not_authorized")
        if run.status in {"failed", "cancelled", "paused"}:
            raise _Blocked(f"native_{run.status}")
        if run.status != "succeeded":
            raise _Blocked("waiting_for_native_completion")

    def _load_payload(self, session, spec, task, current, *, original: dict | None = None) -> dict:
        parents = list(session.scalars(select(ProfessionalTask).join(
            ProfessionalDependency, ProfessionalDependency.parent_task_id == ProfessionalTask.id,
        ).where(ProfessionalDependency.task_id == task.id,
                ProfessionalDependency.run_spec_id == spec.id).order_by(ProfessionalTask.role)))
        upstream = [parent for parent in parents if parent.role != task.role]
        history = [parent for parent in parents if parent.role == task.role]
        if (len(upstream) != len(_PARENTS[task.role])
                or {parent.role for parent in upstream} != _PARENTS[task.role]
                or len(history) > 1 or any(parent.revision >= task.revision for parent in history)):
            raise _Blocked("invalid_dependency_graph")
        outputs = []
        for parent in parents:
            # An explicit edge freezes its predecessor version. A directed
            # question may intentionally depend on the prior same-role answer.
            if parent.run_spec_id != spec.id:
                raise _Blocked("dependency_not_authorized")
            if parent.status != "succeeded":
                reason = f"dependency_{parent.status}" if parent.status in {"failed", "cancelled"} else "waiting_for_dependencies"
                raise _Blocked(reason)
            output = session.scalar(select(ProfessionalOutput).where(ProfessionalOutput.task_id == parent.id,
                                                                      ProfessionalOutput.run_spec_id == spec.id))
            if output is None:
                raise _Blocked("waiting_for_dependencies")
            outputs.append((parent, output))
        actor = ResearchActor(spec.tenant_id, frozenset(), spec.subject_id)
        content = ResearchGatewayContent(session)
        if original is None:
            view = content.read_research(actor, spec.conversation_id, spec.id)
            if view.truncated:
                raise _Blocked("input_budget_exceeded")
            evidence_ids = [UUID(item.evidence_link_id) for item in view.evidence]
        else:
            evidence_ids = [UUID(item["evidence_link_id"]) for item in original["evidence"]]
        if not evidence_ids:
            raise _Blocked("waiting_for_evidence")
        evidence = [content.read_evidence(actor, spec.conversation_id, spec.id, value).model_dump(mode="json")
                    for value in sorted(evidence_ids)]
        authorized = {item["evidence_link_id"]: item for item in evidence}
        dependencies = []
        for parent, output in outputs:
            ProfessionalResult.model_validate(output.content)
            if not output.evidence_manifest:
                raise _Blocked("dependency_not_authorized")
            for reference in output.evidence_manifest:
                value = reference["evidence_link_id"]
                detail = authorized.get(value)
                if detail is None:
                    detail = content.read_evidence(actor, spec.conversation_id, spec.id, UUID(value)).model_dump(mode="json")
                if _manifest([detail])[0] != reference:
                    raise _Blocked("dependency_not_authorized")
            parent_dependency_ids = sorted(
                str(value)
                for value in session.scalars(
                    select(ProfessionalDependency.parent_task_id).where(
                        ProfessionalDependency.task_id == parent.id,
                        ProfessionalDependency.run_spec_id == spec.id,
                    )
                )
            )
            dependencies.append({"task_id": str(parent.id), "output_id": str(output.id),
                                 "role": parent.role, "revision": parent.revision,
                                 "dependency_ids": parent_dependency_ids,
                                 "check_contract": _check_contract(parent.role),
                                 "content": output.content})
        payload = {"task_id": str(task.id), "role": task.role, "revision": task.revision,
                   "run_spec_id": str(spec.id), "instruction": task.instruction,
                   "frozen_scope": spec.frozen_scope, "frozen_cutoff": spec.frozen_cutoff,
                   "frozen_source_policy": spec.frozen_source_policy,
                   "dependency_ids": [str(parent.id) for parent in parents],
                   "evidence": evidence, "dependencies": dependencies}
        from app.services.company_study import professional_company_context
        company_context = professional_company_context(session, actor, spec)
        if company_context is not None:
            payload["company_study_context"] = company_context
        # Copy all ORM-owned JSON; the provider receives only an isolated snapshot.
        return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True))

    def _owns(self, session, team, task, claim) -> bool:
        return (team.status == "active" and task.status == "running"
                and task.lease_token == claim.token and task.attempt == claim.task_attempt
                and task.lease_expires_at is not None and _utc(task.lease_expires_at) > _utc(self._clock())
                and latest_tasks(session, claim.run_spec_id).get(claim.role).id == task.id)

    def _renew(self, claim: _Claim) -> bool:
        with self._transaction() as session:
            team = session.scalar(select(ResearchTeam).where(ResearchTeam.run_spec_id == claim.run_spec_id).with_for_update())
            task = session.get(ProfessionalTask, claim.task_id)
            if team is None or task is None or not self._owns(session, team, task, claim):
                return False
            task.lease_expires_at = _utc(self._clock()) + timedelta(seconds=self._lease_seconds)
            task.updated_at = _utc(self._clock())
            # Lease maintenance is not a state transition and does not churn events.
            return True

    def _finish(self, claim, result, attempts, reason) -> None:
        with self._transaction() as session:
            team = session.scalar(select(ResearchTeam).where(ResearchTeam.run_spec_id == claim.run_spec_id).with_for_update())
            task = session.get(ProfessionalTask, claim.task_id)
            if team is None or task is None:
                raise RuntimeError("professional task storage unavailable")
            for attempt in attempts:
                exists = session.scalar(select(ProfessionalAttempt.id).where(
                    ProfessionalAttempt.task_id == task.id, ProfessionalAttempt.call_id == attempt.call_id,
                    ProfessionalAttempt.attempt == attempt.attempt,
                ))
                if exists is None:
                    session.add(ProfessionalAttempt(task_id=task.id, task_attempt=claim.task_attempt,
                                                    call_id=attempt.call_id, attempt=attempt.attempt,
                                                    details=asdict(attempt)))
            session.flush()
            if not self._owns(session, team, task, claim):
                append_event(session, team, "late_result_discarded", task=task, reason="lease_lost")
                return
            if reason:
                self._set_state(session, team, task, "failed", reason)
                return
            spec = session.get(ResearchRunSpec, claim.run_spec_id)
            try:
                if spec is None:
                    raise _Blocked("input_not_authorized")
                self._native_ready(session, spec)
                payload = self._load_payload(session, spec, task, latest_tasks(session, spec.id), original=claim.payload)
                if _hash(payload) != claim.input_sha256:
                    raise _Blocked("input_authorization_changed")
            except _Blocked as exc:
                state = "cancelled" if exc.reason == "native_cancelled" else "blocked"
                self._set_state(session, team, task, state, exc.reason)
                return
            except _AUTH_ERRORS:
                self._set_state(session, team, task, "blocked", "input_authorization_changed")
                return
            # Authorization queries refresh rows; re-check lease after their work.
            if not self._owns(session, team, task, claim):
                append_event(session, team, "late_result_discarded", task=task, reason="lease_lost")
                return
            if result is None:
                self._set_state(session, team, task, "failed", "invalid_model_result")
                return
            session.add(ProfessionalOutput(task_id=task.id, run_spec_id=claim.run_spec_id,
                                           content=result.model_dump(mode="json"),
                                           evidence_manifest=_manifest(claim.payload["evidence"]),
                                           dependency_output_ids=[item["output_id"] for item in claim.payload["dependencies"]],
                                           input_sha256=claim.input_sha256))
            self._set_state(session, team, task, "succeeded", None)


class _LeaseHeartbeat:
    def __init__(self, worker: ProfessionalWorker, claim: _Claim):
        self.worker, self.claim = worker, claim
        self._stop = Event()
        self._thread: Thread | None = None
        self.lost = False

    def start(self) -> None:
        if not self.worker._renew(self.claim):
            self.lost = True
            raise LLMProviderError("professional task lease unavailable")
        self._thread = Thread(target=self._run, name="professional-task-lease", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        interval = min(15.0, self.worker._lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                if not self.worker._renew(self.claim):
                    self.lost = True
                    return
            except Exception:  # noqa: BLE001 - terminate only an explicitly configured dedicated process
                self.lost = True
                if self.worker._fatal_handler is not None:
                    self.worker._fatal_handler(1)
                return
