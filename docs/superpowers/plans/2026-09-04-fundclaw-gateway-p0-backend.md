# FundClaw Gateway P0 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a secure backend vertical slice in which a subject-authenticated researcher sends one message, receives one durable private conversation, immutable run receipt, four fixed role states, and replayable safe SSE events while reusing the existing automatic-research engine.

**Architecture:** Add ResearchGateway as a deep module, rather than turning jobs, activity, or OpenClaw sessions into the product authority. It owns private conversation authorization, Gateway-scoped idempotency, immutable intent/run records, fixed capability manifests, and safe event projection. A native adapter invokes the existing automatic-research intake and projects its durable run/task/evidence state into four role cards; the evidence ledger, acquisition pipeline, and workers remain authoritative.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL/SQLite test matrix, Server-Sent Events, pytest.

---

## Scope boundary

This is the first backend delivery slice, not a disguised full rewrite.

- It ships: private web conversations; stable subject identity; send-to-start; immutable run receipts; four fixed role rows; native automatic-research integration; safe projection; authenticated SSE replay; idempotency; and closure of the directly relevant unauthenticated operational bypasses.
- It deliberately does not ship: the frontend Gateway page, company-research routing, external OpenClaw runtime, arbitrary tools/plugins, free-form agent memory, model-token streaming, announcement monitoring, or shared conversations.
- It reserves but safely rejects unsupported command kinds such as pause/resume and grounded Q&A. The rejection is an auditable Gateway command/event, not a silent no-op. A later control-and-grounded-Q&A plan can add native support without changing the public Gateway seam.

## File structure

| Path | Responsibility |
| --- | --- |
| backend/app/models/research_gateway.py | Gateway persistence: private threads, append-only messages/intents/specs/events/commands, mutable role rows and idempotency leases. |
| backend/app/repositories/research_gateway.py | Locks, scoped idempotency, per-thread sequence allocation, append operations, and replay queries. |
| backend/app/services/research_gateway_policy.py | Versioned four-role manifest and the allowlist of P0 command/capability kinds. |
| backend/app/services/research_gateway.py | The single deep ResearchGateway module used by routes and tests. |
| backend/app/services/research_gateway_automatic_adapter.py | Adapter from a Gateway start/successor intent to existing automatic intake and from native rows to role events. |
| backend/app/services/research_gateway_projection.py | Safe DTO projection, bounded redaction, artifact references, and native-event deduplication. |
| backend/app/services/research_gateway_stream.py | Snapshot/replay result, SSE frame serialization, cursor validation, and finite polling iterator. |
| backend/app/schemas/v1/research_gateway.py | Strict wire contracts; no raw untyped trace payload. |
| backend/app/api/v1/research_gateway.py | Authenticated HTTP and SSE routes only; no ORM writes in route functions. |
| backend/alembic/versions/0072_research_gateway_p0.py | Explicit portable schema, indexes, immutable-table triggers, and downgrade. |
| backend/tests/test_research_gateway_*.py | Focused service, API, adapter, stream, security, and migration contracts. |

Existing modules retained as owned seams:

- backend/app/services/automatic_research_intake.py remains the native text-to-Case/Run entry adapter.
- backend/app/services/event_research.py remains the atomic Case/document/admission/scope/run creator.
- backend/app/services/auto_research.py, backend/app/services/automatic_research_pipeline.py, and backend/app/scripts/run_research_worker.py remain the durable execution path.
- backend/app/services/case_tenant_access.py remains the Case tenant root. Private conversation ownership is additive and must not reinterpret tenant admission as a user identity.

## Public P0 contract

~~~text
POST /api/v1/research-conversations
POST /api/v1/research-conversations/{conversation_id}/messages
GET  /api/v1/research-conversations
GET  /api/v1/research-conversations/{conversation_id}
GET  /api/v1/research-conversations/{conversation_id}/snapshot
GET  /api/v1/research-conversations/{conversation_id}/events
POST /api/v1/research-conversations/{conversation_id}/runs/{run_spec_id}/commands
~~~

The message endpoint requires an Idempotency-Key header. The response is a receipt, never a speculative UI state:

~~~json
{
  "conversation_id": "uuid",
  "intent_id": "uuid",
  "run_spec_id": "uuid",
  "native_case_id": "uuid",
  "native_run_id": "uuid",
  "status": "queued",
  "latest_sequence": 4
}
~~~

The only safe role keys are scope_identity, sources_evidence, analysis_counter_evidence, and compilation_checks. A role event may contain a bounded display message, reason code, status, and typed artifact reference; it may not carry a prompt, chain-of-thought, raw tool arguments, provider error, source body, or arbitrary JSON payload.

### Task 1: Establish trusted subject identity and an atomic native-intake seam

**Files:**

- Modify: backend/app/api/v1/tenant_context.py
- Modify: backend/app/api/v1/research_session.py
- Modify: backend/app/api/v1/automatic_research.py
- Modify: backend/app/services/automatic_research_intake.py
- Modify: backend/app/services/event_research.py
- Modify: backend/tests/test_event_case_tenant_access.py
- Modify: backend/tests/test_automatic_research_api.py
- Create: backend/tests/test_research_gateway_identity.py

- [ ] **Step 1: Write failing identity and transaction-seam tests.**

~~~python
def test_gateway_actor_requires_host_configured_subject(monkeypatch) -> None:
    from app.api.v1.tenant_context import require_gateway_actor
    from app.errors import PermissionDeniedError

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"legacy":"team-a"}')
    with pytest.raises(PermissionDeniedError):
        require_gateway_actor("Bearer legacy")

    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"alice":{"tenant_id":"team-a","subject_id":"alice","roles":[]}}',
    )
    actor = require_gateway_actor("Bearer alice")
    assert (actor.tenant_id, actor.subject_id) == ("team-a", "alice")


