# 研究准备工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新建或尚未启动正式研究的事件 Case 与 `ResearchRun` 之间建立可观察、可恢复、必须人工授权的研究准备流程。

**Architecture:** 新增独立的 Preparation 聚合保存准备版本、三个系统步骤、三个人工步骤和追加式活动事件；它不创建正式 Run。现有 Worker 领取 Preparation 工作，调用已接入的 LLM 只生成候选陈述、协议草案和补证计划；只有第三个人工命令在 Case 锁内物化正式协议并幂等创建 `ResearchRun`。前端以新的 Preparation 页面和 Desk 投影呈现系统工作、唯一当前任务和完整日志。

**Tech Stack:** FastAPI、SQLAlchemy/Alembic、PostgreSQL/SQLite、现有 `LLMClient`、pytest、React、TypeScript、Vite、Vitest、OpenAPI 合同同步。

---

## 文件结构与边界

| 路径 | 职责 |
| --- | --- |
| `backend/app/models/research_preparation.py` | Preparation、版本化产物和追加式活动事件 ORM 模型及 DB 约束。 |
| `backend/alembic/versions/0055_research_preparation_workbench.py` | 三张新表、索引、唯一约束和回填标记的迁移。 |
| `backend/app/repositories/research_preparation.py` | 行锁、事件序号、当前产物、幂等排队和回填查询。 |
| `backend/app/domain/research_preparation.py` | 固定步骤、状态、产物类型、输入指纹与安全错误的纯函数。 |
| `backend/app/services/research_preparation.py` | 创建、编排、失效、重试、人工确认与授权状态机。 |
| `backend/app/ai/research_preparation.py` | LLM 结构化草案生成；只返回准备产物，不写正式协议或 Run。 |
| `backend/app/scripts/run_research_preparation_worker.py` | 独立领取、执行、恢复 Preparation Job 的 Worker。 |
| `backend/app/api/v1/research_preparation.py` | 准备摘要、活动、三个人工命令和重试 API。 |
| `backend/app/schemas/v1/research_preparation.py` | Preparation API 请求/响应 OpenAPI DTO。 |
| `backend/app/services/event_research.py` | Case 创建事务中创建 Preparation 并改变生命周期文案。 |
| `backend/app/queries/event_research.py`、`backend/app/domain/event_research.py` | Desk/工作台的准备状态、下一动作和路由投影。 |
| `frontend/src/features/events/ResearchPreparationPage.tsx` | 双栏准备工作台、三步确认、失败恢复和轮询。 |
| `frontend/src/domain/researchPreparation.ts` | 前端 Preparation 类型、状态文案、当前任务派生。 |
| `frontend/src/data/httpResearchAdapter.ts`、`frontend/src/data/mockResearchAdapter.ts` | 新 API 的 HTTP 和显式 mock 实现。 |
| `frontend/src/features/events/EventCreatePage.tsx`、`frontend/src/app/routes.tsx` | 新建后跳转与 `/events/:caseId/preparation` 路由。 |
| `frontend/src/features/events/EventDeskPage.tsx`、`frontend/src/domain/eventResearchPresentation.ts` | Desk 的“系统处理中/待你确认/失败”投影和准备页链接。 |
| `frontend/src/styles/research-os-overrides.css` | 准备页面布局、状态、窄屏和可访问性样式；保留用户已有未提交改动。 |
| `backend/tests/test_research_preparation*.py` | 状态机、API、Worker、回填、并发、租户和迁移测试。 |
| `frontend/src/tests/ResearchPreparationPage.test.tsx`、`frontend/src/tests/HttpResearchAdapter.test.ts` | 页面状态、路由、轮询和合同适配测试。 |

## 共享数据契约

准备状态和步骤名在后端领域模块、DTO 与前端只能使用下列值：

```python
PreparationStatus = Literal[
    "preparing",
    "awaiting_claim_review",
    "awaiting_protocol_confirmation",
    "awaiting_plan_authorization",
    "recoverable_failure",
    "authorized",
]

PreparationStep = Literal[
    "parse_claims",
    "draft_protocol",
    "draft_evidence_plan",
]

StepState = Literal["queued", "running", "succeeded", "retrying", "failed", "stale"]
ReviewState = Literal["locked", "awaiting_review", "confirmed", "stale"]
ArtifactKind = Literal["atomic_claim_candidates", "research_protocol_draft", "evidence_acquisition_plan"]
```

准备页只把 `preparing` 解释成“系统正在准备”；它永不表示正式补证正在运行。`authorized` 以前，`research_run_id` 必须为 `NULL`。

## Task 1: 建立迁移、ORM 和领域常量

**Files:**

- Create: `backend/app/models/research_preparation.py`
- Create: `backend/app/domain/research_preparation.py`
- Create: `backend/alembic/versions/0055_research_preparation_workbench.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/operational.py`
- Test: `backend/tests/test_research_preparation_models.py`
- Test: `backend/tests/test_sqlite_migration_bootstrap.py`

- [ ] **Step 1: 写 ORM 约束的失败测试。**

```python
def test_preparation_has_one_current_row_and_no_run_before_authorization(session, case):
    first = ResearchPreparation.new(case.id, input_fingerprint="v1")
    session.add(first)
    session.commit()

    session.add(ResearchPreparation.new(case.id, input_fingerprint="v2"))
    with pytest.raises(IntegrityError):
        session.commit()

    first.research_run_id = uuid.uuid4()
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: 运行失败测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_models.py::test_preparation_has_one_current_row_and_no_run_before_authorization`

Expected: FAIL，因为模型和表尚不存在。

- [ ] **Step 3: 定义不可混用的 Preparation 表。**

在 `research_preparation.py` 定义以下三张表，所有时间使用 UTC：

