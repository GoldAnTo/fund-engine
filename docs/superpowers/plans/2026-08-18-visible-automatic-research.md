# 可见自动研究流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持主题、粘贴材料和支持类型文件的一键自动研究，并在创建、过程、详情和结果中展示可解释的进展、异常、预计时间和关键因素判定。

**Architecture:** `AutomaticResearchIntakeService` 增加文件入口并复用既有原件冻结服务。后端把经验证的 Run、Job、事件和评估投影为一个面向用户的进展 DTO，前端只消费该 DTO，不推断内部 Worker 状态。关键因素判断冻结在 AI assessment 的结构化判定中，结果投影再以证据与反证验证后展示。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy/Alembic、React、TypeScript、Vitest、pytest。

---

## 文件结构

- `backend/app/schemas/v1/automatic_research.py`：上传启动、进展叙述、活动、例外和因素判定的公开 DTO。
- `backend/app/api/v1/automatic_research.py`：JSON 启动与 multipart 文件启动端点。
- `backend/app/services/automatic_research_intake.py`：文本和文件输入的事务性 Case/Run 创建。
- `backend/app/models/ledger.py`、`backend/alembic/versions/0060_*.py`：不可变 assessment 的结构化因素判定。
- `backend/app/ai/assessment_gen.py`、`backend/app/services/assessment.py`：要求模型返回并冻结因素判定。
- `backend/app/services/automatic_research_projection.py`：纯粹的用户进展、活动聚合、ETA、异常翻译和因素投影。
- `backend/app/queries/automatic_research.py`：装载已验证数据并调用投影。
- `frontend/src/domain/automaticResearch.ts`、`frontend/src/data/httpResearchAdapter.ts`：严格 DTO 映射和上传 client。
- `frontend/src/features/events/EventCreatePage.tsx`：统一文字/粘贴/文件入口。
- `frontend/src/features/events/AutomaticResearchPage.tsx`、`AutomaticResearchSections.tsx`：摘要、阶段计数、技术详情和因素依据。

### Task 1: 文件启动契约与原子性

**Files:**

- Modify: `backend/app/schemas/v1/automatic_research.py`
- Modify: `backend/app/api/v1/automatic_research.py`
- Modify: `backend/app/services/automatic_research_intake.py`
- Test: `backend/tests/test_automatic_research_api.py`
- Test: `backend/tests/test_automatic_research_intake.py`

- [ ] **Step 1: 写失败的 multipart API 测试**

```python
def test_start_uploaded_automatic_research_freezes_pdf_and_creates_automatic_run(client, tenant_headers):
    response = client.post(
        "/v1/automatic-research/uploaded",
        headers=tenant_headers,
        data={"input": "判断公告对供应链的影响"},
        files={"file": ("notice.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert_original_document_is_frozen_for_response_run(response.json())

def test_uploaded_start_rejects_unsupported_file_without_case_or_run(client, tenant_headers):
    response = client.post(
        "/v1/automatic-research/uploaded",
        headers=tenant_headers,
        files={"file": ("sheet.xlsx", b"bytes", "application/vnd.ms-excel")},
    )
    assert response.status_code == 422
    assert_case_and_run_counts_are_unchanged()
```