def test_native_intake_can_flush_without_committing(
    cmd_session, monkeypatch
) -> None:
    intake = AutomaticResearchIntakeService(cmd_session, extractor=_FakeExtractor())
    started = intake.start(
        "测试主题", tenant_id="team-a", actor_subject_id="alice", commit=False
    )
    assert started.case_id
    assert cmd_session.in_transaction()
    cmd_session.rollback()
    assert cmd_session.get(ResearchCase, uuid.UUID(started.case_id)) is None
~~~

- [ ] **Step 2: Run the focused tests and confirm they fail for the expected missing subject and commit arguments.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_identity.py
~~~

Expected: FAIL because require_gateway_actor and the new intake arguments do not exist yet.

- [ ] **Step 3: Extend the host-owned actor configuration without trusting client input.**

In backend/app/api/v1/tenant_context.py, make subject_id optional on ResearchActor for legacy compatibility, parse it only from a dict token configuration, and add a Gateway-specific dependency:

~~~python
@dataclass(frozen=True)
class ResearchActor:
    tenant_id: str
    roles: frozenset[str]
    subject_id: str | None = None


def require_gateway_actor(
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchActor:
    if not actor.subject_id:
        raise PermissionDeniedError(
            "a stable research subject is required for Gateway access"
        )
    return actor
~~~

Validate subject_id as a nonblank string of at most 128 characters in _configured_tokens. Preserve string token-to-tenant entries for existing non-Gateway routes; never derive a subject from request JSON, query string, header other than the bearer token, or a client-provided actor field.

- [ ] **Step 4: Make the automatic intake callable inside a caller-owned transaction.**

Change AutomaticResearchIntakeService.start to accept actor_subject_id and commit:

~~~python
def start(
    self,
    raw_input: str,
    *,
    tenant_id: str,
    actor_subject_id: str,
    commit: bool = True,
) -> AutomaticResearchStart:
    ...
    created = EventResearchService(self._session).create(
        CreateEventResearchRequest(
            ...,
            created_by=f"human:{actor_subject_id}",
        ),
        tenant_id=normalized_tenant_id,
        workflow_mode="automatic",
        commit=commit,
    )
~~~

Refactor EventResearchService.create with a commit keyword defaulting to True. When commit is False, flush all Case/document/admission/scope/run rows but do not commit or rollback the caller transaction. Keep the current rollback behavior only when create owns the transaction:

~~~python
try:
    ...
    if commit:
        self._session.commit()
    else:
        self._session.flush()
except Exception:
    if commit:
        self._session.rollback()
    raise
~~~

The Gateway will execute native intake, its own immutable records, and the idempotency receipt in one transaction. Do not use the existing global IdempotencyKey table for this.

- [ ] **Step 5: Keep the old automatic endpoint wire-compatible while correcting its audit actor.**

Use require_research_actor in backend/app/api/v1/automatic_research.py. Pass actor.subject_id when configured; for the old tenant-only token format, explicitly preserve the historical fallback label tenant:{tenant_id}. The request body remains exactly input-only and must not gain actor, tenant, role, model, or tool fields.

Extend ResearchSessionDTO with subject_id: str | None so the browser can identify its own authenticated subject, not another user's identity.

- [ ] **Step 6: Run identity and automatic-entry regression tests.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_identity.py tests/test_event_case_tenant_access.py tests/test_automatic_research_api.py
~~~

Expected: PASS. Confirm that legacy tenant-only automatic research still has its old request/response wire shape and that a Gateway-only dependency rejects a token without subject_id.

- [ ] **Step 7: Commit the identity seam.**

~~~bash
git add backend/app/api/v1/tenant_context.py backend/app/api/v1/research_session.py backend/app/api/v1/automatic_research.py backend/app/services/automatic_research_intake.py backend/app/services/event_research.py backend/tests/test_research_gateway_identity.py backend/tests/test_event_case_tenant_access.py backend/tests/test_automatic_research_api.py
git commit -m "feat: add trusted Gateway subject identity"
~~~

### Task 2: Create the isolated Gateway persistence model and scoped idempotency store

**Files:**

- Create: backend/app/models/research_gateway.py
- Modify: backend/app/models/__init__.py
- Modify: backend/app/models/ledger.py
- Create: backend/alembic/versions/0072_research_gateway_p0.py
- Create: backend/app/repositories/research_gateway.py
- Create: backend/tests/test_research_gateway_repository.py
- Create: backend/tests/test_research_gateway_migration.py

- [ ] **Step 1: Write failing model/repository tests before adding tables.**

~~~python
def test_gateway_request_key_is_scoped_by_tenant_subject_and_operation(session) -> None:
    repo = ResearchGatewayRepository(session)
    first, acquired = repo.acquire_request(
        tenant_id="team-a",
        subject_id="alice",
        operation="send_message",
        client_key="same-key",
        request_fingerprint="a" * 64,
    )
    assert acquired is True
    other, other_acquired = repo.acquire_request(
        tenant_id="team-b",
        subject_id="bob",
        operation="send_message",
        client_key="same-key",
        request_fingerprint="b" * 64,
    )
    assert other_acquired is True
    assert other.id != first.id


def test_gateway_role_event_sequence_is_conversation_ordered_and_source_deduplicated(
    session, gateway_run_spec
) -> None:
    repo = ResearchGatewayRepository(session)
    first = repo.append_role_event(
        run_spec_id=gateway_run_spec.id,
        role_key="sources_evidence",
        event_type="role_started",
        source_key="native:research_run_event:1",
        display_text="正在请求已批准来源",
    )
    duplicate = repo.append_role_event(
        run_spec_id=gateway_run_spec.id,
        role_key="sources_evidence",
        event_type="role_started",
        source_key="native:research_run_event:1",
        display_text="must not create a second event",
    )
    assert first.sequence == 1
    assert duplicate.id == first.id
~~~

- [ ] **Step 2: Run the new tests to prove model imports/tables are absent.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_repository.py
~~~

Expected: FAIL with ImportError or missing-table/model errors.

- [ ] **Step 3: Add a dedicated Gateway model module; do not enlarge operational.py.**

Create backend/app/models/research_gateway.py with these rows and constraints:

~~~python
class ResearchConversation(Base):
    __tablename__ = "research_conversations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    title: Mapped[str | None] = mapped_column(String(256))
    event_retention_floor: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GatewayIdempotencyRequest(Base):
    __tablename__ = "gateway_idempotency_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_id", "operation", "client_key",
            name="uq_gateway_idempotency_scope",
        ),
    )
    ...