```python
class ResearchPreparation(Base):
    __tablename__ = "research_preparations"
    __table_args__ = (
        UniqueConstraint("research_case_id", name="uq_research_preparations_case"),
        CheckConstraint("status IN ('preparing','awaiting_claim_review','awaiting_protocol_confirmation','awaiting_plan_authorization','recoverable_failure','authorized')", name="ck_research_preparations_status"),
        CheckConstraint("(status = 'authorized' AND research_run_id IS NOT NULL) OR (status <> 'authorized' AND research_run_id IS NULL)", name="ck_research_preparations_run_after_authorization"),
    )
    # id, research_case_id, version, input_fingerprint, status,
    # parse_claims_state, draft_protocol_state, draft_evidence_plan_state,
    # claim_review_state, protocol_review_state, plan_review_state,
    # research_run_id, next_attempt_at, last_error_code, created_at, updated_at

class ResearchPreparationArtifact(Base):
    __tablename__ = "research_preparation_artifacts"
    __table_args__ = (UniqueConstraint("research_preparation_id", "sequence", name="uq_research_preparation_artifacts_sequence"),)
    # id, research_preparation_id, kind, sequence, input_fingerprint,
    # payload, state(current|stale|superseded), invalidated_reason, created_at

class ResearchPreparationEvent(Base):
    __tablename__ = "research_preparation_events"
    __table_args__ = (UniqueConstraint("research_preparation_id", "seq", name="uq_research_preparation_events_sequence"),)
    # id, research_preparation_id, seq, type, step, message, detail, created_at
```

`JobKind` 增加 `"prepare_research"`，但 `ResearchRun` 不增加任何“准备中”状态。

- [ ] **Step 4: 编写 0055 迁移。**

迁移以 `0054` 为 `down_revision`，在 SQLite/PostgreSQL 上创建三张表、Case 唯一索引、事件 `(research_preparation_id, seq)` 索引、当前产物 `(research_preparation_id, kind, state)` 索引，并创建 `research_run_id → research_runs.id` 外键。迁移不得访问网络、不得创建 Job、不得回填数据。

- [ ] **Step 5: 通过模型与迁移测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_models.py tests/test_sqlite_migration_bootstrap.py`

Expected: PASS；SQLite 从旧版本升级到 head 后可插入合法 Preparation，重复 Case 和授权前 Run 均被拒绝。

- [ ] **Step 6: 提交。**

```bash
git add backend/app/models/research_preparation.py backend/app/domain/research_preparation.py backend/app/models/__init__.py backend/app/models/operational.py backend/alembic/versions/0055_research_preparation_workbench.py backend/tests/test_research_preparation_models.py backend/tests/test_sqlite_migration_bootstrap.py
git commit -m "feat: add research preparation persistence"
```

## Task 2: 准备状态机、锁和活动事件

**Files:**

- Create: `backend/app/repositories/research_preparation.py`
- Create: `backend/app/services/research_preparation.py`
- Test: `backend/tests/test_research_preparation_service.py`

- [ ] **Step 1: 写状态机 RED 测试。**

```python
def test_claim_confirmation_invalidates_only_protocol_and_plan(session, prepared_case):
    service = ResearchPreparationService(session)
    service.complete_system_step(prepared_case.id, "parse_claims", _claim_payload())
    service.complete_system_step(prepared_case.id, "draft_protocol", _protocol_payload())
    service.complete_system_step(prepared_case.id, "draft_evidence_plan", _plan_payload())

    service.confirm_claims(prepared_case.id, actor="human:reviewer", decisions=_modified_decisions())
    view = service.get(prepared_case.id)

    assert view.parse_claims_state == "succeeded"
    assert view.draft_protocol_state == "stale"
    assert view.draft_evidence_plan_state == "stale"
    assert view.claim_review_state == "confirmed"
    assert view.protocol_review_state == "locked"
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_service.py::test_claim_confirmation_invalidates_only_protocol_and_plan`

Expected: FAIL，因为没有状态机或失效规则。

- [ ] **Step 3: 实现 Repository 的锁和追加事件。**

实现以下固定接口；所有变更者先锁 Case，再锁 Preparation：

```python
class ResearchPreparationRepository:
    def lock_for_case(self, case_id: uuid.UUID) -> ResearchPreparation: raise NotImplementedError
    def append_event(self, preparation: ResearchPreparation, *, type: str, step: str | None, message: str, detail: dict[str, object]) -> ResearchPreparationEvent: raise NotImplementedError
    def current_artifact(self, preparation_id: uuid.UUID, kind: ArtifactKind) -> ResearchPreparationArtifact | None: raise NotImplementedError
    def append_artifact(self, preparation: ResearchPreparation, *, kind: ArtifactKind, input_fingerprint: str, payload: dict[str, object]) -> ResearchPreparationArtifact: raise NotImplementedError
    def queue_step_job(self, preparation: ResearchPreparation, step: PreparationStep) -> Job: raise NotImplementedError
