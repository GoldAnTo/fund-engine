# 事件研究主链验证报告

验证日期：2026-08-16  
分支：`codex/event-research-mainline`  
Task 10 基线：`160bcfc`

## 本轮实际证明的范围

- 冻结研究范围后，编排状态会持久化推进到资料规划、资料获取、覆盖评估、证据归并、报告生成和监控。
- PostgreSQL 16 主链使用独立数据库 `fund_engine_event_test_20260816`，命令、资料获取 worker、研究 worker 和验证读取分别使用独立 SQLAlchemy Session。
- 外部资料只在 source adapter 边界替换为确定性测试实现；数据库、仓储、`AcquisitionModule`、`AcquisitionRunner`、自动准入、编排服务和 worker 入口均运行生产代码。
- 资料获取共创建 7 个目标绑定任务和 7 份不可变查询计划；重复 reconcile/worker 调用不会增加计划、任务或正式证据。
- 正式证据保留 final URL、获取时间、64 位 SHA-256、自动准入决定和 Evidence Link；系统结论明确标记“系统生成，未经人工审核”，reviewer 为空。
- 研究 worker 通过生产入口 `run_research_worker.run_once()` 执行 reconcile、claim、execute、租约围栏完成写入和编排回调。事件编排运行带有冻结的 `orchestrated_evidence_synthesis` 执行模式，只综合当前 scope 已自动准入的 Evidence Link，最终形成 `succeeded / complete`；它不会进入普通研究运行的原子陈述人工门禁，也不会伪造 reviewer。
- 普通研究运行仍保留 `waiting_for_review / claim_review` 门禁；回归测试证明该状态不会被事件编排误判为综合完成。该 PG 用例没有调用模型 provider，因此本报告不声称已验证模型 provider 调用。
- PostgreSQL 主链测试不直接赋值 `ResearchRun.status/stage/stop_reason`；少量状态机单元测试使用受控 fixture 构造完成回执，用于验证拒绝路径与幂等转换。

验证到的持久化阶段顺序：

```text
planning_acquisition
→ acquiring
→ assessing_coverage
→ synthesizing_evidence
→ generating_report
→ monitoring
```

## 架构边界

AST 边界测试验证以下编排模块不直接导入 source adapter、Gildata/交易所数据源或 acquisition repository：

- `app/services/research_orchestration.py`
- `app/services/research_acquisition.py`
- `app/services/acquisition_coverage.py`
- `app/repositories/research_orchestration.py`
- `app/queries/research_workflow.py`

前端工作流模块不得导入 mock adapter。工作流台账读取通过带 Case/tenant 授权的 `AcquisitionModule` 公共方法完成，不再由 query 层绕过服务边界访问 acquisition repository。

## 生产修复及回归证据

1. 有界台账分页和结论引用读取加入 Case、tenant、scope 授权。临时移除 Case 准入校验后，跨租户测试按预期失败；恢复后通过。
2. provider 诊断中的 `NaN`、`Infinity`、`-Infinity` 在持久化前归一化为 JSON `null`。临时移除归一化后 3 个测试按预期失败；恢复后通过。
3. fetch attempt 的 `finished_at` 与 artifact 的 `retrieved_at` 使用同一完成时点。恢复旧的二次读时钟逻辑后，普通 fetch 和 inline fetch 两个 chronology 测试均按预期失败；修复后通过。
4. Task 9 已提交的 reviewer/approved_by 服务端身份变更重新生成 OpenAPI/TypeScript contract；mock API 也由服务端 actor 生成批准人，不再回显客户端伪造值。
5. 事件主链与普通研究运行使用显式执行模式区分。普通 `waiting_for_review` 不能推进编排；事件综合任务在 Case、scope、orchestration、run 与 worker lease 全部匹配后，按 task 持久化实际消费的 Evidence Link ID，并以 `succeeded / complete` 结束。
6. 编排只接受 `succeeded / complete`、专用 stop reason、冻结 scope 事件、同一个已完成 Research Job 的 `job_id / attempt` 和 worker 完成回执同时匹配的运行；任务消费快照必须与完成回执完全一致。worker 与报告共用严格证据快照读取器，逐条校验 Assignment、Evidence Link、admitted AutomaticAdmissionDecision、AcquisitionJob、request snapshot、QueryPlan、Series、goal、tenant、Case、scope 与 run lineage；损坏 lineage 会失败，不会被解释为空证据。跨 run、人工审核或未映射证据不会混入系统草稿。
7. 研究 worker 在编排或报告回调失败时先回滚未提交的成功终态，再以同一个 claim/attempt 写入 `failed / failed` 并增加 `failure_count`；真实 `run_once()` 回归证明该窗口不会退化成无限 stale-lease recovery。Acquisition Job 的 status/stage 和冻结 policy 也纳入严格 lineage 校验。
8. 严格证据快照同时支持因子级映射和当前运行的事件级 `alternative_explanation`：后者必须是 `unmapped / factor_statement=None / mapping_scope=event / contextualizes`，只进入 `alternative/result` 任务，并与完成回执和最终草稿保持同一证据集合。Case 的真实 tenant admission 也在固定查询数内验证；错误租户或角色损坏会失败关闭。