~~~

Add ResearchMessage, ResearchIntent, ResearchRunSpec, RoleRun, RoleEvent, and GatewayCommand in the same module. Use UUID foreign keys and explicit indexes rather than polymorphic unscoped references. Required invariants:

1. Every thread/message/intent/spec/command includes tenant_id and subject provenance.
2. A message has a per-conversation sequence and a SHA-256 input hash, not a raw prompt or model context pack.
3. A run spec is immutable and contains the native Case/ResearchRun IDs, frozen scope/cutoff/source-policy JSON, role-manifest version, capability-manifest version, correlation ID, and input-artifact IDs.
4. RoleRun is mutable operational state; RoleEvent, message, intent, run spec, and command are append-only.
5. RoleEvent has both conversation sequence and run sequence, plus a unique source_key scoped to the run spec. This lets the same projector run after worker restart without duplicate UI events.
6. GatewayCommand stores actor_subject_id, command kind, target hash/version, outcome, and request ID. It stores no raw client payload.

Use CHECK constraints for private visibility, the four role keys, and known state/kind values. Artifact references are a typed JSON list of IDs and kinds, never copied source text.

- [ ] **Step 4: Register append-only Gateway rows with both application and database guards.**

Import research_gateway in backend/app/models/__init__.py. Add these table names to IMMUTABLE_TABLES in backend/app/models/ledger.py:

~~~python
"research_messages",
"research_intents",
"research_run_specs",
"role_events",
"gateway_commands",
~~~

Do not add research_conversations, role_runs, or gateway_idempotency_requests: their lifecycle/status/lease fields are intentionally mutable operational state.

- [ ] **Step 5: Write explicit Alembic revision 0072_research_gateway_p0.py.**

Set down_revision to 0071 after verifying the actual head at implementation time. Create eight tables and the following constraints/indexes:

~~~python
op.create_index(
    "ix_research_conversations_owner_recent",
    "research_conversations",
    ["tenant_id", "owner_subject_id", "updated_at"],
)
op.create_unique_constraint(
    "uq_role_events_conversation_sequence",
    "role_events",
    ["conversation_id", "sequence"],
)
op.create_unique_constraint(
    "uq_role_events_run_sequence",
    "role_events",
    ["run_spec_id", "run_sequence"],
)
op.create_unique_constraint(
    "uq_role_events_native_source",
    "role_events",
    ["run_spec_id", "source_key"],
)
~~~

Install update/delete rejection triggers for every append-only table on SQLite and PostgreSQL, following the dialect-specific style already used in migrations 0069 and 0070. The downgrade must drop triggers/indexes/tables in foreign-key-safe reverse order. Do not autogenerate the revision.

- [ ] **Step 6: Implement one repository interface that owns locks, sequence allocation, and request recovery.**

The public repository operations should be small:

~~~python
class ResearchGatewayRepository:
    def acquire_request(...) -> tuple[GatewayIdempotencyRequest, bool]: ...
    def recover_or_return_receipt(...) -> GatewayReceipt | None: ...
    def create_conversation(...): ...
    def append_message(...): ...
    def append_intent(...): ...
    def create_run_spec(...): ...
    def initialize_role_runs(...): ...
    def append_role_event(...): ...
    def append_command(...): ...
    def events_after(...): ...
~~~

Acquire the Gateway request in a short committed lease transaction before a provider call. Then write the native run, immutable Gateway rows, and completed receipt together. On a retry, return the stored receipt when complete; if a lease is in-progress but its linked run spec exists, reconstruct/complete the receipt; otherwise only an expired lease can be taken over. Same key plus different fingerprint is a 409 conflict. Never adapt IdempotencyKey because it is globally keyed.

- [ ] **Step 7: Run repository, immutability, and schema tests.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_repository.py tests/test_research_gateway_migration.py
~~~

Expected: PASS on SQLite. For the PostgreSQL-only trigger contract, run the focused test under TEST_DATABASE_URL and assert UPDATE and DELETE fail for every immutable Gateway table.

- [ ] **Step 8: Commit the persistence layer.**

~~~bash
git add backend/app/models/research_gateway.py backend/app/models/__init__.py backend/app/models/ledger.py backend/app/repositories/research_gateway.py backend/alembic/versions/0072_research_gateway_p0.py backend/tests/test_research_gateway_repository.py backend/tests/test_research_gateway_migration.py
git commit -m "feat: persist FundClaw Gateway records"
~~~

### Task 3: Define fixed policy and implement the deep ResearchGateway module

**Files:**

- Create: backend/app/services/research_gateway_policy.py
- Create: backend/app/services/research_gateway.py
- Create: backend/tests/test_research_gateway_service.py