```

`append_artifact` 必须先把同 kind 的 current 产物标为 `superseded`，再写新序号；不能更新历史 payload。`append_event` 使用数据库中最大 `seq + 1` 并在锁内写入。

- [ ] **Step 4: 实现纯状态机。**

`ResearchPreparationService` 必须提供：

```python
def create_for_case(self, case_id: uuid.UUID, *, input_fingerprint: str, actor: str) -> ResearchPreparation: raise NotImplementedError
def complete_system_step(self, case_id: uuid.UUID, step: PreparationStep, payload: dict[str, object]) -> ResearchPreparation: raise NotImplementedError
def mark_step_failed(self, case_id: uuid.UUID, step: PreparationStep, error_code: str, retry_at: datetime | None) -> ResearchPreparation: raise NotImplementedError
def confirm_claims(self, case_id: uuid.UUID, *, actor: str, decisions: list[ClaimDecision]) -> ResearchPreparation: raise NotImplementedError
def confirm_protocol(self, case_id: uuid.UUID, *, actor: str, revision: int, payload: ProtocolConfirmation) -> ResearchPreparation: raise NotImplementedError
def authorize_plan(self, case_id: uuid.UUID, *, actor: str, revision: int, idempotency_key: str) -> ResearchPreparation: raise NotImplementedError
def retry_failed_step(self, case_id: uuid.UUID, *, actor: str, revision: int) -> ResearchPreparation: raise NotImplementedError
```

规则固定如下：

- `parse_claims` 成功后开放 Claim review，协议/计划保持锁定；
- Claim review 已确认且协议草案成功后开放 Protocol review；
- Protocol review 已确认且计划成功后开放 Plan authorization；
- 任一 Claim 修改或拒绝使协议和计划的产物与人工确认 `stale`，并仅重排协议步骤；协议重新完成后才重排计划步骤；
- `failed` 仅影响失败步骤及其下游，不删除上游 `succeeded` 产物；
- 所有用户命令检查 `revision`，版本过期抛 `ConflictError`；
- 所有 Provider 错误用固定 `preparation_provider_unavailable`，不得保存 `str(exc)`。

- [ ] **Step 5: 追加完整状态机测试。**

```python
def test_failed_plan_keeps_claims_and_protocol_and_manual_retry_queues_only_plan(session, prepared_case): assert _retry_requeues_only_plan(session, prepared_case)
def test_stale_worker_completion_cannot_replace_newer_artifact(session, prepared_case): assert _late_output_is_discarded(session, prepared_case)
def test_confirming_protocol_before_claims_is_a_conflict(session, prepared_case): assert _protocol_before_claims_conflicts(session, prepared_case)
def test_event_sequence_is_contiguous_and_secret_free(session, prepared_case): assert _events_are_contiguous_and_redacted(session, prepared_case)
```

- [ ] **Step 6: 运行服务测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_service.py`

Expected: PASS，包含失效、重试、版本冲突和日志序号断言。

- [ ] **Step 7: 提交。**

```bash
git add backend/app/repositories/research_preparation.py backend/app/services/research_preparation.py backend/tests/test_research_preparation_service.py
git commit -m "feat: add research preparation state machine"
```

## Task 3: 只生成草案的 LLM 准备器

**Files:**

- Create: `backend/app/ai/research_preparation.py`
- Modify: `backend/app/ai/client.py`
- Test: `backend/tests/test_research_preparation_generator.py`

- [ ] **Step 1: 写 Provider 与结构校验 RED 测试。**

```python
def test_generator_returns_drafts_and_never_creates_research_run(session, frozen_case, fake_llm):
    result = ResearchPreparationGenerator(fake_llm).draft_protocol(_preparation_input(session, frozen_case.id))
    assert set(result) == {"outcomes", "baseline", "horizon", "mechanisms", "verification_rules"}
    assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0

def test_malformed_provider_payload_is_safe_provider_error(frozen_case, fake_llm):
    fake_llm.reply = {"outcomes": "not-a-list"}
    with pytest.raises(ResearchPreparationProviderError, match="invalid preparation response"):
        ResearchPreparationGenerator(fake_llm).draft_protocol(_input(frozen_case))
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_generator.py`

Expected: FAIL，因为生成器和专用错误还不存在。

- [ ] **Step 3: 实现严格的准备输出。**

在 `backend/app/ai/research_preparation.py` 定义：

```python
class ResearchPreparationProviderError(RuntimeError):
    """Safe failure category for setup, transport and malformed preparation replies."""

class ResearchPreparationGenerator:
    def parse_claims(self, input: PreparationInput) -> dict[str, object]: raise NotImplementedError
    def draft_protocol(self, input: PreparationInput) -> dict[str, object]: raise NotImplementedError
    def draft_evidence_plan(self, input: PreparationInput) -> dict[str, object]: raise NotImplementedError
```

三个方法只能使用 `LLMClient.chat_json`，输出必须通过以下形状：

```python
Claim payload: {"candidates": [{"source_span_id": UUID, "quote": str, "quote_start": int, "quote_end": int, "normalized_text": str, "claim_type": str}]}
Protocol payload: {"outcomes": [dict], "baseline": dict, "horizon": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, "mechanisms": [dict], "verification_rules": [dict]}
Plan payload: {"items": [{"factor": str, "evidence_target": str, "allowed_source_roles": [str], "priority": "high|normal|low", "stop_condition": str, "budget": int}]}
```

原文引用必须在传入 SourceSpan 中连续出现；候选调用现有 `AtomicClaimService.admit` 后只写 `AtomicClaimCandidate`。协议和计划仅返回 JSON 草案，不能调用 `ResearchProtocolService` 或 `AutoResearchService`。

- [ ] **Step 4: 显式映射已知 Provider 失败。**

仅将 `OpenAIError`、`httpx.HTTPError`、`TimeoutError`、`ConnectionError`、`ValueError` 和 LLM 客户端定义的 malformed-response 错误转成 `ResearchPreparationProviderError("preparation provider unavailable or returned an invalid response")`；`TypeError`、`KeyError`、`AssertionError` 等程序错误必须继续抛出，保持 500 可见。