## 验证命令和结果

### 后端默认套件

```bash
cd backend
env -u TEST_DATABASE_URL .venv/bin/python -m pytest -q \
  -m 'not pg_only' \
  --ignore=tests/test_verify_live_event_ui.py
```

结果：`2248 passed, 2 skipped, 57 deselected, 22 warnings`。

### PostgreSQL 专属套件

```bash
cd backend
TEST_DATABASE_URL='postgresql+psycopg://evidence:***@127.0.0.1:5432/fund_engine_event_test_20260816' \
  .venv/bin/python -m pytest -q -m pg_only
```

结果：`57 passed, 2251 deselected, 1 warning`。

### PostgreSQL 事件研究主链

```bash
cd backend
TEST_DATABASE_URL='postgresql+psycopg://evidence:***@127.0.0.1:5432/fund_engine_event_test_20260816' \
  .venv/bin/python -m pytest tests/test_event_research_mainline_postgres.py -q
```

结果：`1 passed, 1 warning`。

### 前端

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run e2e
```

结果：

- Vitest：`15 files passed, 350 tests passed`。
- TypeScript：通过。
- Vite production build：通过，63 modules transformed。
- Playwright：`41 passed`。这些 e2e 使用显式 mock 客户端，只证明 UI 行为，不是 live API/worker 验收。

### 契约和静态边界

```bash
bash scripts/sync-contract.sh --check
ruff check <生产文件、新 Task 10 测试和本轮修改的测试文件>
git diff --check
```

结果：contract in sync；Ruff 通过；无 whitespace error。`test_auto_research_api.py` 的历史 `E702/F841` 基线，以及 `run_research_worker.py` 为先加载本地环境而存在的历史 `E402`，在对应聚焦命令中被显式忽略；本轮新增行没有 Ruff 问题。

## 当前真实 provider 范围

当前实现范围仅为：

- SSE
- SZSE
- 已配置并有许可声明的 Gildata

本轮 PG 集成测试不会访问这些 live provider，而是在同一 source adapter 边界注入确定性 SSE/SZSE/Gildata 实现。CNINFO、HKEX、公司 IR、一般网页搜索与其他外部发现能力均未实现或未做 live 验收，不得在产品文案中描述为已支持。

## 明确未声明完成的内容

以下内容属于下一份 runtime-live 计划，不属于本轮 Task 10，也未在本报告中声明完成：

- API、research worker、acquisition worker、scheduler、PostgreSQL 的一键 Compose/部署拓扑与统一监控。
- OIDC/真实用户目录接入；当前已验证的是服务端 actor 与 Case tenant admission 边界，不是生产身份提供商。
- 不使用 Mock、不直接操作数据库的真实浏览器主链。
- 浏览器验收中的 API/worker 进程重启、租约恢复和页面实时恢复证明。
- live SSE/SZSE/Gildata 网络请求、凭据、限流、故障切换和来源可用性 SLA。

现有 `backend/tests/test_verify_live_event_ui.py` 仍绑定已退役的严格协议门禁文案（`frontend/scripts/verify-live-event-ui.mjs:546`），不是当前事件主链的有效验收，故从默认绿色套件中显式排除。它需要在下一 runtime-live 计划中改写成当前流程，并加入 API/worker restart 场景后，才能替代本轮 mock Playwright 证明。

曾诊断性执行过把 `TEST_DATABASE_URL` 全局注入整个 `pytest -q` 的非标准命令，结果为 `2257 passed, 11 skipped, 26 failed`。该命令会把许多明确为 SQLite 编写的通用夹具强制切换到 PostgreSQL，产生 `?` 占位符、SQLite CAS 和并发时序等方言/夹具失败，同时包含上述过期 live UI；它不是本仓库支持的验证拆分，不能用于声称 PG 主链失败或通过。最终证据以“默认套件”和独立 `pg_only` 套件为准。

另外，旧通用 `POST /research-cases` 不创建事件 Case 的不可变 tenant admission；对该 Case 直接请求租户隔离 review queue 会得到 404。事件主链使用 `/event-research` 的受控准入路径；旧通用入口的身份/准入迁移不在本轮完成范围内。