- [ ] **Step 1: Write failing service tests for the end-to-end state transition.**

~~~python
def test_send_message_creates_one_immutable_intent_spec_and_four_roles(
    session, fake_native_runtime, gateway_actor
) -> None:
    gateway = ResearchGateway(session, runtime=fake_native_runtime)
    receipt = gateway.send_message(
        actor=gateway_actor,
        conversation_id=None,
        text="研究光模块行业需求变化",
        idempotency_key="send-1",
    )
    assert receipt.status == "queued"
    assert receipt.native_case_id == fake_native_runtime.case_id
    assert [row.role_key for row in receipt.role_runs] == [
        "scope_identity",
        "sources_evidence",
        "analysis_counter_evidence",
        "compilation_checks",
    ]
    assert receipt.run_spec_id


def test_scope_change_creates_a_successor_without_mutating_prior_spec(
    session, fake_native_runtime, gateway_actor, started_gateway_run
) -> None:
    gateway = ResearchGateway(session, runtime=fake_native_runtime)
    successor = gateway.send_message(
        actor=gateway_actor,
        conversation_id=started_gateway_run.conversation_id,
        text="只分析海外收入影响",
        idempotency_key="scope-2",
    )
    assert successor.parent_intent_id == started_gateway_run.intent_id
    assert successor.run_spec_id != started_gateway_run.run_spec_id
    assert (
        gateway.read_run_spec(started_gateway_run.run_spec_id).scope
        == started_gateway_run.frozen_scope
    )
~~~

- [ ] **Step 2: Run the service test to confirm the interface has not been implemented.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_service.py
~~~

Expected: FAIL because ResearchGateway and policy manifest are absent.

- [ ] **Step 3: Freeze a narrow server-owned four-role capability manifest.**

In backend/app/services/research_gateway_policy.py define immutable RoleDefinition values:

~~~python
ROLE_MANIFEST_VERSION = "fundclaw-roles.v1"
CAPABILITY_MANIFEST_VERSION = "fundclaw-capabilities.v1"

ROLE_DEFINITIONS = (
    RoleDefinition("scope_identity", ("resolve_scope",), ("identity_decision", "blocked")),
    RoleDefinition("sources_evidence", ("request_acquisition", "read_frozen_evidence"), ("artifact_ref", "rejected_source")),
    RoleDefinition("analysis_counter_evidence", ("propose_candidate_claim", "propose_counter_evidence_task"), ("candidate", "gap")),
    RoleDefinition("compilation_checks", ("compile_provisional_draft", "validate_lineage"), ("draft_ref", "validation")),
)
~~~

Expose only helpers such as require_role, supported_command, and frozen_manifest. Do not accept a role, tool, model, capability, plugin, URL, browser setting, or prompt from a user message.

- [ ] **Step 4: Implement the small ResearchGateway interface and hide complexity behind it.**

The module interface is:

~~~python
class ResearchGateway:
    def create_conversation(self, *, actor: ResearchActor, title: str | None) -> ConversationRef: ...
    def send_message(self, *, actor: ResearchActor, conversation_id: UUID | None,
                     text: str, idempotency_key: str) -> GatewayReceipt: ...
    def read_conversation(self, *, actor: ResearchActor, conversation_id: UUID,
                          after_sequence: int = 0) -> ConversationProjection: ...
    def issue_command(self, *, actor: ResearchActor, conversation_id: UUID,
                      run_spec_id: UUID, command: GatewayCommandInput,
                      idempotency_key: str) -> CommandReceipt: ...
~~~

Inside send_message:

1. Resolve the private thread by tenant_id and owner_subject_id, returning 404 for another subject or tenant.
2. Acquire a tenant/subject/operation/key-scoped idempotency lease.
3. Append the user message and classified intent. Start and scope_change are allowed P0 intents; a scope change is linked to the previous intent and makes a new native Case/Run rather than mutating historical scope.
4. Call the injected native runtime only for a supported start/successor. It returns native Case/Run IDs and frozen values.
5. Create immutable RunSpec, initialize exactly four RoleRuns, append initial safe role events, complete the request receipt, and commit once.
6. For unsupported intent/command types, append a rejected intent or command and a safe event with reason_code unsupported_in_gateway_p0. Do not invoke an AI tool or alter a native run.

Add a private _require_conversation method that checks owner subject before every message/read/command. Case tenant access is checked before using a native Case/artifact reference. A user visible message must be bounded (for example 20,000 chars), stored as message content only, and never copied into a ContextPack field.

- [ ] **Step 5: Add deterministic command semantics.**

P0 permits cancel, retry, request_counter_evidence, and scope_change only when their native adapter can safely perform the operation. Each stores:

~~~python
GatewayCommand(
    actor_subject_id=actor.subject_id,
    command_kind=command.kind,
    target_hash=sha256(canonical_command_target),
    outcome="accepted" | "rejected" | "completed",
    gateway_request_id=request.id,
)
~~~

Cancel and retry call service-level operations after Gateway authorization, never the HTTP jobs endpoint. Counter-evidence and scope-change create successor intents/runs. Pause, resume, and grounded_question must return a persisted rejected receipt in P0, so the UI never implies that a native worker was paused or that a model answer is evidence-backed when it is not.

- [ ] **Step 6: Run the service tests.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_service.py
~~~

Expected: PASS, including same-key replay, key/body conflict, cross-subject privacy, successor immutability, and unsupported-command no-side-effect checks.

- [ ] **Step 7: Commit the deep module.**

~~~bash
git add backend/app/services/research_gateway_policy.py backend/app/services/research_gateway.py backend/tests/test_research_gateway_service.py
git commit -m "feat: add FundClaw research Gateway control plane"
~~~

