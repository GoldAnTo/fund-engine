# 来源类别与后继运行恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让以粘贴或上传方式冻结的公司披露按其声明的研究来源类别进入后继运行，并在原子陈述复核后原位恢复被冻结的运行。

**Architecture:** `SourceContract.source_type` 保持接入方式，新增不可变的 `research_source_type` 作为研究范围匹配键；历史合同回填为原接入方式。所有会根据 `allowed_source_types` 选择材料的路径统一读取新字段。已发布 Case 的表单把两个概念分别呈现，并只允许带 HTTP(S) 来源链接的材料声明为公司披露。

**Tech Stack:** FastAPI/Pydantic、SQLAlchemy/Alembic、pytest、React/TypeScript、Vitest/Testing Library。

---

## File structure

- `backend/alembic/versions/0051_source_contract_research_type.py`：为不可变合同新增列并以已有接入方式回填历史数据。
- `backend/app/models/source_governance.py`：持久化新的研究来源类别，并给直接创建 ORM fixture 的旧调用提供与 `source_type` 相同的写入默认值。
- `backend/app/services/source_governance.py`：集中验证类别、HTTP(S) 公司披露链接以及去重合同兼容性。
- `backend/app/services/auto_research.py`、`backend/app/services/recall.py`：以研究来源类别而非接入方式执行冻结范围过滤。
- `backend/app/schemas/v1/documents.py`、`backend/app/queries/documents.py`：在审计读取中同时返回接入方式和研究类别。
- `backend/tests/test_event_source_governance.py`、`backend/tests/test_auto_research_api.py`、`backend/tests/test_atomic_claims_api.py`、`backend/tests/test_sqlite_migration_bootstrap.py`：覆盖合同、迁移、范围过滤和同一 run 恢复。
- `frontend/src/domain/eventResearch.ts`：声明研究类别类型，保留现有 `EventSourceType` 作为接入方式。
- `frontend/src/features/case/CasePages.tsx`：为已发布 Case 新材料表单加入研究类别输入、链接约束和提交 metadata。
- `frontend/src/domain/types.ts`、`frontend/src/data/mockResearchAdapter.ts`：让前端审计模型和 mock 合同保留该类别。
- `frontend/src/tests/ResearchOsPages.test.tsx`：验证真实表单动作和传输 payload。

### Task 1: 冻结合同的研究来源类别与迁移

**Files:**

- Create: `backend/alembic/versions/0051_source_contract_research_type.py`
- Modify: `backend/app/models/source_governance.py:20-42`
- Modify: `backend/app/services/source_governance.py:14-190`
- Modify: `backend/app/schemas/v1/documents.py:38-57`
- Modify: `backend/app/queries/documents.py:414-446`
- Modify: `backend/tests/test_event_source_governance.py`
- Modify: `backend/tests/test_sqlite_migration_bootstrap.py:20-39,86-103`

- [ ] **Step 1: 写入失败的合同与迁移测试**

```python
def test_company_disclosure_declaration_requires_http_source_url(cmd_client) -> None:
    response = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_url="event://pasted-news",
            source_metadata={"research_source_type": "company_disclosure"},
        ),
    )
    assert response.status_code == 422
    assert "company_disclosure requires an HTTP(S) source_url" in response.json()["error"]["message"]


def test_source_contract_keeps_access_method_separate_from_research_category(cmd_client, cmd_session) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json=_event_payload(
            source_type="pasted_snapshot",
            source_url="https://static.cninfo.com.cn/finalpage/report.pdf",
            source_metadata={"research_source_type": "company_disclosure"},
        ),
    )
    assert created.status_code == 201
    contract = cmd_session.scalar(select(SourceContract))
    assert contract.source_type == "pasted_snapshot"
    assert contract.research_source_type == "company_disclosure"
```

Extend the SQLite bootstrap assertion to expect Alembic revision `0051`, inspect `source_contracts`, and assert `research_source_type` is non-null. Add a small upgrade fixture at revision `0050`, insert a contract with `source_type='licensed_provider'`, upgrade to head, and assert its new value is `licensed_provider`.