- [ ] **Step 5: 运行生成器测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_generator.py tests/test_ai_engine.py`

Expected: PASS；验证真实输入的 span、严格 JSON、无 Run、无 secret 和程序错误透传。

- [ ] **Step 6: 提交。**

```bash
git add backend/app/ai/research_preparation.py backend/app/ai/client.py backend/tests/test_research_preparation_generator.py
git commit -m "feat: generate review-gated preparation drafts"
```

## Task 4: Preparation Worker、有限重试和回填扫描

**Files:**

- Create: `backend/app/scripts/run_research_preparation_worker.py`
- Modify: `backend/app/repositories/research_preparation.py`
- Modify: `backend/app/services/research_preparation.py`
- Create: `backend/app/services/research_preparation_backfill.py`
- Test: `backend/tests/test_research_preparation_worker.py`
- Test: `backend/tests/test_research_preparation_backfill.py`

- [ ] **Step 1: 写 Worker RED 测试。**

```python
def test_worker_runs_parse_then_protocol_then_plan_without_creating_run(session, queued_preparation, fake_llm):
    assert run_once(session_factory, generator_factory=lambda: ResearchPreparationGenerator(fake_llm)) is True
    assert _states(session, queued_preparation.id) == ("succeeded", "queued", "queued")
    assert _research_run_count(session, queued_preparation.case_id) == 0

def test_worker_uses_backoff_and_preserves_prior_artifacts(session, protocol_failed_preparation, failing_llm): assert _failure_preserves_artifacts_and_sets_retry(session, protocol_failed_preparation)
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_worker.py`

Expected: FAIL，因为没有独立 Worker。

- [ ] **Step 3: 实现领取与执行。**

`run_research_preparation_worker.py` 采用现有 `SessionLocal` 和 heartbeat 模式，但只领取 `Job.kind == "prepare_research"`。执行顺序固定为：

```python
STEP_ORDER = ("parse_claims", "draft_protocol", "draft_evidence_plan")
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120, 600)
```

执行步骤前锁 Preparation 并刷新；若版本或输入指纹不匹配，写 `preparation_output_discarded` 事件并取消该 Job。Provider 返回后再次锁定并确认输出槽位，之后才写 Artifact、成功 Event 和 Job 终态。失败写固定错误码、尝试次数和 `next_attempt_at`；第三次失败才变为 `recoverable_failure`。

- [ ] **Step 4: 实现幂等回填。**

`ResearchPreparationBackfill.enqueue_eligible(limit: int = 100)` 只选中：已 tenant admitted、存在 CaseDocumentVersion、没有 `ResearchRun`、EventLifecycle 不为 `published`、没有 `ResearchPreparation` 的 Case。排序 `ResearchCase.created_at, ResearchCase.id`。它只创建 Preparation 和首个 parse Job，重复执行不产生第二行。

- [ ] **Step 5: 写并运行回填、恢复和迟到结果测试。**

```python
def test_backfill_orders_oldest_cases_and_is_idempotent(session): assert _backfill_is_oldest_first_and_idempotent(session)
def test_worker_drops_old_version_output_after_claim_review_changed_input(session): assert _stale_claim_output_is_discarded(session)
def test_exhausted_retry_exposes_manual_retry_without_deleting_parse_artifact(session): assert _exhausted_retry_keeps_parse_artifact(session)
```

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_worker.py tests/test_research_preparation_backfill.py`

Expected: PASS；断言没有 `ResearchRun`、Job 被安全结束、日志没有 Provider 原文。

- [ ] **Step 6: 提交。**

```bash
git add backend/app/scripts/run_research_preparation_worker.py backend/app/services/research_preparation_backfill.py backend/app/repositories/research_preparation.py backend/app/services/research_preparation.py backend/tests/test_research_preparation_worker.py backend/tests/test_research_preparation_backfill.py
git commit -m "feat: run and backfill research preparation"
```

## Task 5: 创建 Case 时排队，且不伪造正式研究状态

**Files:**

- Modify: `backend/app/services/event_research.py`
- Modify: `backend/app/models/operational.py`
- Modify: `backend/app/domain/event_research.py`
- Modify: `backend/app/services/event_research_scope.py`
- Test: `backend/tests/test_event_research_api.py`
- Test: `backend/tests/test_event_research_lifecycle.py`

- [ ] **Step 1: 写新建 Case 的 RED API 测试。**

```python
def test_create_event_queues_preparation_but_not_research_run(cmd_client, cmd_session):
    response = cmd_client.post("/api/v1/event-research", json=_strict_event_payload())
    assert response.status_code == 201
    case_id = uuid.UUID(response.json()["case_id"])
    preparation = cmd_session.scalar(select(ResearchPreparation).where(ResearchPreparation.research_case_id == case_id))
    assert preparation.status == "preparing"
    assert preparation.research_run_id is None
    assert cmd_session.scalar(select(func.count()).select_from(ResearchRun).where(ResearchRun.research_case_id == case_id)) == 0
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_event_research_api.py::test_create_event_queues_preparation_but_not_research_run`

Expected: FAIL，因为创建服务尚未创建 Preparation。

- [ ] **Step 3: 在创建事务中调用 Preparation service。**

在 `EventResearchService.create` 写完冻结 Document、SourceSpan、Brief、Scope、Thesis 和 `EventResearchLifecycle` 后、`commit()` 前调用：

```python
ResearchPreparationService(self._session).create_for_case(
    case.id,
    input_fingerprint=preparation_input_fingerprint(document.id, scope.id),
    actor=payload.created_by,
)
```

初始 lifecycle 固定为：

```python
status="awaiting_key_review"
status_summary="资料已冻结；系统正在准备候选陈述、研究协议草案和补证计划"
current_gap="研究准备尚未完成；ResearchRun 未创建，正式补证尚未启动"
next_human_action=None
```

创建成功响应可保留现有生命周期 DTO，但不得返回任何 Run ID。

- [ ] **Step 4: 让范围编辑失效准备输入。**

在 `EventResearchScopeService.update` 成功新增 scope version 后调用 `ResearchPreparationService.invalidate_from_scope_change(case_id, scope.id, actor)`。该调用只让协议与计划 stale，不删除已经人工审核的来源陈述。