### Task 4: Add the native automatic-research adapter and durable four-role projection

**Files:**

- Create: backend/app/services/research_gateway_automatic_adapter.py
- Create: backend/app/services/research_gateway_projection.py
- Modify: backend/app/scripts/run_research_worker.py
- Create: backend/tests/test_research_gateway_automatic_adapter.py
- Modify: backend/tests/test_research_worker_entrypoint.py

- [ ] **Step 1: Write the failing adapter/projection test against a real persisted automatic run.**

~~~python
def test_native_adapter_projects_each_fixed_role_without_source_body(
    cmd_session, gateway_run_spec, completed_automatic_run
) -> None:
    projector = GatewayAutomaticResearchAdapter(cmd_session)
    projector.sync(run_spec_id=gateway_run_spec.id)

    events = ResearchGatewayRepository(cmd_session).events_after(
        conversation_id=gateway_run_spec.conversation_id, after_sequence=0
    )
    assert {event.role_key for event in events} == {
        "scope_identity",
        "sources_evidence",
        "analysis_counter_evidence",
        "compilation_checks",
    }
    assert any(ref.kind == "evidence_link" for event in events for ref in event.artifact_refs)
    assert all("Revenue was" not in event.display_text for event in events)
~~~

- [ ] **Step 2: Run it and confirm the adapter is missing.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_automatic_adapter.py
~~~

Expected: FAIL with ImportError.

- [ ] **Step 3: Define the Adapter seam; use existing automatic intake rather than a second engine.**

Implement a small native runtime interface in backend/app/services/research_gateway_automatic_adapter.py:

~~~python
class NativeResearchRuntime(Protocol):
    def start(
        self, *, text: str, tenant_id: str, actor_subject_id: str, commit: bool
    ) -> NativeRunRef: ...


class AutomaticResearchRuntime:
    def start(...):
        started = AutomaticResearchIntakeService(self._session).start(
            text,
            tenant_id=tenant_id,
            actor_subject_id=actor_subject_id,
            commit=commit,
        )
        return NativeRunRef(
            case_id=UUID(started.case_id),
            research_run_id=UUID(started.run_id),
        )
~~~

The runtime receives no tool list, agent prompt, arbitrary source URL, plugin, or client-provided policy. Gateway freezes policy/version metadata into RunSpec; automatic intake continues to enforce source admission, acquisition, Case tenant admission, and existing ledger gates.

- [ ] **Step 4: Project native facts through one idempotent projector.**

Implement GatewayAutomaticResearchAdapter.sync(run_spec_id). It reads only:

- the native ResearchRun and its scope event;
- ResearchRunEvent rows;
- ResearchTask lifecycle fields;
- permitted EvidenceLink/SourceSpan identifiers after CaseTenantAccess; and
- terminal safe categories from acquisition/run state.

Map them as follows:

| Native fact | Safe role event |
| --- | --- |
| frozen scope/start receipt | scope_identity accepted/completed |
| retrieval/acquisition state | sources_evidence started, waiting, evidence_available, or blocked |
| support/contradict/alternative tasks | analysis_counter_evidence active, gap, or completed |
| result task/conclusion/terminal validation | compilation_checks active, completed, or failed |

Every projection carries source_key such as native:research_run_event:{event_id} or native:research_task:{task_id}:{updated_at}. Append through the repository's unique source-key operation. This is the sole deduplication writer; repeat sync calls must be harmless.

Use typed artifact references:

~~~python
ArtifactReference(
    kind="evidence_link",
    id=str(link.id),
    case_id=str(run.research_case_id),
    locator_available=True,
)
~~~

Never include SourceSpan.verbatim_text, DocumentVersion bytes, message body, ResearchRunEvent.payload_json, JobEvent.message, stack text, headers, secrets, prompts, or a provider response.

- [ ] **Step 5: Make projection recovery independent of an active viewer.**

After the native worker commits each claimed run transition, open a short new session and invoke sync for linked Gateway specs:

~~~python
with SessionLocal() as projection_session:
    GatewayAutomaticResearchAdapter(projection_session).sync_native_run(run.id)
    projection_session.commit()
~~~

Use the same sync method immediately before snapshot/replay reads as a catch-up path for runs advanced outside the normal worker entrypoint. Do not add custom Gateway event writing into automatic_research_pipeline.py or auto_research.py. The adapter source-key constraint, not a best-effort boolean, guarantees restart safety.

- [ ] **Step 6: Run automatic-research and worker regressions.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_automatic_adapter.py tests/test_automatic_research_pipeline.py tests/test_automatic_research_api.py tests/test_research_worker_entrypoint.py
~~~

Expected: PASS. Run the adapter sync twice and after a simulated worker restart; event count and native run count must remain unchanged on the second invocation.

- [ ] **Step 7: Commit the adapter integration.**

~~~bash
git add backend/app/services/research_gateway_automatic_adapter.py backend/app/services/research_gateway_projection.py backend/app/scripts/run_research_worker.py backend/tests/test_research_gateway_automatic_adapter.py backend/tests/test_research_worker_entrypoint.py
git commit -m "feat: project automatic research into Gateway roles"
~~~

### Task 5: Build the safe snapshot, replay, and authenticated SSE transport

**Files:**

- Create: backend/app/services/research_gateway_stream.py
- Create: backend/tests/test_research_gateway_stream.py

- [ ] **Step 1: Write failing replay, redaction, and SSE-frame tests.**

~~~python
def test_replay_honors_after_sequence_and_last_event_id(gateway_stream) -> None:
    replay = gateway_stream.replay(conversation_id=CONVERSATION, after_sequence=2)
    assert [event.sequence for event in replay.events] == [3, 4]
    assert replay.snapshot_required is False


