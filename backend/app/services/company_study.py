"""Owner-scoped continuous research, using the existing Gateway as execution authority."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.v1.tenant_context import ResearchActor
from app.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.models.company_study import (
    CompanyStudy,
    CompanyStudyActivity,
    CompanyStudyMonitor,
    CompanyStudyRequest,
    CompanyStudyRevision,
    utcnow,
)
from app.models.operational import ResearchRun
from app.models.research_gateway import ResearchConversation, ResearchRunSpec
from app.models.research_team import ResearchTeam
from app.schemas.v1.company_study import (
    ActivityCreate,
    ActivityDTO,
    LinkCreate,
    MonitorCreate,
    MonitorDTO,
    RevisionCreate,
    RevisionDTO,
    StudyCreate,
    StudyDTO,
)
from app.services.research_gateway import ResearchGateway, _bounded_text
from app.services.research_team import ResearchTeamService, fingerprint

LABELS = {
    "baseline": "建立研究基线",
    "event": "事件调查",
    "material": "资料研究",
    "refresh": "资料更新",
    "linked": "关联研究",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def next_monitor_due(now, frequency):
    local = aware(now).astimezone(SHANGHAI)
    due = local.replace(hour=20, minute=0, second=0, microsecond=0)
    if frequency == "weekly":
        due += timedelta(days=(0 - due.weekday()) % 7)
        if due <= local:
            due += timedelta(days=7)
    elif frequency == "daily":
        if due <= local:
            due += timedelta(days=1)
    else:
        raise ValidationFailedError("invalid_monitor_frequency")
    return due.astimezone(UTC)


def serialize(row, schema):
    values = {name: getattr(row, name) for name in schema.model_fields}
    for key, value in values.items():
        if isinstance(value, datetime):
            values[key] = aware(value)
    return schema.model_validate(values).model_dump(mode="json")


class CompanyStudyService:
    def __init__(self, session, *, now=utcnow):
        self.session = session
        self.now = now

    def require(self, actor, study_id, *, lock=False):
        subject = ResearchGateway._require_actor(actor)
        criteria = (
            CompanyStudy.id == study_id,
            CompanyStudy.tenant_id == actor.tenant_id,
            CompanyStudy.subject_id == subject,
        )
        if lock:
            self.session.execute(
                update(CompanyStudy)
                .where(*criteria)
                .values(updated_at=CompanyStudy.updated_at)
            )
        study = self.session.scalar(
            select(CompanyStudy)
            .where(*criteria)
            .execution_options(populate_existing=True)
        )
        if study is None:
            raise NotFoundError("company study not found")
        return study

    def _request(self, actor, scope, key, payload):
        existing = self.session.scalar(
            select(CompanyStudyRequest).where(
                CompanyStudyRequest.tenant_id == actor.tenant_id,
                CompanyStudyRequest.subject_id == actor.subject_id,
                CompanyStudyRequest.scope == scope,
                CompanyStudyRequest.key_sha256 == fingerprint(key),
            )
        )
        if existing is not None and existing.payload_sha256 != fingerprint(payload):
            raise ConflictError("company_study_idempotency_payload_conflict")
        return existing

    def _write(self, actor, study_id, operation, key, payload, callback):
        ResearchGateway._require_actor(actor)
        _bounded_text(key, name="idempotency key", limit=512)
        scope = f"{operation}:{study_id or 'collection'}"
        try:
            study = self.require(actor, study_id, lock=True) if study_id else None
            previous = self._request(actor, scope, key, payload)
            if previous is not None:
                receipt = dict(previous.receipt)
                # A replay must not restore a link whose native access was revoked.
                if receipt.get("run_spec_id"):
                    ResearchGateway(self.session).read_run_spec(
                        actor, UUID(receipt["run_spec_id"])
                    )
                self.session.commit()
                return receipt
            receipt = callback(study)
            self.session.add(
                CompanyStudyRequest(
                    tenant_id=actor.tenant_id,
                    subject_id=actor.subject_id,
                    scope=scope,
                    key_sha256=fingerprint(key),
                    payload_sha256=fingerprint(payload),
                    receipt=receipt,
                )
            )
            self.session.commit()
            return receipt
        except IntegrityError:
            self.session.rollback()
            if study_id:
                self.require(actor, study_id)
            previous = self._request(actor, scope, key, payload)
            if previous is not None:
                receipt = dict(previous.receipt)
                if receipt.get("run_spec_id"):
                    ResearchGateway(self.session).read_run_spec(
                        actor, UUID(receipt["run_spec_id"])
                    )
                return receipt
            raise ConflictError("company_study_concurrent_write") from None
        except Exception:
            self.session.rollback()
            raise

    def create(self, actor, *, idempotency_key, **data):
        data = StudyCreate.model_validate(data).model_dump()

        def apply(_):
            study = CompanyStudy(
                tenant_id=actor.tenant_id,
                subject_id=actor.subject_id,
                **data,
                created_at=self.now(),
                updated_at=self.now(),
            )
            self.session.add(study)
            self.session.flush()
            return serialize(study, StudyDTO)

        return self._write(actor, None, "create", idempotency_key, data, apply)

    def list(self, actor):
        subject = ResearchGateway._require_actor(actor)
        rows = self.session.scalars(
            select(CompanyStudy)
            .where(
                CompanyStudy.tenant_id == actor.tenant_id,
                CompanyStudy.subject_id == subject,
            )
            .order_by(CompanyStudy.updated_at.desc(), CompanyStudy.id.desc())
        )
        return {"studies": [serialize(row, StudyDTO) for row in rows]}

    def current_monitor(self, study):
        return self.session.scalar(
            select(CompanyStudyMonitor).where(
                CompanyStudyMonitor.study_id == study.id,
                CompanyStudyMonitor.version == study.monitor_version,
            )
        )

    def _activity(self, study_id, activity_id):
        row = self.session.scalar(
            select(CompanyStudyActivity).where(
                CompanyStudyActivity.id == activity_id,
                CompanyStudyActivity.study_id == study_id,
            )
        )
        if row is None:
            raise NotFoundError("company study activity not found")
        return row

    def execution_state(self, actor, activity):
        """Always derive completion from native state and currently readable outputs."""
        if activity.run_spec_id is None:
            return activity.status, activity.error_code, None
        spec = ResearchGateway(self.session).read_run_spec(actor, activity.run_spec_id)
        if spec.conversation_id != activity.conversation_id:
            raise NotFoundError("company study activity not found")
        native = self.session.get(ResearchRun, spec.native_run_id)
        view = ResearchTeamService(self.session).read(
            actor, spec.conversation_id, spec.id
        )
        latest = {}
        for task in view["tasks"]:
            if (
                task["role"] not in latest
                or task["revision"] > latest[task["role"]]["revision"]
            ):
                latest[task["role"]] = task
        if native.status in {"failed", "cancelled"}:
            return "failed", "native_research_failed", view
        if any(task["status"] in {"failed", "cancelled"} for task in latest.values()):
            return "failed", "professional_research_failed", view
        if (
            view["status"] in {"paused", "cancelled"}
            or native.status in {"waiting_for_sources", "waiting_for_review"}
            or any(task["status"] == "blocked" for task in latest.values())
        ):
            return "blocked", "research_requires_attention", view
        if (
            native.status == "succeeded"
            and len(latest) == 4
            and all(
                task["status"] == "succeeded" and task["output_state"] == "available"
                for task in latest.values()
            )
        ):
            return "completed", None, view
        if any(task["output_state"] == "withheld" for task in latest.values()):
            return "blocked", "research_outputs_unavailable", view
        return "running", None, view

    def activity_view(self, actor, row):
        status, error, _ = self.execution_state(actor, row)
        value = serialize(row, ActivityDTO)
        value.update(status=status, error_code=error)
        return value

    def read(self, actor, study_id):
        study = self.require(actor, study_id)
        rows = list(
            self.session.scalars(
                select(CompanyStudyActivity)
                .where(CompanyStudyActivity.study_id == study_id)
                .order_by(
                    CompanyStudyActivity.created_at.desc(),
                    CompanyStudyActivity.id.desc(),
                )
            )
        )
        activities = [self.activity_view(actor, row) for row in rows]
        versions = list(
            self.session.scalars(
                select(CompanyStudyRevision)
                .where(CompanyStudyRevision.study_id == study_id)
                .order_by(CompanyStudyRevision.version.desc())
            )
        )
        monitor = self.current_monitor(study)
        monitor_value = serialize(monitor, MonitorDTO) if monitor else None
        if monitor_value is not None:
            monitor_value["next_due_at"] = (
                aware(study.monitor_next_due_at).isoformat().replace("+00:00", "Z")
                if study.monitor_next_due_at
                else None
            )
        return {
            "study": serialize(study, StudyDTO),
            "activities": activities,
            "revisions": [serialize(row, RevisionDTO) for row in versions],
            "monitor": monitor_value,
        }

    def _selected_outputs(self, study, version):
        selected = self.session.scalar(
            select(CompanyStudyRevision).where(
                CompanyStudyRevision.study_id == study.id,
                CompanyStudyRevision.version == version,
            )
        )
        if selected is None:
            raise ConflictError("adopted_research_unavailable")
        actor = ResearchActor(study.tenant_id, frozenset(), study.subject_id)
        from app.services.research_team_read import read_team

        spec = ResearchGateway(self.session).read_run_spec(actor, selected.run_spec_id)
        if spec.conversation_id != selected.conversation_id:
            raise NotFoundError("adopted research not found")
        view = read_team(
            self.session,
            actor,
            selected.conversation_id,
            selected.run_spec_id,
            company_context_checked=True,
        )
        exact = {
            task["output"]["id"]: task
            for task in view["tasks"]
            if task["output_state"] == "available"
            and task["output"]
            and task["revision"] <= selected.team_revision
            and task["output"]["id"] in selected.output_ids
        }
        if len(exact) != 4 or set(exact) != set(selected.output_ids):
            raise ConflictError("adopted_research_unavailable")

        return selected, exact

    def adopted_context(self, study, version):
        if not version:
            return None
        selected, exact = self._selected_outputs(study, version)
        actor = ResearchActor(study.tenant_id, frozenset(), study.subject_id)
        authorize_company_ancestry(self.session, actor, selected.run_spec_id)

        def clip(value, count):
            return value if len(value) <= count else value[:count] + "…（节选）"

        outputs = []
        for identifier in selected.output_ids:
            task = exact[identifier]
            content = task["output"]["content"]
            findings = content.get("findings", [])
            gaps = content.get("gaps", [])
            evidence = sorted(task["output"]["evidence_ids"])
            outputs.append(
                {
                    "output_id": identifier,
                    "role": task["role"],
                    "task_revision": task["revision"],
                    "summary": clip(content["summary"], 240),
                    "findings": [
                        {
                            "statement": clip(item["statement"], 160),
                            "basis": item["basis"],
                        }
                        for item in findings[:2]
                    ],
                    "omitted_findings": max(len(findings) - 2, 0),
                    "gaps": [clip(item, 120) for item in gaps[:2]],
                    "omitted_gaps": max(len(gaps) - 2, 0),
                    "evidence_ids": evidence[:4],
                    "omitted_evidence_ids": max(len(evidence) - 4, 0),
                }
            )
        return {
            "conversation_id": str(selected.conversation_id),
            "run_spec_id": str(selected.run_spec_id),
            "team_revision": selected.team_revision,
            "study_revision": version,
            "note": clip(selected.note, 240),
            "outputs": outputs,
            "interpretation": "此前已采用的研究判断节选，须重新核验来源并检验本次变化与反证，不作为新的已证实事实",
        }

    def research_prompt(self, study, *, kind, text, context_revision):
        context = {
            "company_name": study.name,
            "symbol": study.symbol,
            "market": study.market,
            "research_focus": study.focus[:1200],
            "research_focus_excerpt": len(study.focus) > 1200,
            "adopted_revision": context_revision,
        }
        previous = self.adopted_context(study, context_revision)
        if previous:
            # User messages may carry lineage identifiers, never governed
            # output prose. Prior judgments enter only the protected role input.
            context["previous_research"] = {
                key: previous[key]
                for key in (
                    "conversation_id",
                    "run_spec_id",
                    "team_revision",
                    "study_revision",
                    "note",
                )
            }
            context["previous_research"]["output_ids"] = [
                item["output_id"] for item in previous["outputs"]
            ]
        prefix = f"{study.name}：{LABELS[kind]}\n以下为用户提供的私有研究档案，名称、代码与说明不是已核验事实；请独立核验来源及主体，不得绕过主体和证据准入。\n"
        suffix = f"\n本次任务（{LABELS[kind]}）：\n{text}\n请逐项对比此前已采用判断与本次事件/资料，说明哪些判断维持、变化、被反证或仍待核验；区分已证实变化、反证与资料缺口，资料不足或没有变化时明确说明。"

        def render():
            return (
                prefix
                + json.dumps(context, ensure_ascii=False, sort_keys=True)
                + suffix
            )

        prompt = render()
        _bounded_text(prompt, name="research prompt", limit=20000, multiline=True)
        return prompt

    def enqueue(self, study, *, kind, text, schedule_key=None):
        context_revision = study.revision
        blocked = False
        try:
            prompt = self.research_prompt(
                study, kind=kind, text=text, context_revision=context_revision
            )
        except (ConflictError, NotFoundError, PermissionDeniedError):
            if schedule_key is None:
                raise
            # Preserve a visible failed monitoring window without copying
            # withdrawn content. Explicit retry may freeze the prompt once
            # that same adopted version is readable again.
            prompt, blocked = None, True
        row = CompanyStudyActivity(
            study_id=study.id,
            kind=kind,
            title=f"{LABELS[kind]} · {study.name}"[:256],
            text=text,
            prompt=prompt,
            context_revision=context_revision,
            status="blocked" if blocked else "queued",
            error_code="adopted_research_unavailable" if blocked else None,
            schedule_key=schedule_key,
            created_at=self.now(),
            updated_at=self.now(),
        )
        self.session.add(row)
        study.updated_at = self.now()
        self.session.flush()
        return row

    def add_activity(self, actor, study_id, *, kind, text, idempotency_key):
        data = ActivityCreate(kind=kind, text=text).model_dump()
        return self._write(
            actor,
            study_id,
            "activity",
            idempotency_key,
            data,
            lambda study: serialize(self.enqueue(study, **data), ActivityDTO),
        )

    def link(self, actor, study_id, *, conversation_id, idempotency_key):
        data = LinkCreate(conversation_id=conversation_id).model_dump(mode="json")
        conversation_id = UUID(data["conversation_id"])

        def apply(study):
            gateway = ResearchGateway(self.session)
            conversation = gateway.require_read_access(actor, conversation_id)
            spec = self.session.scalar(
                select(ResearchRunSpec)
                .where(ResearchRunSpec.conversation_id == conversation_id)
                .order_by(ResearchRunSpec.created_at.desc(), ResearchRunSpec.id.desc())
                .limit(1)
            )
            if spec is None:
                raise ConflictError("linked_conversation_has_no_research")
            row = CompanyStudyActivity(
                study_id=study.id,
                kind="linked",
                title=(conversation.title or LABELS["linked"])[:256],
                text=conversation.title or LABELS["linked"],
                prompt="",
                status="running",
                conversation_id=conversation_id,
                run_spec_id=spec.id,
                created_at=self.now(),
                updated_at=self.now(),
            )
            self.session.add(row)
            study.updated_at = self.now()
            self.session.flush()
            return self.activity_view(actor, row)

        return self._write(actor, study_id, "link", idempotency_key, data, apply)

    def adopt(
        self, actor, study_id, *, activity_id, expected_revision, note, idempotency_key
    ):
        data = RevisionCreate(
            activity_id=activity_id, expected_revision=expected_revision, note=note
        ).model_dump(mode="json")

        def apply(study):
            if study.revision != expected_revision:
                raise ConflictError("company_study_revision_changed")
            activity = self._activity(study.id, UUID(data["activity_id"]))
            if activity.run_spec_id is None:
                raise ConflictError("adoption_requires_completed_research")
            # Same order as team commands: conversation then team. Locks bind
            # adoption to one exact current set while successors are excluded.
            self.session.execute(
                update(ResearchConversation)
                .where(ResearchConversation.id == activity.conversation_id)
                .values(updated_at=ResearchConversation.updated_at)
            )
            self.session.execute(
                update(ResearchTeam)
                .where(ResearchTeam.run_spec_id == activity.run_spec_id)
                .values(updated_at=ResearchTeam.updated_at)
            )
            state, _, view = self.execution_state(actor, activity)
            if state != "completed":
                raise ConflictError("adoption_requires_completed_authorized_outputs")
            latest = {}
            for task in view["tasks"]:
                if (
                    task["role"] not in latest
                    or task["revision"] > latest[task["role"]]["revision"]
                ):
                    latest[task["role"]] = task
            version = CompanyStudyRevision(
                study_id=study.id,
                version=expected_revision + 1,
                activity_id=activity.id,
                conversation_id=activity.conversation_id,
                run_spec_id=activity.run_spec_id,
                team_revision=view["revision"],
                output_ids=[latest[role]["output"]["id"] for role in sorted(latest)],
                note=data["note"],
                created_at=self.now(),
            )
            changed = self.session.execute(
                update(CompanyStudy)
                .where(
                    CompanyStudy.id == study.id,
                    CompanyStudy.revision == expected_revision,
                )
                .values(revision=expected_revision + 1, updated_at=self.now())
            )
            if changed.rowcount != 1:
                raise ConflictError("company_study_revision_changed")
            self.session.add(version)
            self.session.flush()
            return serialize(version, RevisionDTO)

        return self._write(actor, study_id, "adopt", idempotency_key, data, apply)

    def configure_monitor(
        self, actor, study_id, *, status, frequency, focus, idempotency_key
    ):
        data = MonitorCreate(
            status=status, frequency=frequency, focus=focus
        ).model_dump()

        def apply(study):
            due = (
                next_monitor_due(self.now(), frequency) if status == "active" else None
            )
            monitor = CompanyStudyMonitor(
                study_id=study.id,
                version=study.monitor_version + 1,
                **data,
                next_due_at=due,
                created_at=self.now(),
            )
            self.session.add(monitor)
            study.monitor_version += 1
            study.monitor_next_due_at = due
            study.updated_at = self.now()
            self.session.flush()
            return serialize(monitor, MonitorDTO)

        return self._write(actor, study_id, "monitor", idempotency_key, data, apply)

    def retry(self, actor, study_id, activity_id, *, idempotency_key):
        def apply(study):
            activity = self._activity(study.id, activity_id)
            state, _, _ = self.execution_state(actor, activity)
            if activity.run_spec_id is not None:
                raise ConflictError("retry_existing_research_in_its_workbench")
            if state not in {"failed", "blocked"}:
                raise ConflictError("activity_is_not_retryable")
            activity.status, activity.error_code = "queued", None
            activity.attempt = 0
            activity.lease_token, activity.lease_expires_at = None, None
            activity.updated_at = study.updated_at = self.now()
            self.session.flush()
            return serialize(activity, ActivityDTO)

        return self._write(
            actor, study_id, f"retry/{activity_id}", idempotency_key, {}, apply
        )


def generated_company_activity(session, actor, spec):
    """Resolve immutable Gateway origin even before the worker saves its receipt.

    The Gateway request, native run and spec commit together. An activity's
    operational run/conv fields may lag after a crash; they are not the origin.
    """
    from app.models.research_gateway import GatewayIdempotencyRequest
    from app.services.research_gateway import _fingerprint

    ResearchGateway._require_actor(actor)
    if spec.tenant_id != actor.tenant_id or spec.subject_id != actor.subject_id:
        raise NotFoundError("company research not found")
    request = (
        session.get(GatewayIdempotencyRequest, spec.gateway_request_id)
        if spec.gateway_request_id
        else None
    )
    prefix = "company-study-activity:"
    if request is None or not request.client_key.startswith(prefix):
        return None
    if (
        request.operation != "send_message"
        or request.tenant_id != actor.tenant_id
        or request.subject_id != actor.subject_id
    ):
        raise NotFoundError("company research not found")
    try:
        activity_id = UUID(request.client_key[len(prefix) :])
    except ValueError:
        raise ConflictError("company_context_provenance_conflict") from None
    activity = session.scalar(
        select(CompanyStudyActivity)
        .join(CompanyStudy)
        .where(
            CompanyStudyActivity.id == activity_id,
            CompanyStudyActivity.kind != "linked",
            CompanyStudy.tenant_id == actor.tenant_id,
            CompanyStudy.subject_id == actor.subject_id,
        )
    )
    if (
        activity is None
        or activity.prompt is None
        or activity.run_spec_id not in (None, spec.id)
        or activity.conversation_id not in (None, spec.conversation_id)
        or request.request_fingerprint
        != _fingerprint({"conversation_id": None, "text": activity.prompt})
    ):
        raise ConflictError("company_context_provenance_conflict")
    return activity


def authorize_company_ancestry(session, actor, run_spec_id):
    """Iterate exact adopted links; no recursive reads and no unbounded chains."""
    seen = set()
    service = CompanyStudyService(session)
    for _ in range(32):
        if run_spec_id in seen:
            raise ConflictError("company_context_cycle")
        seen.add(run_spec_id)
        spec = ResearchGateway(session).read_run_spec(actor, run_spec_id)
        activity = generated_company_activity(session, actor, spec)
        if activity is None or not activity.context_revision:
            return
        study = service.require(actor, activity.study_id)
        selected, _ = service._selected_outputs(study, activity.context_revision)
        run_spec_id = selected.run_spec_id
    raise ConflictError("company_context_history_limit")


def professional_company_context(session, actor, spec):
    activity = generated_company_activity(session, actor, spec)
    if activity is None:
        return None
    if activity.prompt is None:
        raise ConflictError("company_context_not_frozen")
    service = CompanyStudyService(session)
    study = service.require(actor, activity.study_id)
    prior = service.adopted_context(study, activity.context_revision)
    return {
        "study_id": str(study.id),
        "activity_id": str(activity.id),
        "kind": activity.kind,
        "context_revision": activity.context_revision,
        "user_research_request": activity.prompt,
        "adopted_context": prior,
        "usage": "此前判断仅作需要重新核验的背景，不得作为本轮已准入证据，不得引用其 evidence_ids 充当本轮依据；本轮引用仅可来自 evidence 数组。逐项检验判断维持、变化、反证与缺口。",
    }