- [ ] **Step 5: 运行创建与生命周期测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_event_research_api.py tests/test_event_research_lifecycle.py tests/test_event_research_scope.py`

Expected: PASS；旧的 legacy `research_protocol_required=False` fixture 保持其既有行为，新默认 intake 进入 Preparation。

- [ ] **Step 6: 提交。**

```bash
git add backend/app/services/event_research.py backend/app/models/operational.py backend/app/domain/event_research.py backend/app/services/event_research_scope.py backend/tests/test_event_research_api.py backend/tests/test_event_research_lifecycle.py
git commit -m "feat: queue preparation for new event research"
```

## Task 6: 人工确认命令和唯一正式授权

**Files:**

- Create: `backend/app/schemas/v1/research_preparation.py`
- Create: `backend/app/api/v1/research_preparation.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/services/research_preparation.py`
- Modify: `backend/app/services/research_protocol.py`
- Test: `backend/tests/test_research_preparation_api.py`

- [ ] **Step 1: 写确认顺序与幂等授权 RED 测试。**

```python
def test_authorization_requires_claim_and_protocol_confirmation(cmd_client, prepared_case):
    response = cmd_client.post(f"/api/v1/event-research/{prepared_case.id}/preparation/authorize", json=_authorization())
    assert response.status_code == 409

def test_authorization_replay_creates_exactly_one_run(cmd_client, prepared_ready_case):
    first = cmd_client.post(_authorize_url(prepared_ready_case), json=_authorization(key="authorize-1"))
    second = cmd_client.post(_authorize_url(prepared_ready_case), json=_authorization(key="authorize-1"))
    assert first.status_code == second.status_code == 201
    assert first.json()["research_run_id"] == second.json()["research_run_id"]
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_api.py -k "authorization"`

Expected: FAIL，因为路由和 DTO 尚不存在。

- [ ] **Step 3: 定义严格 DTO。**

定义并使用下列请求类型，所有写请求包含 `revision`，授权另含 `idempotency_key`：

```python
class ClaimDecisionDTO(V1Model):
    candidate_id: UUID
    outcome: Literal["confirmed", "modified", "rejected"]
    reason: str = Field(min_length=1, max_length=2000)
    normalized_text: str | None = None

class ConfirmClaimsRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    decisions: list[ClaimDecisionDTO] = Field(min_length=1)

class ConfirmProtocolRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    draft_sequence: int = Field(ge=1)
    edits: dict[str, object] = Field(default_factory=dict)

class AuthorizeEvidencePlanRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    plan_sequence: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)
```

- [ ] **Step 4: 实现 API。**

提供以下端点，并全部经 `require_research_tenant` 和 Case tenant access 过滤：

```text
GET  /api/v1/event-research/{case_id}/preparation
GET  /api/v1/event-research/{case_id}/preparation/events?after_seq=0
POST /api/v1/event-research/{case_id}/preparation/claims/confirm
POST /api/v1/event-research/{case_id}/preparation/protocol/confirm
POST /api/v1/event-research/{case_id}/preparation/authorize
POST /api/v1/event-research/{case_id}/preparation/retry
```

每个写端点把 `ConflictError` 映射为 409、输入不合法映射为 422、Provider error 映射为固定 503。不得把异常详情放进响应。

- [ ] **Step 5: 物化正式协议与 Run。**

`confirm_protocol` 只在人工提交有效当前草案时调用已有 `ResearchProtocolService`，依次写入 metric/binding draft、批准 binding、选择 template、写 verification rules；每一项的 `reviewer` 为请求 actor，`reason` 固定携带 artifact sequence。协议任何字段无法通过现有严格校验时返回 422，不把 Preparation 标记 confirmed。

`authorize_plan` 在 Case → Preparation → protocol rows 锁顺序内复核：三个人工步骤都是 `confirmed`、当前 plan artifact sequence 匹配、没有 ResearchRun。然后调用现有正式 Run 创建入口，写 Preparation `authorized` 和 `research_run_id`，追加 `research_authorized` 事件。重复相同 idempotency key 返回既有 Run；不同 key 的第二次授权返回 409。

- [ ] **Step 6: 写并运行完整 API 测试。**

```python
def test_cross_tenant_preparation_is_not_visible_or_mutable(other_tenant_client, prepared_case): assert _tenant_cannot_read_or_write(other_tenant_client, prepared_case)
def test_stale_revision_returns_409_without_new_protocol_rows(cmd_client, prepared_case): assert _stale_revision_writes_no_rows(cmd_client, prepared_case)
def test_provider_text_is_absent_from_failure_response_and_events(cmd_client, failed_preparation): assert _provider_text_is_redacted(cmd_client, failed_preparation)
```

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_api.py tests/test_research_protocol_api.py tests/test_atomic_claims_api.py`

Expected: PASS；授权前 Run 为零，授权后恰好一个，重复请求不重复创建。

- [ ] **Step 7: 同步合同并提交。**

```bash
cd backend && bash scripts/sync-contract.sh --update
git add backend/app/schemas/v1/research_preparation.py backend/app/api/v1/research_preparation.py backend/app/api/v1/router.py backend/app/services/research_preparation.py backend/app/services/research_protocol.py backend/tests/test_research_preparation_api.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: add preparation review and authorization API"
```

## Task 7: 读模型、生命周期与 Research Desk 投影

**Files:**

- Modify: `backend/app/queries/event_research.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/domain/event_research.py`
- Modify: `backend/app/repositories/event_research.py`
- Test: `backend/tests/test_event_research_api.py`
- Test: `backend/tests/test_research_preparation_api.py`

- [ ] **Step 1: 写 Desk 投影 RED 测试。**

```python
def test_event_list_projects_preparing_without_human_count(cmd_client, preparation_in_progress):
    item = _event_item(cmd_client, preparation_in_progress.case_id)
    assert item["next_action_kind"] == "wait"
    assert item["next_human_action"] is None
    assert "系统正在准备" in item["status_summary"]

def test_event_list_projects_claim_review_to_preparation(cmd_client, claims_ready_preparation):
    item = _event_item(cmd_client, claims_ready_preparation.case_id)
    assert item["next_action_kind"] == "review_preparation_claims"
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_event_research_api.py -k "preparation"`