def test_retention_gap_requires_snapshot_not_silent_event_loss(gateway_stream) -> None:
    gateway_stream.set_retention_floor(CONVERSATION, 7)
    replay = gateway_stream.replay(conversation_id=CONVERSATION, after_sequence=2)
    assert replay.snapshot_required is True
    assert replay.snapshot.latest_sequence >= 7


def test_safe_projection_removes_prompt_secret_tool_arguments_and_traceback() -> None:
    event = project_native_failure(
        {"prompt": "hidden", "authorization": "Bearer secret", "error": "Traceback"}
    )
    wire = event.model_dump_json()
    assert "hidden" not in wire
    assert "secret" not in wire
    assert "Traceback" not in wire
    assert event.reason_code == "native_execution_failed"
~~~

- [ ] **Step 2: Run the stream tests and confirm the transport has not been implemented.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_stream.py
~~~

Expected: FAIL with ImportError.

- [ ] **Step 3: Implement a strict safe projection DTO.**

Create a projection function that accepts only Gateway-owned event fields and typed artifact identifiers:

~~~python
def to_safe_role_event(event: RoleEvent) -> SafeRoleEventDTO:
    return SafeRoleEventDTO(
        sequence=event.sequence,
        role=RoleKey(event.role_key),
        type=SafeEventType(event.event_type),
        status=RoleStatus(event.status),
        summary=event.display_text,
        reason_code=event.reason_code,
        artifacts=[SafeArtifactRef.model_validate(ref) for ref in event.artifact_refs],
        occurred_at=event.occurred_at,
    )
~~~

SafeArtifactRef must use a discriminator and only allow case, evidence_link, document_version, research_run, and conclusion identifiers. Unknown fields, URLs with embedded credentials, source text, raw exception strings, or a generic details dictionary must be validation errors, not silently forwarded.

- [ ] **Step 4: Implement deterministic replay and snapshots.**

In research_gateway_stream.py, define:

~~~python
class ReplayResult(NamedTuple):
    events: tuple[SafeRoleEventDTO, ...]
    latest_sequence: int
    snapshot_required: bool
    snapshot: ConversationSnapshotDTO | None
~~~

Cursor rules:

1. after_sequence must be a non-negative integer.
2. If both after_sequence query parameter and Last-Event-ID header are supplied, they must match or return 422.
3. A cursor below conversation.event_retention_floor - 1 returns snapshot_required with a tenant-safe snapshot, never a partial misleading replay.
4. Events are strictly ordered by conversation sequence and duplicate source-key events never appear twice.

- [ ] **Step 5: Add an SSE frame iterator with short-lived database sessions.**

Use StreamingResponse later at the route layer, but keep the generator independently testable:

~~~python
def sse_frames(
    *,
    session_factory: Callable[[], Session],
    actor: ResearchActor,
    conversation_id: UUID,
    after_sequence: int,
    poll_seconds: float = 1.0,
) -> Iterator[bytes]:
    while True:
        with session_factory() as session:
            projection = ResearchGateway(session).read_conversation(
                actor=actor,
                conversation_id=conversation_id,
                after_sequence=after_sequence,
            )
            ...
        yield f"id: {event.sequence}\nevent: role_event\ndata: {event.model_dump_json()}\n\n".encode()
~~~

Do not retain the FastAPI request Session, or a transaction, for the lifetime of a stream. Emit an event named snapshot_required when needed and heartbeat comments only when the idle interval expires. The browser reconnects using the last delivered sequence; no raw job/event feed is permitted.

- [ ] **Step 6: Run stream tests.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_stream.py
~~~

Expected: PASS, including a finite iterator test for ordering, cursor replay, gap snapshot, unauthorized reader rejection before generator construction, SSE framing, and redaction.

- [ ] **Step 7: Commit safe streaming.**

~~~bash
git add backend/app/services/research_gateway_stream.py backend/tests/test_research_gateway_stream.py
git commit -m "feat: stream safe Gateway role events"
~~~

### Task 6: Publish the versioned FastAPI Gateway contract

**Files:**

- Create: backend/app/schemas/v1/research_gateway.py
- Create: backend/app/api/v1/research_gateway.py
- Modify: backend/app/api/v1/router.py
- Create: backend/tests/test_research_gateway_api.py
- Modify: backend/tests/test_event_case_tenant_access.py

- [ ] **Step 1: Write failing HTTP and OpenAPI contract tests.**

~~~python
def test_message_start_returns_one_receipt_and_safe_initial_timeline(
    gateway_client, monkeypatch
) -> None:
    response = gateway_client.post(
        "/api/v1/research-conversations",
        headers={"Idempotency-Key": "create-1"},
        json={"initial_message": "研究光模块行业需求变化"},
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) >= {
        "conversation_id", "intent_id", "run_spec_id",
        "native_case_id", "native_run_id", "latest_sequence",
    }
    assert "prompt" not in response.text
    assert "tool_arguments" not in response.text


def test_other_subject_cannot_read_private_conversation(
    gateway_client, other_subject_headers, started_gateway_receipt
) -> None:
    response = gateway_client.get(
        f"/api/v1/research-conversations/{started_gateway_receipt.conversation_id}",
        headers=other_subject_headers,
    )
    assert response.status_code == 404
~~~

- [ ] **Step 2: Run the API tests and confirm the route/schema imports are missing.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_api.py
~~~

Expected: FAIL with 404 or ImportError.

- [ ] **Step 3: Define strict Pydantic DTOs.**

Create wire types for ConversationCreateRequest, MessageSendRequest, GatewayReceiptDTO, CommandRequest, CommandReceiptDTO, SafeRoleEventDTO, ConversationSnapshotDTO, ConversationProjectionDTO, and ConversationListResponse.