- [ ] **Step 2: 运行失败测试**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_automatic_research_api.py -k uploaded`

Expected: FAIL，因为 `/uploaded` 端点和 service 方法不存在。

- [ ] **Step 3: 实现 multipart 入口和 service 方法**

```python
@router.post("/uploaded", response_model=AutomaticResearchStartResponse, status_code=201)
def start_uploaded_automatic_research(
    input: str = Form(""),
    file: UploadFile = File(),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchStartResponse:
    raw = file.file.read()
    started = AutomaticResearchIntakeService(db).start_uploaded(
        raw_input=input, raw=raw, file_name=file.filename or "", mime_type=file.content_type or "",
        tenant_id=tenant_id,
    )
    return AutomaticResearchStartResponse(
        case_id=started.case_id, run_id=started.run_id, status="queued"
    )
```

`start_uploaded()` 必须把文本为空时的研究标题回退为文件名；调用 `EventResearchService.create(payload, tenant_id=tenant_id, workflow_mode="automatic", initial_uploaded_original=initial_uploaded_original)`；只接受 PDF、TXT、Markdown、CSV；任何验证或冻结失败都 `rollback()`，不留下 Case、Run 或 DocumentVersion。

- [ ] **Step 4: 运行 API 和 intake 回归**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_automatic_research_api.py backend/tests/test_automatic_research_intake.py backend/tests/test_document_upload_artifacts.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/v1/automatic_research.py backend/app/api/v1/automatic_research.py backend/app/services/automatic_research_intake.py backend/tests/test_automatic_research_api.py backend/tests/test_automatic_research_intake.py
git commit -m "feat: start automatic research from uploaded material"
```

### Task 2: 冻结的关键因素判定

**Files:**

- Modify: `backend/app/models/ledger.py`
- Create: `backend/alembic/versions/0060_add_ai_assessment_factor_judgement.py`
- Modify: `backend/app/ai/assessment_gen.py`
- Modify: `backend/app/services/assessment.py`
- Modify: `backend/app/repositories/research.py`
- Test: `backend/tests/test_ai_engine.py`
- Test: `backend/tests/test_automatic_research_pipeline.py`

- [ ] **Step 1: 写因素判定的失败测试**

```python
def test_automatic_assessment_requires_structured_factor_judgement(session, thesis, evidence):
    generated = AssessmentGenerator(fake_client({
        "conclusion": "supported", "rationale": "该因素直接改变供给并有多条来源支持。", "gaps": [],
        "factor_judgement": {"relevance": "direct", "causal_impact": "high", "evidence_strength": "strong", "counter_evidence": "none"},
    })).generate(thesis.id, cutoff, session)
    assert generated.factor_judgement["classification"] == "key"

def test_contradicted_or_insufficient_factor_cannot_be_key(session, thesis, evidence):
    judgement = generate_assessment_for_test(
        session=session, thesis=thesis, evidence=evidence,
        conclusion="insufficient_evidence",
    ).factor_judgement
    assert judgement["classification"] in {"pending", "excluded"}
```

- [ ] **Step 2: 运行失败测试**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_engine.py -k factor_judgement`

Expected: FAIL，因为 assessment 尚无 `factor_judgement`。

- [ ] **Step 3: 添加不可变 JSON 字段和迁移**

```python
factor_judgement: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
```

迁移 `0060` 为已有行保留 `NULL`。自动 assessment 必须写入固定键：`relevance`、`causal_impact`、`evidence_strength`、`counter_evidence`、`classification`、`ranking_reason`。验证规则：只有 `conclusion == "supported"`、直接相关、高因果影响、强证据且无实质反证时才允许 `classification == "key"`；其余只能为 `secondary`、`pending` 或 `excluded`。更新模型 schema hint、`AssessmentService.create_ai_assessment()` 和 repository 参数，使该 JSON 与 snapshot 同时冻结。

- [ ] **Step 4: 运行迁移和因素回归**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_engine.py backend/tests/test_automatic_research_pipeline.py backend/tests/test_sqlite_migration_bootstrap.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/ledger.py backend/alembic/versions/0060_add_ai_assessment_factor_judgement.py backend/app/ai/assessment_gen.py backend/app/services/assessment.py backend/app/repositories/research.py backend/tests/test_ai_engine.py backend/tests/test_automatic_research_pipeline.py
git commit -m "feat: freeze evidence-based factor judgements"
```

### Task 3: 面向用户的进展和因素投影

**Files:**

- Modify: `backend/app/schemas/v1/automatic_research.py`
- Modify: `backend/app/services/automatic_research_projection.py`
- Modify: `backend/app/queries/automatic_research.py`
- Test: `backend/tests/test_automatic_research_projection.py`
- Test: `backend/tests/test_automatic_research_api.py`

- [ ] **Step 1: 写纯投影失败测试**

```python
def test_progress_aggregates_repeated_extract_events_and_exposes_next_action():
    progress = project_automatic_research_progress(
        status="running", run=run, jobs=jobs, run_events=run_events,
        acquisition_events=events, admitted_evidence_count=0,
        exception_reason_codes=[], now=now,
    )
    assert progress.narrative.current_action == "正在解析已找到的来源"
    assert progress.narrative.completed_count == 8
    assert progress.narrative.total_count == 12
    assert progress.narrative.next_action == "随后校验证据是否可引用"
    assert progress.activities[0].label == "正在解析 8 个来源"
    assert len(progress.activities[0].technical_details) == 8

def test_factor_projection_refuses_legacy_or_invalid_key_judgement():
    factor = project_factor_judgement(
        statement="需求变化", assessment=legacy_assessment, links=[],
    )
    assert factor.classification == "pending"
    assert factor.is_key is False
```

- [ ] **Step 2: 运行失败测试**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_automatic_research_projection.py -k 'narrative or factor'`

Expected: FAIL，因为公开 DTO 尚无 narrative、聚合活动和 factor 判定。

- [ ] **Step 3: 实现 DTO 与纯投影**

```python
class AutomaticResearchNarrativeDTO(V1Model):
    current_action: str
    completed_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    next_action: str
    elapsed_seconds: int = Field(ge=0)
    estimated_remaining_seconds_min: int | None = Field(default=None, ge=0)
    estimated_remaining_seconds_max: int | None = Field(default=None, ge=0)

class AutomaticResearchFactorDTO(V1Model):
    statement: str
    classification: Literal["key", "secondary", "pending", "excluded"]
    ranking_reason: str
    support_count: int = Field(ge=0)
    counter_evidence_count: int = Field(ge=0)
    evidence_gap: str | None
```

在 `project_automatic_research_progress()` 中按已验证 job 状态计算总数、完成数和下一动作；只在同一 Run 已有至少两个已完成项目时，使用完成项目的中位耗时乘以待处理量给出范围。将 acquisition 事件按“用户动作 + 阶段”聚合，技术明细保留事件时间、来源任务和内部状态。例外 DTO 增加 `impact` 和 `system_action`，将 `admit` 等内部值完全留在技术明细。查询层只传入当前 frozen scope 的 jobs、links、assessments，禁止跨 Run 数据。

- [ ] **Step 4: 运行投影/API 回归**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_automatic_research_projection.py backend/tests/test_automatic_research_api.py backend/tests/test_automatic_research_pipeline.py`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/v1/automatic_research.py backend/app/services/automatic_research_projection.py backend/app/queries/automatic_research.py backend/tests/test_automatic_research_projection.py backend/tests/test_automatic_research_api.py
git commit -m "feat: expose visible automatic research progress"
```

### Task 4: 前端上传入口与严格映射

**Files:**

- Modify: `frontend/src/domain/automaticResearch.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/features/events/EventCreatePage.tsx`
- Modify: `frontend/src/styles/automatic-research.css`
- Test: `frontend/src/tests/EventCreatePage.test.tsx`
- Test: `frontend/src/tests/AutomaticResearchAdapter.test.ts`

- [ ] **Step 1: 写失败的页面和 adapter 测试**

```tsx
it("starts from an accepted PDF and keeps its selected file while an upload fails", async () => {
  render(<EventCreatePage />)
  await user.upload(screen.getByLabelText("上传研究材料"), new File(["pdf"], "notice.pdf", { type: "application/pdf" }))
  await user.click(screen.getByRole("button", { name: "开始自动研究" }))
  expect(researchClient.startAutomaticResearchWithFile).toHaveBeenCalledWith(expect.objectContaining({ file: expect.any(File) }))
})

it("rejects a second file and unsupported extension before submitting", async () => {
  await user.upload(screen.getByLabelText("上传研究材料"), [
    new File(["one"], "one.pdf", { type: "application/pdf" }),
    new File(["two"], "two.txt", { type: "text/plain" }),
  ])
  expect(screen.getByRole("alert")).toHaveTextContent("一次只能上传一个文件")
})
```

- [ ] **Step 2: 运行失败测试**

Run: `npm test -- --run src/tests/EventCreatePage.test.tsx src/tests/AutomaticResearchAdapter.test.ts`

Expected: FAIL，因为 client 和页面尚无文件入口。

- [ ] **Step 3: 实现 client、mapper 和创建页**

```ts
export interface AutomaticResearchStartFileInput { input: string; file: File }
startAutomaticResearchWithFile(input: AutomaticResearchStartFileInput): Promise<AutomaticResearchStart>
```

HTTP adapter 用 `FormData` 提交到 `/automatic-research/uploaded`，不手写 `Content-Type`。页面只允许一个文件，接受 `.pdf,.txt,.md,.markdown,.csv`，显示文件名/大小/移除按钮和“将作为本次研究原始材料”说明；无文件时仍走原文本端点。上传选择、系统错误、busy、键盘焦点和 `aria-live` 状态都必须保持可访问。

- [ ] **Step 4: 运行前端回归与类型检查**

Run: `npm test -- --run src/tests/EventCreatePage.test.tsx src/tests/AutomaticResearchAdapter.test.ts && npm run typecheck`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/domain/automaticResearch.ts frontend/src/data/researchClient.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/features/events/EventCreatePage.tsx frontend/src/styles/automatic-research.css frontend/src/tests/EventCreatePage.test.tsx frontend/src/tests/AutomaticResearchAdapter.test.ts
git commit -m "feat: accept uploaded automatic research material"
```

### Task 5: 清晰过程、技术详情和关键因素界面

**Files:**

- Modify: `frontend/src/domain/automaticResearch.ts`
- Modify: `frontend/src/features/events/AutomaticResearchPage.tsx`
- Modify: `frontend/src/features/events/AutomaticResearchSections.tsx`
- Modify: `frontend/src/features/events/useAutomaticResearchProgress.ts`
- Modify: `frontend/src/styles/automatic-research.css`
- Test: `frontend/src/tests/AutomaticResearchPage.test.tsx`
- Test: `frontend/src/domain/automaticResearch.test.ts`

- [ ] **Step 1: 写失败的过程页测试**

```tsx
it("states current work, completed scope, next action and an honest ETA", async () => {
  renderProcess(runningView({ narrative: { currentAction: "正在解析已找到的来源", completedCount: 8, totalCount: 12, nextAction: "随后校验证据是否可引用", estimatedRemainingSecondsMin: 120, estimatedRemainingSecondsMax: 300 } }))
  expect(screen.getByText("正在解析已找到的来源")).toBeVisible()
  expect(screen.getByText("已完成 8/12 条")).toBeVisible()
  expect(screen.getByText("随后校验证据是否可引用")).toBeVisible()
  expect(screen.getByText("预计还需约 2–5 分钟")).toBeVisible()
})

it("keeps raw extracting events inside technical details and aggregates the default activity", () => {
  renderProcess(runningView({ activities: [aggregatedExtractingActivity] }))
  expect(screen.getByText("正在解析 8 个来源")).toBeVisible()
  expect(screen.queryByText("extracting")).not.toBeInTheDocument()
  await user.click(screen.getByText("查看技术详情"))
  expect(screen.getByText("extracting")).toBeVisible()
})

it("shows an unconfirmed factor instead of calling the first supported factor key", () => {
  renderProcess(completedView({ factors: [pendingSupportedFactor] }))
  expect(screen.getByText("尚未确认关键因素")).toBeVisible()
})
```

- [ ] **Step 2: 运行失败测试**

Run: `npm test -- --run src/tests/AutomaticResearchPage.test.tsx src/domain/automaticResearch.test.ts`

Expected: FAIL，因为 view 没有 narrative、visible activities 或 factor 判定。

- [ ] **Step 3: 实现过程摘要和渐进披露**

在页面标题下增加一个非卡片化摘要区域，按顺序呈现当前动作、完成量、下一步、已用时和 ETA/“正在估算”。五阶段行显示真实的阶段计数和摘要。`查看过程` 默认显示聚合活动与用户例外说明；单独的 `技术详情` disclosure 才显示 `extracting`、job 时间和原始阶段。结果页以清单展示因素的分类、证据数、反证数、判定依据与证据缺口；关键因素只使用已验证 `classification == "key"` 的数据。

保持现有轮询的最后可信 view、退避和重新读取行为；新增 DTO 的 runtime validator 必须 fail closed，避免 malformed 投影误导用户。为动态摘要设置 `aria-live="polite"` 与原子播报。

- [ ] **Step 4: 运行流程页回归、全前端测试和类型检查**

Run: `npm test -- --run src/tests/AutomaticResearchPage.test.tsx src/domain/automaticResearch.test.ts && npm test && npm run typecheck`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/domain/automaticResearch.ts frontend/src/features/events/AutomaticResearchPage.tsx frontend/src/features/events/AutomaticResearchSections.tsx frontend/src/features/events/useAutomaticResearchProgress.ts frontend/src/styles/automatic-research.css frontend/src/tests/AutomaticResearchPage.test.tsx frontend/src/domain/automaticResearch.test.ts
git commit -m "feat: explain automatic research work and factors"
```

### Task 6: 端到端和运行时验收

**Files:**

- Modify: `frontend/e2e/automatic-research.spec.ts`
- Modify: `README.md`
- Test: `backend/tests/test_automatic_research_api.py`

- [ ] **Step 1: 写失败的 Playwright 验收**

```ts
test("researcher starts with a PDF and understands the visible process", async ({ page }) => {
  await page.goto("/events/new")
  await page.getByLabel("上传研究材料").setInputFiles("e2e/fixtures/sample-material.pdf")
  await page.getByRole("button", { name: "开始自动研究" }).click()
  await expect(page.getByText(/正在做什么|已完成|下一步/)).toBeVisible()
  await expect(page.getByText("查看技术详情")).toBeVisible()
})
```

- [ ] **Step 2: 运行失败验收**

Run: `npm run e2e -- --grep "PDF and understands"`

Expected: FAIL，因为上传入口和可见进展尚未完成。

- [ ] **Step 3: 补齐 mock fixture、README 和验收路径**

Mock 必须提供文件启动、聚合活动、ETA、因素分类和失败/待确认场景。README 说明支持的文件类型、估时为区间以及自动因素的审核边界。

- [ ] **Step 4: 运行最终验证**

Run: `backend/.venv/bin/python -m pytest -q backend && npm test && npm run typecheck && npm run e2e`

Expected: 全部 PASS；记录环境跳过项。

- [ ] **Step 5: 提交**

```bash
git add frontend/e2e/automatic-research.spec.ts frontend/e2e/fixtures/sample-material.pdf frontend/src/data/mockResearchAdapter.ts README.md
git commit -m "test: cover visible automatic research flow"
```

## 自检

- 规格中的三类输入由 Task 1 和 Task 4 覆盖。
- 当前动作、完成量、下一步、诚实 ETA、活动聚合、异常翻译和技术详情由 Task 3 和 Task 5 覆盖。
- 关键因素四道判定、分类、排序与无确认状态由 Task 2、Task 3 和 Task 5 覆盖。
- 所有 Case/Run/Job 数据仍经过 frozen scope 与当前运行边界验证。
- 没有保留占位符、模糊的“适当处理”或未定义的接口名称。