Expected: FAIL，因为投影还不了解 Preparation。

- [ ] **Step 3: 扩展闭合动作词汇。**

在 `EventNextActionKind` 追加：

```python
"review_preparation_claims",
"review_preparation_protocol",
"authorize_preparation_plan",
"recover_preparation",
```

`EventResearchQueries._next_action` 必须优先读取当前 Preparation：

| Preparation 状态 | next_action_kind | next_human_action |
| --- | --- | --- |
| `preparing` | `wait` | `None` |
| `awaiting_claim_review` | `review_preparation_claims` | `核验原文与候选陈述` |
| `awaiting_protocol_confirmation` | `review_preparation_protocol` | `确认研究协议草案` |
| `awaiting_plan_authorization` | `authorize_preparation_plan` | `审核补证计划并授权启动` |
| `recoverable_failure` | `recover_preparation` | `恢复研究准备` |

正式 ResearchRun 存在后继续采用已有 lifecycle 投影。

- [ ] **Step 4: 增加 Preparation 摘要读 DTO。**

`EventWorkbenchDTO` 增加可空 `preparation: EventPreparationSummaryDTO | None`，包含 `status`、`revision`、所有步骤/人工步骤状态、`research_run_id`、`next_attempt_at` 和固定 `last_error_message`。非授权前 `research_run_id` 必须为 null。

- [ ] **Step 5: 运行投影测试与合同检查。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_event_research_api.py tests/test_research_preparation_api.py && bash scripts/sync-contract.sh --check`

Expected: PASS；Desk 不把系统执行算进“待你审核”，下一动作能准确导向准备页。

- [ ] **Step 6: 提交。**

```bash
git add backend/app/queries/event_research.py backend/app/schemas/v1/event_research.py backend/app/domain/event_research.py backend/app/repositories/event_research.py backend/tests/test_event_research_api.py backend/tests/test_research_preparation_api.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: project preparation status into event desk"
```

## Task 8: 前端领域类型和数据适配器

**Files:**

- Create: `frontend/src/domain/researchPreparation.ts`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Test: `frontend/src/tests/HttpResearchAdapter.test.ts`
- Test: `frontend/src/tests/MockResearchAdapter.test.ts`

- [ ] **Step 1: 写 HTTP adapter RED 测试。**

```ts
it("loads preparation with cursor events and sends optimistic revision", async () => {
  server.use(http.get("*/event-research/case-1/preparation", () => HttpResponse.json(preparationDto)));
  const view = await adapter.getResearchPreparation("case-1");
  await adapter.confirmPreparationClaims({ caseId: "case-1", revision: 3, actor: "human:researcher", decisions });
  expect(view.researchRunId).toBeNull();
  expect(lastRequestBody()).toMatchObject({ revision: 3, actor: "human:researcher" });
});
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd frontend && npm test -- --run src/tests/HttpResearchAdapter.test.ts src/tests/MockResearchAdapter.test.ts`

Expected: FAIL，因为客户端方法和映射类型尚不存在。

- [ ] **Step 3: 定义前端模型与客户端接口。**

```ts
export type ResearchPreparationStatus = "preparing" | "awaiting_claim_review" | "awaiting_protocol_confirmation" | "awaiting_plan_authorization" | "recoverable_failure" | "authorized";
export interface ResearchPreparationView { caseId: string; revision: number; status: ResearchPreparationStatus; researchRunId: string | null; steps: PreparationSteps; artifacts: PreparationArtifacts; nextAttemptAt: string | null; lastErrorMessage: string | null; }
export interface ResearchPreparationEvent { seq: number; type: string; step: string | null; message: string; detail: Record<string, unknown>; createdAt: string; }
```

在 `EventResearchClient` 添加 `getResearchPreparation`、`listResearchPreparationEvents`、`confirmPreparationClaims`、`confirmPreparationProtocol`、`authorizePreparationPlan`、`retryResearchPreparation`。HTTP adapter 仅消费生成的 OpenAPI 类型；mock adapter 显式生成 `preparing`、三个待确认、失败和授权状态，禁止隐式模拟完成。

- [ ] **Step 4: 处理 409 和 503。**

适配器将 API 409 暴露为可识别的 `ConflictError`，供页面提示“准备已更新，请刷新”；503 显示后端提供的固定安全提示，不拼接原始响应 body。

- [ ] **Step 5: 运行适配器测试和类型检查。**

Run: `cd frontend && npm test -- --run src/tests/HttpResearchAdapter.test.ts src/tests/MockResearchAdapter.test.ts && npx tsc --noEmit`

Expected: PASS。

- [ ] **Step 6: 提交。**

```bash
git add frontend/src/domain/researchPreparation.ts frontend/src/domain/eventResearch.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/data/researchClient.ts frontend/src/tests/HttpResearchAdapter.test.ts frontend/src/tests/MockResearchAdapter.test.ts
git commit -m "feat: add preparation client contract"
```

## Task 9: 准备工作台页面、路由和创建后的落点

**Files:**

- Create: `frontend/src/features/events/ResearchPreparationPage.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/features/events/EventCreatePage.tsx`
- Modify: `frontend/src/styles/research-os-overrides.css`
- Test: `frontend/src/tests/ResearchPreparationPage.test.tsx`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: 写页面状态 RED 测试。**

```tsx
it("says that formal research has not started while the system prepares", async () => {
  mockClient.getResearchPreparation.mockResolvedValue(preparingView);
  renderAt("/events/case-1/preparation");
  expect(await screen.findByText("ResearchRun 未创建，后台正式研究尚未启动")).toBeVisible();
  expect(screen.getByText("现在不用做")).toBeVisible();
  expect(screen.queryByRole("button", { name: /授权启动/ })).not.toBeInTheDocument();
});