Use V1Model everywhere. MessageSendRequest has only text; command input has a Literal command kind and bounded reason/successor text. All response lists are typed. Do not use dict[str, Any] for user-visible role event payloads.

- [ ] **Step 4: Implement the routes as thin adapters to ResearchGateway.**

Use:

~~~python
router = APIRouter(
    prefix="/research-conversations",
    tags=["research-gateway-v1"],
    dependencies=[Depends(require_gateway_actor)],
)
~~~

Every handler gets the trusted actor from require_gateway_actor, passes it to the deep module, and lets private ownership/Case tenant failures surface as a generic 404. Message and command endpoints require Idempotency-Key, reject blank/oversized values, and map a fingerprint collision to the existing 409 error envelope.

For events:

~~~python
@router.get("/{conversation_id}/events")
def stream_events(...):
    gateway.require_read_access(actor=actor, conversation_id=conversation_id)
    return StreamingResponse(
        sse_frames(...),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
~~~

Perform authorization before constructing the generator. Register this router in backend/app/api/v1/router.py. Do not add Gateway behavior to research_session.py.

- [ ] **Step 5: Run route, OpenAPI, and tenant privacy tests.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_api.py tests/test_research_gateway_identity.py tests/test_event_case_tenant_access.py
~~~

Expected: PASS. Verify OpenAPI lists the event stream media type and no schema exposes raw trace/details fields.

- [ ] **Step 6: Commit the HTTP contract.**

~~~bash
git add backend/app/schemas/v1/research_gateway.py backend/app/api/v1/research_gateway.py backend/app/api/v1/router.py backend/tests/test_research_gateway_api.py backend/tests/test_event_case_tenant_access.py
git commit -m "feat: expose authenticated FundClaw Gateway API"
~~~

### Task 7: Close directly relevant unauthenticated operational bypasses

**Files:**

- Modify: backend/app/api/v1/jobs.py
- Modify: backend/app/api/v1/activity.py
- Modify: backend/app/api/legacy.py
- Create: backend/tests/test_operational_access_api.py
- Modify: backend/tests/test_workbench_api.py
- Create: backend/tests/test_research_gateway_security.py

- [ ] **Step 1: Write failing access-control regression tests for the bypasses.**

~~~python
def test_job_route_cannot_expose_or_cancel_foreign_or_caseless_jobs(
    cmd_client, cmd_session, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"a":{"tenant_id":"team-a","subject_id":"alice"},'
        '"b":{"tenant_id":"team-b","subject_id":"bob"}}',
    )
    foreign = cmd_client.get(f"/api/v1/jobs/{foreign_case_job_id}", headers=_auth("b"))
    caseless = cmd_client.get(f"/api/v1/jobs/{caseless_job_id}", headers=_auth("a"))
    assert foreign.status_code == 404
    assert caseless.status_code == 404


def test_legacy_workbench_requires_case_tenant_admission(cmd_client, monkeypatch) -> None:
    response = cmd_client.get(
        f"/api/research-cases/{foreign_case_id}/workbench", headers=_auth("b")
    )
    assert response.status_code == 404
~~~

- [ ] **Step 2: Run the security tests to prove current routes bypass authorization.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_security.py
~~~

Expected: FAIL because jobs/activity/tasks/legacy currently expose unguarded data.

- [ ] **Step 3: Require tenant and Case access for generic jobs.**

Add require_research_actor at the router level in backend/app/api/v1/jobs.py. Before read, events, cancel, or retry:

~~~python
def _require_visible_job(db: Session, job_id: UUID, actor: ResearchActor) -> Job:
    job = JobRepository(db).get_job(job_id)
    if job is None or job.research_case_id is None:
        raise NotFoundError("job not found")
    CaseTenantAccess(db).require_case(job.research_case_id, actor.tenant_id)
    return job
~~~

Do not return JobEvent.message or Job.error through Gateway. The legacy endpoint may retain its own contract after authentication but it is not a Gateway source or deep link.

- [ ] **Step 4: Make activity/task endpoints fail closed rather than post-filtering global events.**

In backend/app/api/v1/activity.py:

1. Require a trusted actor.
2. Make case_id mandatory for activity/evidence changes; validate it with CaseTenantAccess before query.
3. Require a case ID on externally created/updated/read tasks and return 404 for a task with no Case.
4. Do not attempt tenant-after-the-fact filtering on DomainEvent because its payload is not uniformly Case-keyed.
5. Return a bounded operational DTO and do not expose raw DomainEvent.payload/actor in any future Gateway path.

Keep all Gateway UI reads on its safe projection routes, not these generic feeds.

- [ ] **Step 5: Protect the legacy workbench route.**

In backend/app/api/legacy.py add require_research_actor and CaseTenantAccess before WorkbenchService.load_workbench:

~~~python
@router.get("/{case_id}/workbench")
def workbench(
    case_id: UUID,
    cutoff: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> dict:
    CaseTenantAccess(db).require_case(case_id, actor.tenant_id)
    ...
~~~

Update all tests/clients that intentionally exercise the compatibility route to include an admitted tenant token.

- [ ] **Step 6: Run security and affected regression suites.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_security.py tests/test_operational_access_api.py tests/test_workbench_api.py tests/test_event_case_tenant_access.py
~~~

Expected: PASS. Verify missing/foreign credentials produce 401/403/404 according to the existing non-enumeration convention, and no case-less job/task can be externally enumerated.

- [ ] **Step 7: Commit the security closure.**

~~~bash
git add backend/app/api/v1/jobs.py backend/app/api/v1/activity.py backend/app/api/legacy.py backend/tests/test_research_gateway_security.py backend/tests/test_operational_access_api.py backend/tests/test_workbench_api.py
git commit -m "fix: close Gateway operational data bypasses"
~~~

### Task 8: Verify recovery, migration, and release readiness

**Files:**

- Modify: backend/tests/test_research_gateway_service.py
- Modify: backend/tests/test_research_gateway_automatic_adapter.py
- Modify: backend/tests/test_research_gateway_stream.py
- Modify: backend/tests/test_research_gateway_migration.py
- Create: docs/evaluation/fundclaw_gateway_p0_acceptance.md

- [ ] **Step 1: Add a real vertical-slice acceptance test with a fake extractor and persisted worker transition.**

~~~python
def test_gateway_vertical_slice_survives_receipt_replay_refresh_and_worker_restart(
    gateway_client, cmd_session, monkeypatch
) -> None:
    first = _send(gateway_client, key="one")
    replay = _send(gateway_client, key="one")
    assert replay == first

    _run_one_native_worker_transition(cmd_session)
    refreshed = gateway_client.get(
        f"/api/v1/research-conversations/{first['conversation_id']}/snapshot"
    )
    events = _finite_sse_replay(
        gateway_client, first["conversation_id"], after_sequence=0
    )
    assert refreshed.status_code == 200
    assert [item["sequence"] for item in events] == sorted(
        item["sequence"] for item in events
    )
    assert len({item["sequence"] for item in events}) == len(events)
~~~

- [ ] **Step 2: Run the focused complete P0 suite.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_research_gateway_identity.py tests/test_research_gateway_repository.py tests/test_research_gateway_migration.py tests/test_research_gateway_service.py tests/test_research_gateway_automatic_adapter.py tests/test_research_gateway_stream.py tests/test_research_gateway_api.py tests/test_research_gateway_security.py
~~~

Expected: PASS with no external model/provider/network dependency.

- [ ] **Step 3: Run the affected legacy regression suite.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q tests/test_automatic_research_api.py tests/test_automatic_research_pipeline.py tests/test_research_worker_entrypoint.py tests/test_event_case_tenant_access.py tests/test_operational_access_api.py tests/test_workbench_api.py
~~~

Expected: PASS.

- [ ] **Step 4: Verify migrations on both supported dialects.**

Run SQLite migration/schema contract tests first. Then, in the configured disposable PostgreSQL environment:

~~~bash
cd backend && TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest -q -m pg_only tests/test_research_gateway_migration.py
~~~

Expected: PASS. Confirm the migration upgrade and downgrade leave no stale triggers or indexes and append-only rows reject both UPDATE and DELETE.

- [ ] **Step 5: Run the full backend suite before declaring P0 ready.**

Run:

~~~bash
cd backend && .venv/bin/python -m pytest -q
~~~

Expected: PASS, with only documented opt-in external markers skipped.

- [ ] **Step 6: Write a concise operational acceptance note.**

Create docs/evaluation/fundclaw_gateway_p0_acceptance.md containing:

1. Required host token shape with tenant_id, subject_id, and roles.
2. Commands for API, research worker, and a fetch-based authenticated SSE client.
3. The expected receipt/replay/restart behavior.
4. The redaction and source-policy non-negotiables.
5. Explicit exclusions: company research, external OpenClaw runtime, arbitrary tools, shared conversations, and unsupported P0 command kinds.

- [ ] **Step 7: Perform final static review and commit the acceptance artifacts.**

Run:

~~~bash
git diff --check
git status --short
git diff --check HEAD
~~~

Then commit only the acceptance/documentation and final test changes:

~~~bash
git add backend/tests/test_research_gateway_service.py backend/tests/test_research_gateway_automatic_adapter.py backend/tests/test_research_gateway_stream.py backend/tests/test_research_gateway_migration.py docs/evaluation/fundclaw_gateway_p0_acceptance.md
git commit -m "test: verify FundClaw Gateway P0 recovery"
~~~

## Plan self-review

### Spec coverage

| Validated design requirement | Implementing task |
| --- | --- |
| Stable tenant + human subject, no client-supplied actor | Task 1 |
| Persistent private conversation, intent, immutable RunSpec, role records, command audit | Task 2 |
| Fixed roles and server-owned capabilities | Task 3 |
| Reuse automatic intake/workers/evidence, no second ledger | Task 4 |
| Safe observable role timeline, replay, reconnect, no raw reasoning/traces | Tasks 4–6 |
| Idempotent send/command and successor scope history | Tasks 2–3 and 8 |
| Tenant/case/private-thread access and bypass closure | Tasks 1, 3, 6, and 7 |
| API/worker restart resilience and dialect verification | Task 8 |
| Company research / monitoring / OpenClaw runtime deferred | Scope boundary |

### Placeholder scan

No task uses placeholder markers or an unspecified error-handling step. Unsupported P0 capabilities have a specific persisted rejected outcome instead of an implicit promise.

### Type consistency

The same names are used throughout: ResearchGateway, ResearchGatewayRepository, NativeResearchRuntime, AutomaticResearchRuntime, GatewayAutomaticResearchAdapter, ResearchConversation, ResearchRunSpec, RoleRun, RoleEvent, and GatewayIdempotencyRequest. The public cursor is the conversation sequence; a role event additionally has a run sequence for run-local audit.

### Follow-on plans

After this P0 is independently accepted, write separate plans for:

1. The FundClaw three-column frontend and fetch-SSE client.
2. Native pause/resume, grounded frozen-material Q&A, and richer control-command support.
3. Tenant-scoped CompanyResearchRun and governed announcement monitoring.
4. Optional sandboxed OpenClawRuntimeAdapter.
