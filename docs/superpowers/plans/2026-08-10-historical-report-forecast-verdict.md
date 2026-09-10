# 历史研报预测 Verdict 单案例实施计划

> **执行约束：** 按 `superpowers:executing-plans` 逐项实施；每步先写失败测试，再写最小实现。

**目标：** 把火星人（300894.SZ）一份历史研报的 2023 年归母净利润预测，连同同页 2022 年基线、2023 年年报实际值、可重放的数值判据、人审 verdict，以及股票/基金历史披露下钻，做成一个真实 HTTP 可验证的单案例闭环。

**架构：** 新增一个狭窄的 forecast-verdict 领域模块。它以已审核、Case 内已准入的 `SourceStatement` 为边界，冻结量化 target 和 actual observation；纯函数只产生不可见的机器候选；人审新增 verdict 才能进入 `MarketExpression` 的关键因素读模型。已有 `MarketInstrumentBinding`、`FundamentalImpact`、`HoldingDisclosure` 保持原职责，承接股票与基金下钻，绝不把历史持仓写成实时暴露或收益结论。

**技术栈：** FastAPI、SQLAlchemy、Alembic、Pydantic v2、React/TypeScript、Vitest、pytest。

---

## 1. 建立不可变 forecast-verdict 账本与迁移

**Files:**

- Modify: `backend/app/models/research_expression.py`
- Modify: `backend/app/models/ledger.py`
- Create: `backend/alembic/versions/0038_forecast_verdicts.py`
- Test: `backend/tests/test_forecast_verdicts.py`

**Step 1: 写失败模型测试。**

覆盖以下事实：四张新表由 `Base.metadata.create_all` 建出；`ForecastTargetVersion` 关联同 Case 的 reviewed `ReportClaim` 与 `KeyFactor`，冻结 2022 baseline、2023 forecast period、数值、单位、比较符和容差；`ActualMetricObservation` 关联冻结实际来源和 `available_at`；candidate/verdict 均是 append-only，且新增表名在 `IMMUTABLE_TABLES`。

**Step 2: 运行失败测试。**

Run: `python3 -m pytest -q backend/tests/test_forecast_verdicts.py`

Expected: FAIL，因为模型尚不存在。

**Step 3: 写最小模型和 migration。**

在 `research_expression.py` 新增：

- `ForecastTargetVersion`：Case、factor、report claim、forecast/baseline source statement、metric name、entity key、baseline/target `Numeric` 值和单位、forecast period、operator、relative tolerance、review actor/reason/time。
- `ActualMetricObservation`：target、actual source statement、数值/单位/期间、`available_at`、记录 actor/reason/time。
- `ForecastEvaluationCandidate`：target、actual、cutoff、outcome、输入快照 JSON、rule version、rationale、machine state/created time。
- `ForecastVerdict`：candidate、human decision、published outcome、reason、reviewer、time，以及可选 `supersedes_id`；不提供 update 路径。

在 `ledger.py` 将四张表纳入 `IMMUTABLE_TABLES`。迁移 `0038` 建表、外键、约束及 PostgreSQL immutable triggers，SQLite 测试可照常用 metadata 建表。

**Step 4: 运行测试。**

Run: `python3 -m pytest -q backend/tests/test_forecast_verdicts.py`

Expected: PASS。

## 2. 以测试先行实现受控写入、数值判据和人审发布

**Files:**

- Create: `backend/app/services/forecast_verdicts.py`
- Modify: `backend/tests/test_forecast_verdicts.py`

**Step 1: 写失败服务测试。**

测试服务拒绝跨 Case / 非 reviewed / 非准入来源、指标或实体不符、单位不符、错误期间和早于可用时点的 cutoff。对火星人 target (`455000000 CNY`, `within_tolerance`, `0.10`) 和 actual (`247000000 CNY`) 断言 candidate 是 `contradicted`，输入快照含完整数值与 rule `forecast-numeric-v1`。再以容差内 actual 断言 `supported`。确认未经人审时没有已发布 verdict；`confirmed` 发布 candidate outcome，`modified` 强制指定替代 outcome，`rejected` 不会被正式读模型返回。

**Step 2: 运行失败测试。**

Run: `python3 -m pytest -q backend/tests/test_forecast_verdicts.py`

Expected: FAIL，因为服务尚不存在。

**Step 3: 写最小服务。**

定义显式 command dataclass 和纯函数 `evaluate_numeric_forecast`：

- `at_least` / `at_most` 精确比较；
- `within_tolerance` 使用 `abs(actual-target) <= abs(target)*tolerance`；
- 需要补数或未到披露时点只产生 `insufficient_evidence` / `not_due`，不伪称 supported；
- 服务验证 target、actual、source governance 和 Case admission；不从文本抽数字、不访问外网、不调用 LLM；
- 仅 `create_verdict` 将人工决定加入已发布序列；修正必须 append successor，不能 UPDATE。

**Step 4: 运行测试。**

Run: `python3 -m pytest -q backend/tests/test_forecast_verdicts.py`

Expected: PASS。

## 3. 暴露 HTTP 契约并把已发布 verdict 接到市场表达读模型

**Files:**

- Create: `backend/app/schemas/v1/forecast_verdicts.py`
- Create: `backend/app/api/v1/forecast_verdicts.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/schemas/v1/market_expression.py`
- Modify: `backend/app/queries/market_expression.py`
- Modify: `backend/tests/test_forecast_verdicts.py`

**Step 1: 写失败 API/read-model 测试。**

从 TestClient 发起 target → actual → evaluate → verdict 真实请求。断言：

