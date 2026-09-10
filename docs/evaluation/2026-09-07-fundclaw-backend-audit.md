# FundClaw Gateway 后端独立审计与性能修复

审计日期：2026-09-07。目标工作区：`<project-root>`。本报告依据该工作区本次读取、测试和测量；不把主工作区或此前真实案例的验收结果当成本次验证。

## 发现与优先级

| 优先级 | 发现 | 状态与位置 |
| --- | --- | --- |
| P1 | 已经持久化的全部原生事件，在每次快照和 SSE 空轮询中重新进入幂等写入路径。单次空轮询生成数万条 SQL，并在会话写锁内完成，放大读延迟和 worker 争锁。 | 已修复。[adapter:128](../../backend/app/services/research_gateway_automatic_adapter.py#L128)、[repository:1236](../../backend/app/repositories/research_gateway.py#L1236)。 |
| P2 | 即使已经追平，SSE 仍然构造完整会话快照；事件过滤在内存中完成，读取、摘要校验、DTO 创建和 revision 哈希仍随历史增长。 | 保留为后续优化。[stream:55](../../backend/app/services/research_gateway_stream.py#L55)、[projection:80](../../backend/app/services/research_gateway_projection.py#L80)、[adapter:208](../../backend/app/services/research_gateway_automatic_adapter.py#L208)。 |
| P2 | 首次把大量从未投影过的原生历史补入 Gateway，仍逐条持久化；本次批量去重不减少真正新增事件的写入成本。 | 未修复。应作为历史补建/迁移负载单独设计和验收，不能把重复读取加速当作初次补建加速。 |

本次没有复现新的跨 owner / tenant 读取漏洞、幂等重复执行、worker 恢复或终态覆盖缺陷。该结论仅覆盖下述测试边界，不代表全面安全认证。

## 复现与测量

使用 `cmd_session` 创建的独立 SQLite 内存数据库，以 stub extractor 建立合法的私有会话和原生范围，追加 **4161 条合成 ResearchRunEvent**。首次投影后共 **4170 条 RoleEvent**（另有 4 条初始角色、1 条原生范围和 4 条状态事实）。这不是生产数据库中那 4161 条历史的副本，也没有读取生产案例。

SQL 计数来自 SQLAlchemy `before_cursor_execute`，包含 SELECT、UPDATE、INSERT 和保存点语句；计时包含服务读取和 session 关闭，未包含 HTTP 传输、浏览器渲染和序列化整个 HTTP JSON 响应。每项为一次本机测量，时间不是 SLA；回归门槛使用 SQL 数量而非易波动的墙钟阈值。

| 场景 | 修复前 SQL | 修复前耗时 | 修复后 SQL | 修复后耗时 |
| --- | ---: | ---: | ---: | ---: |
| 从未投影的历史首次补建 | 37544 | 5.851134 秒 | 37545 | 5.732541 秒 |
| 已持久化历史完整快照 | 20880 | 3.597142 秒 | 51 | 0.279847 秒 |
| 游标已追平的空 replay | 20880 | 3.268389 秒 | 51 | 0.297447 秒 |

两项稳定历史读取的 SQL 减少约 **99.76%**；该次测量的快照耗时降低约 92.2%，空轮询降低约 90.9%。原文档记录的“首次页面加载可能超过 15 秒”只是背景，本次没有在 live 数据上重新确认或宣称已经解决那个具体时延。

新增测试先在未修复版本运行并失败，失败值为 `20880 < 100`；随后只修改 adapter，按同样的 4161 条规模重跑通过。测试文件默认 64 条，避免每次普通回归都承担大规模初次补建；64 条与 4161 条的稳定轮询均为 51 条 SQL。

可复现实验（在目标工作区 `backend` 目录）：

```sh
env -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test GATEWAY_AUDIT_HISTORY_SIZE=4161 .venv/bin/python -m pytest -q -s tests/test_research_gateway_poll_performance.py
```

## 最小修复及授权边界

只修改生产文件 `backend/app/services/research_gateway_automatic_adapter.py`。在已有会话 writer lock、native case/run 和 frozen scope 检查之后，一次读取当前 run spec 的持久化 `RoleEvent.source_key` 集合。原生运行事件、采集事件、证据引用和状态事件统一经过局部 `append_once`：已有 key 直接跳过；新的 key 仍走原仓储的完整锁、序号、幂等与回滚逻辑。

- 集合局限于本次 `sync` 调用，事务回滚或下一次轮询都会重新读取，避免把未提交事件误记为已经提交。
- 会话写锁在读取集合之前取得，因此两个投影器不会靠过期集合自行分配序号。
- 不跳过 [private owner / tenant 查询](../../backend/app/services/research_gateway.py#L162)、native Case admission、run/case 归属和 frozen scope 校验。
- 不缓存授权结果；每轮仍执行 [native_artifacts](../../backend/app/services/research_gateway_automatic_adapter.py#L190)，快照独立重建授权引用。来源展示权限过期后，历史引用仍会被移除；SSE 仍通过 `artifact_access_changed` 要求客户端清除旧视图。
- 不使用可变 native 状态作为“无变化则返回缓存”的依据，执行任务、worker heartbeat 和来源重试状态仍实时投影。
- 没有改动仓储原有脏工作、数据库迁移、AI client 或前端；没有压缩、删除或重写原有历史事件。

## 验证

第一次针对性回归：**232 passed, 4 skipped, 2 warnings，37.50 秒**。命令：

```sh
env -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test .venv/bin/python -m pytest -q tests/test_research_gateway_service.py tests/test_research_gateway_repository.py tests/test_research_gateway_projection.py tests/test_research_gateway_stream.py tests/test_research_gateway_automatic_adapter.py tests/test_research_gateway_artifact_authorization.py tests/test_research_gateway_execution.py tests/test_research_gateway_security.py tests/test_research_gateway_api.py tests/test_research_worker_entrypoint.py tests/test_acquisition_worker_recovery.py tests/test_research_gateway_poll_performance.py
```

随后补齐并发/回滚/撤销专项，新增测试文件最终 **7 passed, 2 warnings，3.00 秒**；该次测试文件与第一次回归中的 1 项重叠，不应把两个结果直接相加成独立用例总数。

| 边界 | 本次验证证据 |
| --- | --- |
| 历史成本与增量幂等 | [poll_performance:55](../../backend/tests/test_research_gateway_poll_performance.py#L55)：稳定历史 SQL 有界；增添 1 条原生事件仅追加 1 条事件与 4 条状态事实；原有事件 DTO 一致；序号连续。 |
| 采集历史 | [poll_performance:89](../../backend/tests/test_research_gateway_poll_performance.py#L89)：追加 64 条采集事件后稳定轮询 SQL 不随事件数线性增长；原始 payload 不进入 DTO。 |
| 崩溃/回滚恢复 | [poll_performance:115](../../backend/tests/test_research_gateway_poll_performance.py#L115)：同一 adapter 在回滚后重新投影完整 source key 集合；既有 worker 测试覆盖 stale lease、提交失败、owner 轮换和恢复检查点。 |
| 两个投影器并发 | [poll_performance:135](../../backend/tests/test_research_gateway_poll_performance.py#L135)：独立文件 SQLite、两个独立 Session/线程；事件无重复，会话及 run 序号均连续。既有 worker 并发 Gateway 测试一并通过。 |
| 私有权限重检 | [poll_performance:172](../../backend/tests/test_research_gateway_poll_performance.py#L172)：已追平后 owner、tenant、Case admission 变化均拒绝读取；既有 API/service/security 测试覆盖同租户其他主体、跨租户与 raw operational 旁路。 |
| 源权限撤销 | 既有 projection/stream/artifact_authorization 测试验证展示权到期、历史引用移除、SSE reset、伪造/跨范围引用丢弃和来源策略变化后的 draft 隐藏。 |
| 状态机与 worker | 既有 automatic_adapter/execution/worker 测试验证 waiting→running、失败终态、原生成功但无有效 draft 不冒充完成、retry_wait、离线 heartbeat、取消/其他 worker 终态胜出，以及原生事务提交后的投影。 |
| 幂等恢复 | 既有 repository/service 测试验证相同请求只启动一次、不同 payload 冲突、过期 lease、错误 provenance、被篡改 receipt cache、回收请求和初始事件恢复。 |

Ruff 检查修改的 adapter 和新增测试文件通过。测试出现两条已有 warning：Starlette 对 AnyIO BlockingPortal 别名的弃用，以及 SourceLocatorV1 的 `schema` 字段遮蔽父类属性。

## 未覆盖与后续建议

1. **未连接 PostgreSQL 或 Neo4j。** 4 个 PostgreSQL worker terminalization 竞争场景按配置跳过；实际 PostgreSQL 锁等待、隔离级别和多进程行为仍需独立临时数据库验证。本次 SQLite 并发通过不能替代 PostgreSQL 并发验收。
2. **未访问 live 数据库，未发真实模型或外部数据请求。** 测量不覆盖真实证据体积、多个 run specs、多客户端持续连接、真实网络 RTT、provider 延迟或系统级容量。
3. 后续若进一步降低空轮询的 O(N) 读取，应拆出“当前授权 artifact identity 集合”和“游标后的增量事件”。仅把 `after_sequence` 传给现有 snapshot 会让 stream 看不到历史引用的权限撤销，不能直接这样优化。
4. 首次补建可考虑批处理持久化/后台 catch-up，但必须继续保证 conversation/run 双序号、唯一 source key、事务回滚、授权检查和多 writer 竞争；本次没有扩展到该设计。
5. 未重新运行数据库迁移套件、整个后端全部用例或真实浏览器端到端负载。本报告只证明上述范围，主任务的额外验证应单独列出。

## 追加：独立 PostgreSQL 验证（2026-09-07）

根任务随后明确授权使用新建、可丢弃的 PostgreSQL 容器。因此上述第 1 项的“未连接 PostgreSQL”及第 5 项的“未重新运行数据库迁移套件”仅描述最初 SQLite 阶段；本节补齐指定的 PostgreSQL 迁移和 worker 竞争场景，未扩展到整个 PostgreSQL 测试套件。

连接身份已在运行前核对：

| 项目 | 实际结果 |
| --- | --- |
| 容器 | `fundclaw-workbench-audit-pg-0907`，新建一次性容器 |
| 镜像和映射 | `postgres:16-alpine`，仅绑定 `127.0.0.1:55439 → 5432` |
| SQL 当前库 / 用户 | `fundclaw_audit` / `postgres` |
| SQL `version()` | `PostgreSQL 16.15 on aarch64-unknown-linux-musl`，Alpine GCC 15.2.0，64-bit |
| Alembic 实际方言 | `Context impl PostgresqlImpl`；`Will assume transactional DDL` |
| 迁移结果 | 从空库完整 `upgrade head` 成功，`alembic_version = 0074` |

运行时将 `DATABASE_URL` 和 `TEST_DATABASE_URL` 同时明确指定到该临时容器，设置 `APP_ENV=test` 并去掉 `NEO4J_URL`。为避免将口令写入报告，下述 `$AUDIT_PG_URL` 表示该一次性库的完整 `postgresql+psycopg://` 连接串；未使用项目默认数据库或任何业务数据库。

```sh
env -u NEO4J_URL DATABASE_URL="$AUDIT_PG_URL" TEST_DATABASE_URL="$AUDIT_PG_URL" APP_ENV=test .venv/bin/python -m alembic upgrade head
env -u NEO4J_URL DATABASE_URL="$AUDIT_PG_URL" TEST_DATABASE_URL="$AUDIT_PG_URL" APP_ENV=test .venv/bin/python -m pytest -q -ra -m pg_only tests/test_research_gateway_migration.py tests/test_research_worker_entrypoint.py
```

结果：**7 passed, 62 deselected, 40 warnings，26.01 秒**，退出码 0，没有跳过指定的 PostgreSQL 用例。

| 场景 | 结果 |
| --- | --- |
| `test_0073_postgres_migration_installs_append_only_guards_and_downgrades` | 通过；实际 PostgreSQL append-only guards、历史模式验证与降级路径 |
| `test_0074_postgres_direct_upgrade_authenticates_0073_before_stamping` | 通过；直接升级在 stamping 前验证 0073 模式 |
| `test_0073_postgres_serializes_legacy_preflight_before_hardening` | 通过；并发 legacy writer 与 hardening preflight 串行化 |
| `test_postgres_worker_terminalization_serializes_public_job_cancel` 的四个参数场景 | 全部通过；原生成功/失败两种终态 × cancel/worker 两种锁竞争胜出顺序 |

测试后再次查询确认 `fundclaw_audit` 的公共 schema 仍为版本 `0074`，三个迁移测试创建的 `gateway_007%` 临时 schema 已清理（0 行）。本次未修改生产代码；没有触碰其他数据库，也没有发起真实模型或外部数据请求。容器由根任务负责停止。

40 条 warning 分别是 38 次 Alembic `path_separator` 缺失弃用提示、1 次 Starlette/AnyIO 别名弃用提示和 1 次 SourceLocatorV1 `schema` 字段提示。它们没有使测试失败。本次 PostgreSQL 验证不替代 SQLite 性能数据，也没有对批量 source key 去重做真实 PostgreSQL 压测；该性能和容量边界仍需后续单独验收。

## 追加：全量后端 9 个失败的独立归因（2026-09-07）

根任务提供的 `/tmp/fundclaw-gateway-full-backend.log` 记录：**9 failed, 4862 passed, 41 skipped, 418 warnings，1001.63 秒**。本节只调查这 9 项并进行精确重现，没有再次运行全量，也没有修改生产代码。

结论：**6 项旧前端资产/测试未迁移，1 项传统 CLI 测试对 Python 解析行为的假设不成立，2 项同一处既有研究初始化 cutoff 回归。没有证据将这 9 项归为本次 Gateway 性能、HTTP deadline、SSE、导航或代理修复的回归。** 全量套件仍然失败，不能据此把工作区宣称为全绿。

### 逐项归因

| # | 失败测试 / 断言位置 | 直接原因与建议 |
| --- | --- | --- |
| 1 | `test_runtime_images_include_migrations_and_server_side_auth`；[test_one_click_runtime_assets.py:9](../../backend/tests/test_one_click_runtime_assets.py#L9) | `frontend/Dockerfile` 缺失，尚未执行 Docker/auth 内容断言；相邻依赖 `frontend/nginx.one-click.conf.template` 也已退休。迁移 one-click 构建/代理入口与验收，而非为空文件补桩。 |
| 2 | `test_frontend_docker_build_context_excludes_client_environment_files`；[test_one_click_runtime_assets.py:21](../../backend/tests/test_one_click_runtime_assets.py#L21) | 同一 Dockerfile 缺失；`frontend/.dockerignore` 也已退休，测试未能运行客户端环境文件排除断言。若继续支持该部署方式，须为新前端提供实际构建资产及凭据排除测试。 |
| 3 | `test_cli_rejects_deep_json_without_traceback`；[test_one_click_stability.py:501](../../backend/tests/test_one_click_stability.py#L501) | 2000 层合法 JSON 数组在当前 Python 3.13.3 能解析；因此走 [validate_snapshot:62](../../scripts/one_click_stability.py#L62) 的结构拒绝，输出 `error: inspected container is malformed`。测试期待解析器抛递归错误后输出 `error: JSON input is invalid`。程序仍返回 2 且没有 traceback；应测试安全拒绝契约，若产品确实要求最大嵌套深度，则显式规定/检查深度，不能依赖解释器栈阈值。 |
| 4 | `test_package_exposes_the_closed_live_company_research_command`；[test_verify_live_company_research_ui.py:2190](../../backend/tests/test_verify_live_company_research_ui.py#L2190) | 新前端 package 已不含 `verify:live-company-research`（后续断言的 `test:live-company-research-support` 也不存在）。应迁移验收脚本入口或明确退休旧验收能力；不能将缺少命令解释成已验证浏览器业务。 |
| 5 | `test_live_event_ui_verifier_keeps_reviewed_case_pages_working_through_default_http_client`；[test_verify_live_event_ui.py:18](../../backend/tests/test_verify_live_event_ui.py#L18) | 启动即找不到 `frontend/scripts/with-project-node.mjs`，目标 `verify-live-event-ui.mjs` 也缺失。没有进入浏览器或后端业务流程。输出中的 Node 18.20.4 是 wrapper 无法启动后的环境事实，当前首要根因仍是已删文件。 |
| 6 | `test_preview_is_read_only_and_resolves_all_effective_alphabet_securities`；[test_company_research_initializer.py:116](../../backend/tests/underwriting/test_company_research_initializer.py#L116) | [initializer:199](../../backend/app/underwriting/services/company_research_initializer.py#L199)将请求截止传给 preview，丢弃已经解析的治理截止；实际为 `2026-08-28T00:00:00Z`，期待为 `2026-08-25T23:59:59Z`。建议恢复 `cutoff_at=boundary.cutoff_at`，与既有归一化契约一致。 |
| 7 | `test_initialize_replays_an_idempotency_key_for_later_supported_cutoff`；[test_company_research_initializer.py:545](../../backend/tests/underwriting/test_company_research_initializer.py#L545) | 与 #6 同根因。截止进入 [preview canonical payload:531](../../backend/app/underwriting/domain/company_research.py#L531)，次日请求因此产生不同 input hash，在 [initializer:301](../../backend/app/underwriting/services/company_research_initializer.py#L301)被判为同幂等键绑定其他请求。应修正截止归一化，不能放松幂等冲突检查。 |
| 8 | `test_dump_openapi_includes_underwriting_routes_without_touching_default`；[test_openapi_dump.py:187](../../backend/tests/underwriting/test_openapi_dump.py#L187) | 在调用 OpenAPI 导出脚本前读取已删除的 `frontend/openapi.json` 即失败；当前结果不能说明导出脚本或 underwriting schema 有错误。建议以临时默认输出/受支持的当前产物检查“不改默认文件”，避免绑定已退休前端资产。 |
| 9 | `test_archive_contract_and_ui_sources_do_not_introduce_investment_fields`；[test_openapi_dump.py:541](../../backend/tests/underwriting/test_openapi_dump.py#L541) | 读取已删除的 `frontend/src/data/underwritingResearchApi.ts` 失败；`ResearchArchivePage.tsx` 同样缺失。在此之前后端 OpenAPI 的禁止字段断言已通过。应将 UI 内容检查迁移到仍受支持的页面；后端 schema 断言应独立保留。 |

### 提交与因果证据

- `git log --diff-filter=D` 和 `git show 24a38c24` 确认 **2026-09-02 的 `24a38c2414188ab4da67f781f1ef972dbc4dc261`（`chore: retire legacy frontend`）**已经删除 #1/#2/#5/#8/#9 涉及的 8 个 Docker、nginx、OpenAPI、脚本和旧页面资产。它们在本次审计开始前的 HEAD 中就不存在。
- `git show 6727a928:frontend/package.json` 确认 #4 的两个命令在 `feat: rebuild frontend as FundClaw workbench` 之后已经不存在；本次 package 差异只涉及依赖升级，没有删除这些命令。
- 同一 `24a38c24` 的代码差异明确把 initializer 第 199 行从 `cutoff_at=boundary.cutoff_at` 改为 `cutoff_at=requested_cutoff`。当前该服务、其领域类型和对应测试均没有未提交改动。该改变与 [2026-08-28 历史边界设计:66](../superpowers/specs/2026-08-28-company-research-historical-basis-recovery-design.md#L66)“请求晚于 fixture 时按 fixture 截止归一化”的要求冲突。
- #3 的 CLI 和测试均没有本次修改，最近相关提交为 `d82599f2`。当前解释器为 **Python 3.13.3**、`sys.getrecursionlimit() == 1000`；直接解析实验中 1000/2000/3000/4000 层数组均被 `json.loads` 接受。解析成功后第一层子元素是 list，因此被容器字典结构校验立即拒绝。此次仅证明测试文案预期不便携，没有进行极端深度的资源压力测试。

### 精确重现与范围

所有单项重现均在目标 worktree 的 `backend` 目录，以以下隔离前缀运行：

```sh
env -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test .venv/bin/python -m pytest
```

- 6 个资产相关 node ID 合并重跑：**6 failed，2 warnings，3.17 秒**；错误与全量日志一致。
- 深 JSON 单项重跑：**1 failed，1 warning，0.11 秒**；返回码仍为安全拒绝的 2，唯一失败是 stderr 分类文案。
- 两个 initializer 单项由独立子审重跑：**2 failed，0.92 秒**；随后只在测试进程内将 preview 构造参数恢复为治理截止，再跑原两项：**2 passed，0.46 秒**。没有编辑文件；此单变量实验用于定位因果，不代表已交付代码修复。

建议优先级：**P2 修正研究初始化的治理截止回归**；**P2 明确旧前端退役后的部署/验收资产支持矩阵并迁移 6 项测试**；**P3 去除深 JSON 测试对解释器递归阈值及单一错误文案的依赖**。本次只归因，没有用删除断言、跳过用例或恢复大量旧前端文件来掩盖失败。