- [ ] **Step 2: 运行测试，确认它们先失败**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_event_source_governance.py -q
backend/.venv/bin/pytest backend/tests/test_sqlite_migration_bootstrap.py -q
```

Expected: the contract has no `research_source_type`, the HTTP(S) validation is absent, and the expected head revision is not `0051`.

- [ ] **Step 3: 实现列、类别规范化和审计 DTO**

Add this constant and normalizer near `USER_CONTROLLED_TYPES` in `source_governance.py`:

```python
RESEARCH_SOURCE_TYPES = frozenset({
    "pasted_snapshot", "uploaded_file", "licensed_provider",
    "public_url", "company_disclosure",
})


def research_source_type_for_intake(
    *, source_type: str, source_metadata: dict[str, Any] | None, source_url: str | None
) -> str:
    declared = str((source_metadata or {}).get("research_source_type") or source_type).strip()
    if declared not in RESEARCH_SOURCE_TYPES:
        raise ValueError("research_source_type is not supported")
    if declared == "company_disclosure":
        parsed = urlparse((source_url or "").strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("company_disclosure requires an HTTP(S) source_url")
    return declared
```

Pass `document.source_url` into this normalizer in `record_event_intake`; write the resulting string to `SourceContract.research_source_type`; compare it in `_assert_existing_contract_compatible`. Add `research_source_type` to `SourceContractDTO` and `DocumentQueries._source_contract_dto` without renaming `source_type`.

Define the model column with a context default so direct fixture constructors remain backwards compatible:

```python
research_source_type: Mapped[str] = mapped_column(
    String(32),
    nullable=False,
    default=lambda context: context.get_current_parameters()["source_type"],
)
```

Create revision `0051` from `0050`: add the column nullable, temporarily drop the PostgreSQL immutability trigger if present, `UPDATE source_contracts SET research_source_type = source_type`, change the column to non-null (using `batch_alter_table` on SQLite), then recreate the immutable trigger. The downgrade drops only the new column.

- [ ] **Step 4: 运行合同和迁移测试，确认通过**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_event_source_governance.py backend/tests/test_sqlite_migration_bootstrap.py -q
```

Expected: PASS; the migration is replayable from an empty SQLite database and preserves every legacy contract's existing source type as its category.

- [ ] **Step 5: 提交合同与迁移切片**

```bash
git add backend/alembic/versions/0051_source_contract_research_type.py backend/app/models/source_governance.py backend/app/services/source_governance.py backend/app/schemas/v1/documents.py backend/app/queries/documents.py backend/tests/test_event_source_governance.py backend/tests/test_sqlite_migration_bootstrap.py
git commit -m "feat: separate source category from intake method"
```

### Task 2: 让冻结范围和原子陈述门禁匹配研究类别

**Files:**

- Modify: `backend/app/services/auto_research.py:937-1030`
- Modify: `backend/app/services/recall.py:351-368`
- Modify: `backend/tests/test_auto_research_api.py:123-216`

- [ ] **Step 1: 写入冻结范围的失败测试**

In `test_monitor_run_extracts_only_the_frozen_allowed_source_types`, change the second fixture into an official report that arrived by paste:

```python
pasted_disclosure = DocumentVersion(
    content_sha256=uuid.uuid4().hex,
    source_url="https://static.cninfo.com.cn/finalpage/report.pdf",
    available_at=now,
    acquired_at=now,
    parser_version="user-pasted-v1",
)
# The access method stays pasted_snapshot; the research category is company_disclosure.
SourceContract(
    document_version_id=pasted_disclosure.id,
    source_type="pasted_snapshot",
    research_source_type="company_disclosure",
    ...,
)
```

Assert `extracted == [disclosure.id, pasted_disclosure.id]`. Keep a third `pasted_snapshot` contract whose `research_source_type` is `pasted_snapshot`, and assert that it is the only excluded document. Add a direct `_pending_atomic_claims(..., allowed_source_types={"company_disclosure"})` assertion which returns candidates from the pasted official report and excludes the ordinary pasted snapshot.

- [ ] **Step 2: 运行测试，确认它们先失败**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_auto_research_api.py::test_monitor_run_extracts_only_the_frozen_allowed_source_types -q
```

Expected: FAIL because the worker still compares `contract.source_type` to `allowed_source_types`.

- [ ] **Step 3: 将所有研究范围过滤改为研究类别**

Change the matching predicates, leaving event payload key names stable:

```python
elif contract.research_source_type not in allowed_source_types:
    reason = "source_type_not_in_frozen_scope"

...
and contract.research_source_type in allowed_source_types
```

Apply the equivalent change in `RecallService` where `allowed_source_types` constrains `SourceStatement` candidates. Do not alter permission, tenant, `available_at`, or active-contract checks; they remain independent boundaries.

- [ ] **Step 4: 运行范围测试，确认通过**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_auto_research_api.py backend/tests/test_recall.py -q
```

Expected: PASS; company-disclosure scope includes an official linked report pasted by a researcher but excludes an ordinary paste.

- [ ] **Step 5: 提交范围过滤切片**

```bash
git add backend/app/services/auto_research.py backend/app/services/recall.py backend/tests/test_auto_research_api.py
git commit -m "fix: match research runs by source category"
```

### Task 3: 原子陈述审核后原位恢复冻结运行

**Files:**

- Modify: `backend/tests/test_atomic_claims_api.py:126-174`
- Modify: `backend/app/services/auto_research.py:248-295` only if the failing test exposes a missing guard
- Modify: `backend/app/repositories/auto_research.py:178-218` only if the failing test exposes a missing state transition

- [ ] **Step 1: 写入同一运行与冻结范围的失败测试**

Extend `test_atomic_claim_review_requeues_the_paused_research_run` to start the original run with a real frozen scope and persist it before pausing:

```python
run = service.start(
    case.id,
    trigger="material_continuation",
    allowed_source_types=["company_disclosure"],
    scope_context={"source_document_version_id": str(document.id)},
)
...
scope_events = list(session.scalars(
    select(ResearchRunEvent)
    .where(ResearchRunEvent.run_id == run.id)
    .where(ResearchRunEvent.stage == "scope")
))
assert len(scope_events) == 1
assert scope_events[0].payload_json["allowed_source_types"] == ["company_disclosure"]
assert not session.scalar(
    select(ResearchRun).where(
        ResearchRun.research_case_id == case.id,
        ResearchRun.trigger == "manual",
    )
)
```

After the review API call, assert the only resumed run is `run.id`, its job is queued, and the completion event has `payload_json["candidate_id"] == str(candidate.id)`.

- [ ] **Step 2: 运行测试，确认它们先失败**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_atomic_claims_api.py::test_atomic_claim_review_requeues_the_paused_research_run -q
```

Expected: FAIL only if recovery creates another run, drops the frozen scope, or fails to retain the original run event. If it already passes, record that the live defect's empty manual runs come from a different caller before changing production code.

- [ ] **Step 3: 只修复被测试证实的恢复断点**

Keep `AutoResearchService.resume_after_atomic_claim_review` as the sole resume entry point. If a defect is demonstrated, make `AutoResearchRepository.resume_after_claim_review` requeue the existing job and blocked tasks on the supplied `run`; do not invoke `start`, `start_from_monitor`, or any path that can create a `trigger="manual"` run. Keep the event payload:

```python
{
    "candidate_id": str(candidate_id),
    "reviewer": reviewer,
    "resume_from_round": run.round + 1,
}
```

This task deliberately does not invent a new recovery mechanism when the existing repository method already meets the contract.

- [ ] **Step 4: 运行恢复与回归测试，确认通过**

Run:

```bash
backend/.venv/bin/pytest backend/tests/test_atomic_claims_api.py backend/tests/test_auto_research_api.py -q
```

Expected: PASS; no empty-scope manual run is produced by the atomic-claim review path.

- [ ] **Step 5: 提交已验证的恢复切片**

```bash
git add backend/app/services/auto_research.py backend/app/repositories/auto_research.py backend/tests/test_atomic_claims_api.py
git commit -m "fix: resume the original frozen run after claim review"
```

### Task 4: 已发布 Case 表单的分类声明和端到端验证

**Files:**

- Modify: `frontend/src/domain/eventResearch.ts:10-16`
- Modify: `frontend/src/domain/types.ts:352-366`
- Modify: `frontend/src/data/mockResearchAdapter.ts:4254-4310`
- Modify: `frontend/src/features/case/CasePages.tsx:717-1011`
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx:1451-1580`
- Modify: `backend/tests/test_event_research_scope.py:1879-2025`

- [ ] **Step 1: 写入前端和发布材料 API 的失败测试**

Add a UI test that selects company disclosure while keeping paste as the access method:

```tsx
await user.selectOptions(
  screen.getByLabelText("新增材料研究来源类别"),
  "company_disclosure",
);
expect(screen.getByText("公司披露必须提供可复核的 HTTP(S) 来源链接")).toBeVisible();
expect(screen.getByRole("button", { name: "冻结材料并纳入重新复核" })).toBeDisabled();
await user.type(screen.getByPlaceholderText("https://…"), "https://static.cninfo.com.cn/finalpage/report.pdf");
await user.click(screen.getByRole("button", { name: "冻结材料并纳入重新复核" }));
await waitFor(() => expect(decide).toHaveBeenCalledWith(expect.objectContaining({
  sourceType: "pasted_snapshot",
  sourceMetadata: expect.objectContaining({ research_source_type: "company_disclosure" }),
})));
```

Add a backend published-material decision test with the same body/link/category. Assert `201`, a `material_continuation` scope has `allowed_source_types == ["company_disclosure"]`, and the stored contract has `source_type == "pasted_snapshot"` plus `research_source_type == "company_disclosure"`. Keep the published conclusion assertion unchanged.

- [ ] **Step 2: 运行测试，确认它们先失败**

Run:

```bash
cd frontend && npm test -- ResearchOsPages.test.tsx
cd ../backend && .venv/bin/pytest tests/test_event_research_scope.py -q
```

Expected: the form has no category selector/payload, and the backend contract cannot report the separate category.

- [ ] **Step 3: 实现表单和 mock 的最小改动**

Add this frontend type beside `EventSourceType`:

```ts
export type ResearchSourceType = EventSourceType | "company_disclosure";
```

In `PublishedMaterialDecisionForm`, add `researchSourceType` state initialized from the current `sourceType`; reset it to the newly selected access method in `changeSourceType`. Render a labelled select `新增材料研究来源类别` with the four compatible categories plus `company_disclosure`. Compute:

```ts
const companyDisclosureNeedsUrl = researchSourceType === "company_disclosure";
const baseSourceReady = sourceType === "licensed_provider"
  ? Boolean(providerName.trim() && providerRecordId.trim() && providerRequestScope.trim())
  : sourceType === "public_url"
    ? Boolean(sourceUrl.trim())
    : true;
const sourceReady = baseSourceReady && (!companyDisclosureNeedsUrl || /^https?:\/\/\S+$/i.test(sourceUrl.trim()));
```

Add `research_source_type: researchSourceType` to `materialMetadata`, the explanatory text, and the disabled predicate. Extend the document contract type and mock adapter so audit data retains `research_source_type`; keep the existing `source_type` display as the access method.

- [ ] **Step 4: 运行前后端、类型检查与构建**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_event_source_governance.py tests/test_event_research_scope.py tests/test_auto_research_api.py tests/test_atomic_claims_api.py tests/test_sqlite_migration_bootstrap.py -q
cd ../frontend && npm test -- ResearchOsPages.test.tsx && npm run typecheck && npm run build
```

Expected: all selected tests, TypeScript check, and production build pass.

- [ ] **Step 5: 在隔离端口复跑工业富联真实人工流程**

Copy the existing acceptance SQLite file before writing to it, upgrade the copy to Alembic head, and start this worktree's API and worker on unused ports. Submit the official 2025 annual report as `pasted_snapshot + company_disclosure` with its CNInfo HTTP(S) URL; choose `reopen`; manually review the generated atomic claim; then inspect run events and conclusion history.

Acceptance evidence:

```text
SourceContract.source_type == "pasted_snapshot"
SourceContract.research_source_type == "company_disclosure"
material_continuation scope.allowed_source_types == ["company_disclosure"]
source_scope has no exclusion for the submitted annual-report document
atomic review requeues the same material_continuation run ID
the historical 2024 human-published conclusion remains present in /history
```

Do not treat an AI assessment as publication; the rerun ends at a human-reviewable state unless a reviewer explicitly publishes a new conclusion.

- [ ] **Step 6: 提交前验证并提交 UI 与端到端切片**

Run:

```bash
git diff --check
git status --short
git log --oneline main..HEAD
git add frontend/src/domain/eventResearch.ts frontend/src/domain/types.ts frontend/src/data/mockResearchAdapter.ts frontend/src/features/case/CasePages.tsx frontend/src/tests/ResearchOsPages.test.tsx backend/tests/test_event_research_scope.py
git commit -m "feat: declare research source category for successor material"
```

Then rerun the commands from Step 4 and record the actual output in the handoff.