it("shows only claim review as the primary action when claims are ready", async () => { mockClient.getResearchPreparation.mockResolvedValue(claimsReadyView); renderAt("/events/case-1/preparation"); expect(await screen.findByRole("button", { name: /确认候选陈述/ })).toBeVisible(); });
it("keeps later protocol and plan cards locked", async () => { mockClient.getResearchPreparation.mockResolvedValue(claimsReadyView); renderAt("/events/case-1/preparation"); expect(await screen.findByText("等待上一步确认")).toBeVisible(); });
it("renders a recoverable failure and manual retry without hiding prior artifacts", async () => { mockClient.getResearchPreparation.mockResolvedValue(failedPlanView); renderAt("/events/case-1/preparation"); expect(await screen.findByRole("button", { name: /手动重试/ })).toBeVisible(); expect(screen.getByText("候选陈述草案")).toBeVisible(); });
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd frontend && npm test -- --run src/tests/ResearchPreparationPage.test.tsx`

Expected: FAIL，因为页面和路由不存在。

- [ ] **Step 3: 实现页面结构。**

页面只使用一项主要操作，结构固定：

```tsx
<main className="ros-page ros-preparation">
  <PreparationHeader view={view} />
  <div className="ros-preparation-layout">
    <PreparationActivityTimeline events={events} loading={loadingEvents} />
    <aside className="ros-preparation-current-task">
      <PreparationCurrentTask view={view} onConfirm={() => undefined} onRetry={() => undefined} />
    </aside>
  </div>
</main>
```

Header 在 `authorized` 之前固定显示“ResearchRun 未创建，后台正式研究尚未启动”。Timeline 完整显示、不折叠准备事件。右栏按状态只能显示：说明性“现在不用做”、候选陈述表单、协议确认表单、计划授权表单，或失败后的重试按钮。后续步骤显示预览但使用 `disabled` 和“等待上一步确认”的文字，不允许跳步。

- [ ] **Step 4: 添加轮询与失效交互。**

当状态是 `preparing` 或 `recoverable_failure` 以外的运行中步骤时，每 2 秒刷新摘要并用 `afterSeq` 拉取事件；卸载时清除 timer。确认后刷新 view；收到 409 时重新加载并提示“此准备版本已更新，已显示最新内容”。Claim 修改导致下游 stale 时，页面展示“协议草案和补证计划已因原文核验变更失效，系统将仅重新生成受影响步骤”。

- [ ] **Step 5: 接入路由和创建跳转。**

在 `routes.tsx` 加：

```tsx
<Route path="events/:caseId/preparation" element={<ResearchPreparationPage />} />
```

`EventCreatePage.create` 在 Case 创建成功后，无论来源是粘贴文本还是上传文件，最终导航到 `/events/${created.caseId}/preparation`；上传材料完成后保留其 document 信息为 query 参数，但不导航到 documents 页面。

- [ ] **Step 6: 实现克制的响应式样式。**

在现有 `research-os-overrides.css` 追加独立 `.ros-preparation*` 规则，不重写用户已有 CSS。桌面使用 `minmax(0, 1fr) minmax(300px, 360px)`；窄屏 `@media (max-width: 840px)` 改为单列且右侧当前任务在前。错误、锁定和状态除颜色外必须使用文字；按钮焦点采用项目既有 outline token。

- [ ] **Step 7: 运行前端测试。**

Run: `cd frontend && npm test -- --run src/tests/ResearchPreparationPage.test.tsx src/tests/ResearchOsPages.test.tsx && npx tsc --noEmit`

Expected: PASS；新建跳转、准备中、三步确认、失败、窄屏和键盘焦点均被覆盖。

- [ ] **Step 8: 提交。**

```bash
git add frontend/src/features/events/ResearchPreparationPage.tsx frontend/src/app/routes.tsx frontend/src/features/events/EventCreatePage.tsx frontend/src/styles/research-os-overrides.css frontend/src/tests/ResearchPreparationPage.test.tsx frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "feat: add research preparation workbench"
```

## Task 10: Desk 路由、回到首个待办和正式研究交接

**Files:**

- Modify: `frontend/src/features/events/EventDeskPage.tsx`
- Modify: `frontend/src/domain/eventResearchPresentation.ts`
- Modify: `frontend/src/features/case/CasePages.tsx`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: 写 Desk RED 测试。**

```tsx
it("does not count preparing cases as human review but links ready preparation to its current step", async () => {
  mockClient.listEventResearch.mockResolvedValue([preparingEvent, claimsReviewEvent]);
  renderAt("/events");
  expect(screen.getByText("待你审核").parentElement).toHaveTextContent("1");
  expect(screen.getByRole("link", { name: /核验原文与候选陈述/ })).toHaveAttribute("href", "/events/case-ready/preparation");
});
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx -t "preparing cases"`

Expected: FAIL，因为现有 Desk 不认识新的 next action kind。

- [ ] **Step 3: 实现动作展示和路由。**

在 `eventResearchPresentation.ts` 对四个 `review_preparation_*`/`recover_preparation` action kind 返回：

```ts
{ href: `/events/${caseId}/preparation`, buttonLabel: "进入研究准备", role: "你需要做" }
```

`wait` 且 lifecycle 未启动 Run 时返回 Preparation 链接但不设置 `nextHumanAction`。`EventDeskPage` 的 `review` 数组只统计 action kind 不等于 `wait` 的 Preparation 状态。`CaseConclusionPage` 发现 `workbench.preparation` 非 `authorized` 时，将主任务卡入口改为 Preparation，且不再声称“系统正在核验证据”。

- [ ] **Step 4: 运行 Desk 与结论页回归。**

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npx tsc --noEmit`

