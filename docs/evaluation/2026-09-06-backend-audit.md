# 2026-09-06 后端独立审查

范围：API 租户边界、任务恢复与认领、上传原件、数据库及容器部署。仅使用临时/内存 SQLite；未连接真实数据库、未访问上游、未读取凭据文件。全量测试由主代理另行执行。本报告记录审查时发现，主代理可能随后修复认证问题。

## 已确认问题

### P1：旧 API 可绕过 Case 租户边界

- `backend/app/api/legacy.py:22` 的 `GET /api/research-cases/{case_id}/workbench` 无认证依赖；`WorkbenchService.load_workbench` 直接按主键读取 Case。
- `backend/app/api/v1/commands/cases.py:97` 的 `POST /api/v1/research-cases/{case_id}/theses` 无认证或 Case 所属租户校验；`ResearchService.add_thesis` 仅检查 Case 存在。
- 内存数据库复现：token-a 创建 team-a 的事件研究返回 201；匿名访问新版 workbench 返回 401，但匿名访问旧版 workbench 返回 200，匿名向同一个 Case 插入 thesis 返回 201。
- 影响：知道 Case ID 的未认证调用方能读取研究并向已隔离 Case 注入论点。仅加全局认证不能阻止合法的其他租户写入，必须补对象所属 Case 校验。
- 现有保障：新版事件研究 API 使用宿主配置的 bearer token；`CaseTenantAccess.require_case` 对不属于当前租户的 Case 返回 404；这些保障没有覆盖旧路由。
- 建议：复用可信 tenant dependency 与 `CaseTenantAccess`，对旧路径补匿名、外租户与本租户的读写回归。关联 thesis、document、review、job 必须追溯所属 Case；共享目录应明确单独处理。

同类静态审查清单（未逐一路由做权限攻击验证，不能全部当作已确认泄露）：

| 文件 | 路由/能力 |
| --- | --- |
| `api/v1/documents.py:18,36` | 文档列表、任意 version ID 详情，无 tenant 入参 |
| `api/v1/jobs.py:54,62,95,106` | Job 详情、事件、取消、重试 |
| `api/v1/activity.py:49,71,87,109,141` | 活动、证据变更、task 增改查 |
| `api/v1/commands/engine.py:115,198,243,332` | 文档补件/抽取、thesis 重跑/提案 |
| `api/v1/commands/reviews.py:31,44,80` | review queue、证据审核、评估审核 |
| `api/v1/commands/proposals.py:50,82,123` | proposal 列表、认领、决策 |
| `api/v1/commands/causal.py:42,63` | thesis 因果步骤和边写入 |
| `api/v1/commands/themes.py:22` | Case theme tags 修改 |
| `api/v1/commands/instruments.py` | 公司、股票、基金、持仓、估值、公司 theme role 写入 |
| `api/v1/commands/cases.py:47` | 旧 Case 创建（也没有租户 admission） |
| `api/v1/{research_ops,knowledge,companies,themes,metrics}.py` | 全局读模型，需要核定共享与租户范围 |

以上路径均相对 `backend/app/`，行号按独立审查时版本记录。

### P1：失去认领的 worker 能覆盖新 worker 的等待状态（已修复）

- `backend/app/repositories/auto_research.py:135` 的 `wait_for_sources` 原先只防取消，没有验证 claim token 或已完成状态；`backend/app/scripts/run_research_worker.py:76` 原先未传递领取任务时保存的 token。
- 复现：领取任务后持久化另一 worker 的 token，再调用旧 worker 的等待分支，会把新认领的 running Job 覆盖成 `waiting_for_sources` 并清空 token；已成功或失败任务也能被重新挂起。
- 现有保障：terminal 分支 `record_job_completion` 已有 claim fencing、终态和取消校验，使用 Case → Run → Job 锁顺序；等待分支遗漏了对应校验。
- 实施：等待分支新增 `expected_claim_token`；不匹配或 Job 已 succeeded/failed 时 rollback 并返回，连同旧 worker 的 staged writes 一起丢弃；worker 调用显式传递原 claim token。原取消处理和锁顺序保留。
- 回归：新增 running/succeeded/failed 三种状态；先验证三个断言都失败（Job 被写成 waiting_for_sources），再修复为通过。
- 验证命令（backend 目录）：`APP_ENV=test TEST_DATABASE_URL='' DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_automatic_research_pipeline.py tests/test_research_worker_entrypoint.py -q`。
- 结果：**79 passed, 5 skipped**。跳过项需要 PostgreSQL；本次未声称已验证真实 PostgreSQL 并发锁行为。

### P2：幂等冲突退路和市场观察注解缺失导入（已修复）