1. candidate 创建后 `GET /research-cases/{case_id}/market-expression` 中 factor 仍没有 forecast verdict；
2. confirmed 后，同一 factor 返回 target/actual 两个来源、period、cutoff、`contradicted`、reviewer/reason；
3. `GET /research-cases/{case_id}/forecast-verdicts?cutoff=` 只返回截止时已发布的有效 verdict；
4. 未到 `available_at` 的历史 cutoff 不泄漏 2023 实际值或 verdict。

**Step 2: 运行失败测试。**

Run: `python3 -m pytest -q backend/tests/test_forecast_verdicts.py`

Expected: FAIL，因为路由和 DTO 尚不存在。

**Step 3: 写最小 HTTP/read-model 实现。**

新增受控写端点：

```text
POST /research-cases/{case_id}/forecast-targets
POST /research-cases/{case_id}/actual-metric-observations
POST /forecast-targets/{target_id}/evaluate
POST /forecast-evaluations/{candidate_id}/verdicts
GET  /research-cases/{case_id}/forecast-verdicts?cutoff=
```

端点把领域验证错误映射到现有 v1 error envelope。`MarketExpressionResponse` 的 `KeyFactorDTO` 增加可空 `forecast_verdict`，只经由读查询选择 confirmed/modified、case/source/factor 仍可见、且不晚于 cutoff 的最新 verdict。候选和 rejected verdict 不序列化。保留原 `ClaimVerificationDTO`，不改变其既有含义。

**Step 4: 运行测试。**

Run: `python3 -m pytest -q backend/tests/test_forecast_verdicts.py backend/tests/test_market_expression_api.py`

Expected: PASS。

## 4. 接入真实 HTTP 前端读模型和关键因素下钻文案

**Files:**

- Modify: `frontend/src/contracts/v1.ts`（由仓库现有 contract 同步命令生成）
- Modify: `frontend/src/features/case/MarketExpressionContent.tsx`
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`

**Step 1: 写失败前端测试。**

用真实 `/api/v1/research-cases/:id/market-expression` mock HTTP response（不是 `?client=mock`）渲染 Case 市场表达页。断言选中 factor 显示“预测未兑现”、预测/基线/实际值和来源链接、人审人及理由；并同时显示 `300894.SZ` 股票，以及 `560010` 的历史披露期间/披露日/来源/新鲜度。不得出现“实时仓位”“将上涨/下跌”“买入/卖出”。无 verdict response 时显示“待人审”，不显示实际结论。

**Step 2: 运行失败测试。**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: FAIL，因为 UI 尚未呈现 forecast verdict。

**Step 3: 写最小 UI。**

同步 OpenAPI contract，利用 `factor.forecast_verdict` 在现有四段链路加“预测验证”卡：机器候选不可见，已发布结果复用 supported/contradicted 标签，并显式标为“人工已审核”；继续复用既有股票绑定、fund exposure 和 stale/coverage 标签。不要添加自动注册表单或批量评分入口。

**Step 4: 运行测试。**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: PASS。

## 5. 冻结真实短摘录单案例，并作端到端验收

**Files:**

- Create: `backend/tests/fixtures/historical_report_forecast/firestar_broker_report_excerpt.json`
- Create: `backend/tests/fixtures/historical_report_forecast/firestar_2023_annual_report_excerpt.json`
- Create: `backend/tests/fixtures/historical_report_forecast/gf_560010_holding_excerpt.json`
- Create: `backend/tests/test_historical_report_forecast_e2e.py`
- Modify: `docs/superpowers/specs/2026-08-10-historical-report-forecast-verdict-design.md`（仅在实际来源页码、hash、基金披露字段校验后补入）

**Step 1: 写失败 E2E。**

fixture 必含真实原始 URL、发布日、页码/locator、短必要原文、SHA-256、许可证/准入字段和数值；测试用真实 HTTP 写入产生 Case、冻结文档/statement、report claim、factor、target、actual、candidate、confirmed verdict、reviewed stock binding/fundamental impact 及 holding disclosure。断言报告预测 `455m` 对实际 `247m` 的正式结论为 `contradicted`，并能从同一 market-expression response 下钻到 300894.SZ 和 560010 历史披露。

**Step 2: 运行失败测试。**

Run: `python3 -m pytest -q backend/tests/test_historical_report_forecast_e2e.py`

Expected: FAIL，直到 fixture 和完整 HTTP 链路完成。

**Step 3: 添加最小真实 fixture 与 E2E 设置。**

只保存为验证所需的短摘录，不转载整份研报；由 source URL、文件 hash 与 locator 回跳原始 PDF。基金 fixture 只记录经核验的报告期、披露日、position quantity/value/weight、采集日、coverage 和来源。若不能从官方公告确认这些字段，停止并把基金断言保留为待补数据，绝不虚构历史持仓。

**Step 4: 运行全链路验证。**

Run:

```bash
python3 -m pytest -q backend/tests/test_forecast_verdicts.py backend/tests/test_historical_report_forecast_e2e.py backend/tests/test_market_expression_api.py
npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx
npm --prefix frontend run build
git diff --check
```

Expected: 全部 PASS；测试不依赖外网、不依赖 `?client=mock`，只消费冻结 fixture。

## 6. 复核并交付

**Files:**

- Review: 本计划涉及的所有文件

**Step 1: 审阅 diff。**

Run: `git status --short && git diff --check && git diff --stat`

确认没有碰主工作树的 `event_impact` 实验文件，没有批量评分、实时仓位或投资建议语义。

**Step 2: 提交前复跑上面的全链路验证。**

仅在命令均成功后，才总结成功范围和仍未覆盖的批量评分工作。