Expected: PASS；系统工作、待用户确认、失败恢复和授权后正式研究的链接互不混淆。

- [ ] **Step 5: 提交。**

```bash
git add frontend/src/features/events/EventDeskPage.tsx frontend/src/domain/eventResearchPresentation.ts frontend/src/features/case/CasePages.tsx frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "feat: route research desk through preparation tasks"
```

## Task 11: 端到端、迁移和安全验收

**Files:**

- Create: `backend/tests/test_research_preparation_integration.py`
- Modify: `backend/tests/test_sqlite_migration_bootstrap.py`
- Modify: `frontend/src/tests/ResearchPreparationPage.test.tsx`
- Modify: `docs/superpowers/specs/2026-08-13-research-preparation-workbench-design.md`

- [ ] **Step 1: 写完整链路 RED 测试。**

```python
def test_live_compatible_fake_provider_prepares_then_explicit_authorization_starts_one_run(cmd_client, fake_openai_server):
    case_id = _create_case(cmd_client)
    _run_preparation_worker_until_idle(fake_openai_server)
    before = cmd_client.get(f"/api/v1/event-research/{case_id}/preparation").json()
    assert before["status"] == "awaiting_claim_review"
    assert before["research_run_id"] is None
    _confirm_all_three_human_steps(cmd_client, case_id)
    after = cmd_client.get(f"/api/v1/event-research/{case_id}/preparation").json()
    assert after["status"] == "authorized"
    assert _run_count(case_id) == 1
```

- [ ] **Step 2: 运行 RED 测试。**

Run: `cd backend && ./.venv/bin/pytest -q tests/test_research_preparation_integration.py`

Expected: FAIL until all prior tasks are integrated.

- [ ] **Step 3: Add integration fixtures without external credentials.**

Use a localhost OpenAI-compatible fake that asserts Authorization/model/JSON mode and returns the three strict response shapes. Set dummy endpoint/key only inside the test process. Do not read `.env`, do not call real LLM or Gildata, and assert the fake sees exactly three preparation calls before authorization.

- [ ] **Step 4: Add migration and crash-recovery coverage.**

```python
def test_0054_database_upgrades_to_preparation_schema_and_backfill_is_explicit(session): assert _upgrade_and_explicit_backfill(session)
def test_crashed_worker_job_is_reclaimed_without_duplicate_current_artifact(session): assert _reclaim_writes_one_current_artifact(session)
def test_two_sessions_authorize_same_plan_and_observe_one_research_run(pg_session_factory): assert _concurrent_authorization_writes_one_run(pg_session_factory)
```

The two-session test is PostgreSQL-only and must synchronize after both sessions see the ready plan, then assert one 201/one replay-or-409 and exactly one persisted Run.

- [ ] **Step 5: Run required verification.**

Run:

```bash
cd backend && ./.venv/bin/pytest -q \
  tests/test_research_preparation_models.py \
  tests/test_research_preparation_service.py \
  tests/test_research_preparation_generator.py \
  tests/test_research_preparation_worker.py \
  tests/test_research_preparation_backfill.py \
  tests/test_research_preparation_api.py \
  tests/test_research_preparation_integration.py \
  tests/test_event_research_api.py \
  tests/test_event_research_lifecycle.py \
  tests/test_research_protocol_api.py \
  tests/test_sqlite_migration_bootstrap.py
cd ../frontend && npm test -- --run src/tests/ResearchPreparationPage.test.tsx src/tests/ResearchOsPages.test.tsx src/tests/HttpResearchAdapter.test.ts src/tests/MockResearchAdapter.test.ts
cd ../backend && bash scripts/sync-contract.sh --check
cd ../frontend && npx tsc --noEmit
```

Expected: all selected tests pass; PostgreSQL-only tests may be skipped only when `TEST_DATABASE_URL` is absent.

- [ ] **Step 6: Run full checks and update the spec only with verified implementation notes.**

Run:

```bash
cd backend && ./.venv/bin/pytest -q
cd ../frontend && npm test -- --run
git diff --check
git status --short
```

Append to the design document a short “Implemented and verified” section containing only actual migration revision, test counts and any environment-gated PostgreSQL test. Do not amend product decisions.

- [ ] **Step 7: 提交。**

```bash
git add backend/tests/test_research_preparation_integration.py backend/tests/test_sqlite_migration_bootstrap.py frontend/src/tests/ResearchPreparationPage.test.tsx docs/superpowers/specs/2026-08-13-research-preparation-workbench-design.md
git commit -m "test: verify research preparation workflow"
```

## Plan self-review

### Spec coverage

- 独立准备域、三类 Artifact、完整日志：Task 1–2。
- 自动解析/协议/计划且不运行正式研究：Task 3–5。
- 有限重试、失败保留、人工重试、迟到输出：Task 2、4。
- 三次确认、正式协议门禁、唯一授权 Run：Task 6。
- 新 Case 和既有未启动 Case 回填：Task 4–5。
- Desk 与明确人机责任边界：Task 7、10。
- 新页面、状态、日志、窄屏和可访问性：Task 8–9。
- 租户、脱敏、合同、迁移和并发验收：Task 6–7、11。

### Placeholder scan

已扫描并移除所有未落地占位语句。每个代码变更都给出了固定路径、状态/方法名或结构化契约，每个任务均有 RED、GREEN 和提交步骤。

### Type consistency

`ResearchPreparation`、`ResearchPreparationArtifact`、`ResearchPreparationEvent`、`ResearchPreparationService`、`ResearchPreparationGenerator`、`ResearchPreparationProviderError`、三步名与六个聚合状态在所有任务中保持一致。授权以前 `research_run_id` 始终为 `None`，授权后由唯一幂等写入产生。