- `backend/app/repositories/operational.py:311` 的冲突记录消失退路原本引用未导入的 `ConflictError`，会抛出 NameError 而不是可重试的领域冲突。已补 `from app.errors import ConflictError`。
- 新增隔离回归实际执行重复主键 INSERT 触发 IntegrityError，然后在重新 SELECT 前删除冲突记录，模拟冲突记录已消失；先确认 NameError，再确认返回 `ConflictError("idempotency_conflict")`、会话仍可提交且外层先前写入的 Job 保留。该测试验证错误处理分支与 savepoint，不宣称复现真实 PostgreSQL winner rollback 时序。
- `backend/app/services/market_expression.py:91` 的 `MarketObservationInput.relative_return` 注解缺少 `Decimal` 导入；新增 `get_type_hints` 测试先确认 NameError，再补导入后验证解析结果为 `Decimal | None`。由于 future annotations 延迟求值，未声称所有业务请求都会触发此问题。
- 回归命令（backend 目录）：`APP_ENV=test TEST_DATABASE_URL='' DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_operational_repository_regressions.py tests/test_operational_api.py tests/test_market_expression_api.py -q`，结果 **22 passed**。
- 两个生产文件的 `ruff check --select F821` 通过。未修改其他未经验证的静态告警。

### 兼容性变更：明确退役旧无来源 Case 创建入口

- 整合租户检查后，旧 `POST /api/v1/research-cases` 仍能返回 201 却不生成 admission，导致创建者无法打开/继续该 Case。现已将此入口明确退役：匿名请求 401，认证且请求形状有效时 409 `conflict`，错误说明指向 `POST /api/v1/event-research`。OpenAPI 标记 deprecated 并声明 409，不再声明创建成功的 201。
- 这是有意的 breaking change。旧接口不再创建无归属 Case，也不为其伪造 source 或 admission；支持的创建路径是提交用户原文/原始材料的事件研究流程。已存在 Case 的追加 thesis 仍保留所属租户检查。
- 新增回归验证：匿名/认证旧创建后 Case、Thesis、DocumentVersion 都零写入；公开 event API 使用用户粘贴原文创建后，无手工 admission 即可打开旧 workbench（200）并追加 human/AI thesis（201，confirmed/draft）。
- `test_review_commands_api.py`、`test_causal_commands_api.py`、`test_gap_fill_api.py`、`test_research_flow_e2e.py` 中旧创建 fixture 已迁移到公开 event API，然后调用追加 thesis 接口；没有用手动 admit 绕过公共流程。
- 对上述四个文件加 `test_event_case_tenant_access.py`、`test_workbench_api.py` 执行隔离回归，结果 **60 passed**。旧创建新契约先复现 `201 != 409` 再实施修复。`git diff --check` 通过。

## 上传、数据库、部署观察

- 上传 API 读取上限是 20 MiB + 1，超限拒绝；限定 PDF/TXT/Markdown/CSV MIME；空文件拒绝；原始 bytes、SHA-256 与解析结果在同一事务保存；解析失败保存失败版本而不伪造文本。
- 上传先由 multipart 解析，业务层大小限制不能等同于反向代理请求体限制；压缩 PDF 解码资源消耗与恶意 multipart 大请求的端到端限制未做压力测试，不宣称存在已复现 DoS。
- SQLite 引擎设置外键检查；PostgreSQL 有边界明确的连接池参数、pre-ping 与回收机制；append-only 与迁移测试属于主代理全量验证范围。
- 一键 Compose：API/frontend 端口绑定 loopback、数据库无宿主端口、生产数据库参数必填、容器非 root、worker 带心跳健康检查，迁移成功后才启动服务。
- 普通 `docker-compose.yml` 是另一条开发启动路径：数据库端口绑定所有网卡且使用仓库默认密码。建议显式标注仅开发用途并绑定 loopback；尚未检查用户主机防火墙或实际端口暴露。
- `/health` 只报告进程存活，不探测数据库；适合作为 liveness，但不能代表 DB readiness。建议另设 readiness，避免混淆故障定位。
- 长时间任务恢复、同时取消/重新领取等 PostgreSQL 真实并发，以及上传解压资源消耗，仍需独立隔离环境验证。

## 主任务整合补充

已复现的旧 workbench 和 thesis 绕过现已加 tenant/Case 校验；旧无来源创建认证后409并引导事件创建，未准入Case不自动猜测归属。另公司研究 initializer 预览截止日期恢复为 governed boundary，解决预览与basis不一致及等价日期幂等冲突；原完整测试26项通过。开发 Compose 端口已改为loopback，前端Docker构建文件已恢复；未实际重启容器。最终结果以总报告为准。
