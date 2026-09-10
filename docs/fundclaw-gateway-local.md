# FundClaw 对话启动与实时事件流

## 当前实现（2026-09-08，整栈验收进行中）

网页发送研究请求，Gateway 调用现有 automatic research intake，原子保存私有对话、原文、意图、冻结范围和原生 Case/Run 引用。后台继续使用现有研究与采集 worker，不引入第二套 OpenClaw 执行引擎。

四个专业角色现在使用持久化任务和独立 worker。产业与财务可并行，策略绑定两者的产出版本，AI 质控绑定前序产出并执行六项检查。原生执行阶段仍单独展示；角色产出会记录模型尝试、用量、输入指纹、依赖和冻结证据引用。当前权限失效或引用校验失败时不展示正文。

首次输入用于发起研究。已有会话的普通指令发送给选定角色或全体团队，生成后继任务版本；`调整范围：新的范围` 仍创建原生研究的新轮次，旧 Case/Run 不被改写。专业团队支持暂停、恢复、取消和失败重试，写入要求幂等键及预期版本。暂停只影响专业任务；取消还会终止仍活跃的原生研究。断开 SSE 仅停止观察。

人工复核单独保存，绑定当前四个产出 ID、团队版本和服务端认证主体。AI 质控不能代替人工复核。页面保留历史任务和复核记录；审核合成验收数据不表示真实投资研究通过。

新增迁移为 `0075`，历史任务定义、产出、调用、依赖、事件、回执和审核均有数据库保护。下方有日期的既往验收记录描述当时版本，不能用于判断当前角色能力；本轮完整验收结果另行记录。

## 必须使用隔离服务与独立数据库

启动入口是 `app.gateway_main:app`，**不是** `app.main:app`。

旧应用仍有全局文档/Case 查询，信任边界不等于私有 Gateway。专用入口只挂载 Gateway、研究身份和健康检查；旧应用拒绝 Gateway 请求。必须给专用 API、研究 worker 和采集 worker 配置同一个**新建的独立数据库**，不得让旧应用同时连接该库。

API 在非测试环境强制要求 `GATEWAY_DATABASE_URL`，不会默认落到原数据库。worker 与 Alembic 使用 `DATABASE_URL`，应显式设为相同的 Gateway 库。不要在现有生产数据库上试跑以下步骤。

## 本地启动

在独立 checkout/worktree 中安装现有 backend/frontend 依赖。下面各进程在 `backend/` 目录启动，凭据只配置在服务器环境，不提交 `.env`。

1. 配置独立数据库 `GATEWAY_DATABASE_URL`。多进程 API、研究和采集联调使用新建 PostgreSQL 数据库；SQLite 仅用于轻量本地验证，不作为部署建议。
2. 配置 `LLM_API_KEY`，以及供应商需要的 `LLM_BASE_URL`、`LLM_MODEL`。真实运行不会在缺少模型凭据时降级为假研究。
3. 配置 `RESEARCH_TENANT_TOKENS`，形状为 `{"<opaque-token>":{"tenant_id":"team-a","subject_id":"researcher-a","roles":[]}}`。必须有稳定 `subject_id`，仅有 tenant 的旧令牌不能使用 Gateway。
4. 如需外部采集，按项目现有采集配置设置供应商凭据与来源权限。当前原生 `B_SCOPE_POLICY` 要求 `gildata,sse,szse` 三个适配器，所以需设置 `ACQUISITION_ENABLED_ADAPTERS=gildata,sse,szse` 和 `GILDATA_TOKEN`。只使用 worker 的开发默认值 `sse,szse` 会因不满足冻结策略而以 `adapter_configuration_invalid` 拒绝采集；不得为绕过此校验随意放宽来源策略。

```sh
DATABASE_URL="$GATEWAY_DATABASE_URL" .venv/bin/alembic upgrade head
.venv/bin/uvicorn app.gateway_main:app --host 127.0.0.1 --port 8018
```

在另外两个同样加载服务器配置的终端启动 worker：

```sh
DATABASE_URL="$GATEWAY_DATABASE_URL" .venv/bin/python -m app.scripts.run_research_worker --loop
```

```sh
DATABASE_URL="$GATEWAY_DATABASE_URL" ACQUISITION_ENABLED_ADAPTERS=gildata,sse,szse .venv/bin/python -m app.scripts.run_acquisition_worker --loop
```

在 `frontend/` 的服务器环境或不提交的 `.env.local` 中设置 `GATEWAY_PROXY_TOKEN`，值对应前面 `RESEARCH_TENANT_TOKENS` 中选定的单人身份令牌。不要使用 `VITE_` 前缀、不要提交真实令牌。然后启动同源代理：

```sh
GATEWAY_API_BASE=http://127.0.0.1:8018 npm run dev -- --port 5178 --strictPort
```

打开 `http://localhost:5178`，页面自动确认研究员身份，刷新后也不需要填写令牌。凭据只由本地服务端代理持有，浏览器不保存或发送 Bearer token，也不写入 URL、localStorage 或前端构建变量。缺少服务端身份配置会显示页面内配置提示和重试按钮，不会绕过后端鉴权。

此桥接仅用于本机单人工作台，信任本机 OS 用户和能访问该回环服务的本地进程；不是共享机器上的多用户登录方案。前端拒绝非回环监听、非回环请求、不可信 Host/Origin 和跨站 Fetch Metadata；上游只能是字面量回环 HTTP 地址，代理只允许 Gateway 的既有路径/方法。浏览器自带的 Authorization 不可选择研究主体，也不能借此访问旧应用接口。不要将 Vite 暴露到局域网/公网或转发给其他用户。

生产构建及 `npm run preview` 不提供这个本地身份代理。多人部署应使用正常登录/SSO，由受控同源认证代理按用户注入身份，并禁用 SSE 响应缓冲。专用 API 仍验证每个请求的身份，仍不开放跨域凭据访问。

仅启动网页/API 而没有 worker 时，研究会真实地保持排队；不能把排队动画当成 AI 已开始。只有研究 worker、没有采集 worker 时，来源工作会等待采集执行。

## HTTP 与恢复语义

所有对话接口均要求 Bearer 身份；写请求还要求 `Idempotency-Key`。

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/research-conversations` | `{initial_message}` 发起研究 |
| `POST /api/v1/research-conversations/{id}/messages` | `{text}` 提交范围调整或取得拒绝回执 |
| `GET /api/v1/research-conversations` | 仅当前主体的私有会话 |
| `GET /api/v1/research-conversations/{id}/snapshot` | 安全快照与原生状态补偿投影 |
| `GET /api/v1/research-conversations/{id}/events?after_sequence=N` | 可恢复 SSE |
| `GET /api/v1/research-conversations/{id}/runs/{run_spec_id}/research` | 当前获授权的研究草稿及证据摘要，最多 200 条，明确返回截断与总数 |
| `GET /api/v1/research-conversations/{id}/runs/{run_spec_id}/evidence/{evidence_link_id}` | 单条证据的冻结原文摘录、来源类别、时间、定位与版本 hash |
| `GET /api/v1/research-conversations/{id}/runs/{run_spec_id}/tasks/{task_id}/trace` | 真实来源任务最近 100 条阶段记录及分类异常，明确标注历史截断 |
| `POST /api/v1/research-conversations/{id}/runs/{run_spec_id}/commands` | `{kind}` 控制回执；P0 安全拒绝未接通控制 |

成功启动仍返回七个字段：`conversation_id`、`intent_id`、`run_spec_id`、`native_case_id`、`native_run_id`、`status`、`latest_sequence`。拒绝意图使用单独的 `receipt_kind: intent` 回执，不伪造原生 Run。

网络超时意味着结果未知，重试必须保留**同一个 key 与同一个正文**。相同 key/不同正文会冲突；尚未过期的执行租约也会返回冲突，防止重复调用供应商。私有会话不对其他主体公开，即便对方属于同一 tenant。

SSE 使用同源 fetch 流，Authorization 由受控服务端代理在转发时附上。`role_event` 帧带单调递增序号；重连从最后应用的序号补读。`Last-Event-ID` 与 query 同时给出时必须相同。保留窗口缺口或证据展示权限变化会要求重新读取完整快照，不能静默跳过。权限失效会终止流并清除受保护视图。每轮查询独立开关数据库会话，不把事务挂在长连接上。已追平的连接会立即发送受权心跳，之后空闲时每 15 秒发送一次。

事件仅含固定类型、状态、安全摘要、原因码和重新授权的 artifact ID。证据必须属于冻结范围及受权原生采集链，且当前允许展示；历史引用也重新检查。原文正文、工具参数、提示词、模型推理、凭据和异常堆栈不进入 SSE。草稿引用仅在原生完成链验证通过后发布，不代表人工审核通过。

## 验证与待办

### 2026-09-06：过程主导的并列证据工作台

本轮调整正式研究页的阅读结构，替代下方历史记录中的三标签布局。中央保留「执行过程 / 研究结果」切换，右侧「证据与来源」常驻当前研究权限边界。点击任务证据或分项判断依据，不再切走中央内容；原文在右侧顶部按实际 ID 读取，关闭后返回触发位置。窄屏改为上下阅读并支持定位与返回。

任务关联使用来源任务 ID；分项判断关联使用冻结评估的实际输入 ID 集合，两者不能混为同一种任务。右侧明确显示当前过滤范围，允许恢复全部证据。输入集合不是逐句引文映射，检索方向也不是已完成的支持或反驳核验。

运行状态、任务阶段、异常和重试信息来自既有投影；来源心跳与任务进展分开。专业角色仍为规划说明，不显示虚构的独立角色活动。共同质量说明收拢，实际依据入口优先，原始报告及判断理由保持原样。没有新增后端路由、模型调用或研究重跑。

设计和验收要求见 [工作台设计](superpowers/specs/2026-09-06-gateway-research-desk-design.md) 与 [实施计划](superpowers/plans/2026-09-06-gateway-research-desk.md)。

验证：前端 158 项通过，类型检查、构建、独立 SPEC/QUALITY 审查通过。既有真实案例 3 项判断、7 条证据的原文联动、桌面滚动保持、窄屏定位/返回、刷新和失败状态通过；原始报告与 4161 条历史事件未改动，无新增研究写请求。大历史快照首次加载仍可能超过 15 秒，此次没有优化后端读取延迟。运行中状态更新使用自动化事件帧测试验证，不代表本轮重新执行了真实研究。

### 2026-09-05：结论级溯源与只读质量检查

「研究结果」增加结果摘要与逐项判断，先展示原 AI 的支持、反驳和证据不足项数。每项保留原判断理由与缺口，并通过最终轮结果任务的实际 assessment / snapshot 关联展示「查看本项依据」，可直接打开对应冻结原文，不解析报告中的 Link 编号来猜引用。

新增质量检查与原 AI 判断分开展示：数据期混杂或缺失、仅有用户材料、未包含官方原始披露、来源独立性尚未核验、检索方向尚未验证为实际支持或反驳关系。单项提示只检查其实际输入；整份报告的提示检查全部输入，因此不能用另一项有官方来源来消除本项只有用户材料的风险。不同数据期是比较限制，不直接判定资料错误，也不猜测标准化目标年份。检查不产生可信度评分或「审核通过」结论。

原始报告保留在「查看原始报告」，正文、发现和局限不被重写；关联无法完整校验或证据列表超过读取上限时，明确显示关联不可用并保留当前有权阅读的原报告。新增字段 `assessment_review` 与现有研究响应一起授权读取，不增加写接口或独立存储。旧服务缺少此字段时保留原报告阅读；格式错误的新增字段不会被当作兼容缺省而忽略。

实施及验收见 [结论级溯源计划](superpowers/plans/2026-09-05-gateway-assessment-review.md)。本轮不新增模型调用、研究任务、专业角色调度，也不把历史 AI 判断转为人工审核结论。

本轮验证：前端 152 项、后端隔离 SQLite 189 项通过，类型检查、生产构建和独立复核通过。真实 PostgreSQL 在强制只读事务中读取了 3 项评估与 7 条证据，监测到零写入；真实浏览器完成三项原文下钻、原报告展开、桌面/手机布局及刷新验证。更新前后原始报告内容哈希一致。

### 2026-09-05：过程、溯源与结果阅读闭环

研究页增加「执行过程」「研究结果」「证据与来源」三个视图。运行中的研究默认显示过程；结束后默认显示结果，失败、取消、尚未生成及当前无权展示均有明确状态。已有研究直接读取账本，不要求重新运行。

结果页可进入按原生研究论点分组的证据，再打开单条证据的 exact quote、来源类别、数据期、材料发布日期、冻结定位与版本标识。用户材料保持 `user_supplied`，不冒充外部独立来源；冻结用户材料的第一页不是用户文字声称的公司报告页码。支持、反证等关系目前来自检索任务方向，不代表已经验证的支持或反驳关系。报告保持历史草稿内容及「系统生成，未经人工审核」标签，不能据此宣称已完成可信财务核验。

任务行可以按需展开实际持久化阶段、分类异常与下一步建议，并查看本任务关联证据。只有原生研究仍活跃、任务确处于已排期重试状态时才提示等待重试。历史默认最多 100 条，异常计数不是独立证据数量；未准入或未获展示授权的来源不会泄露标题、正文或原始提供方异常。

三个读取接口保持私有会话 owner、tenant、case、run、冻结 cutoff、采集绑定和当前来源合同校验，成功响应为 `Cache-Control: no-store`。详情懒加载，正文不进入 SSE；查询失败不能拼接旧报告与缺失证据，历史 run 不能显示 case 当前其他 run 的报告。本地身份代理只新增这三个精确 GET 路径，不扩大写操作或暴露旧文档 API。

本轮实现及验收记录见 [过程、证据与结果计划](superpowers/plans/2026-09-05-gateway-process-evidence-result.md)。四个专业角色的独立执行、自然追问和可执行任务控制仍不属于本轮交付。

此前验收：前端 130 项、后端 SQLite 151 项通过；另一次配置专用 PostgreSQL URL 的 112 项重复回归也通过，但复查发现 `cmd_session` 固定使用 SQLite，不能把该次计数当作 112 项 PostgreSQL 覆盖。类型检查、生产构建及独立质量复审通过。真实本地浏览器已验证原茅台案例的 7 条证据、原报告、任务历史、桌面/手机布局及刷新恢复，没有新建研究。开发时一直打开的旧标签页建议完整刷新一次；读取重试不会重新执行研究。

### 2026-09-05：材料卡死与执行不可见修复

本次实测 Google 新闻研究存在叠加故障：PostgreSQL 不支持 JSON 等号查询，材料处理异常使采集进程退出；Gateway 又未识别合法的“先处理材料、外部任务尚未绑定”状态，隐藏了实际进展。恢复后继续发现冻结请求缺少公司主体，必然无法通过语义准入；Gildata 研报也漏填了可信提供方标识。

已补 portable locator 重放匹配、独立任务异常的有限重试/失败与 lease fencing、材料优先状态的严格授权校验，以及真实执行快照和 `execution_progress` SSE 帧。该帧没有事件 ID、不推进角色游标，每次替换前都重新授权；不包含原文、模型推理或原始异常。页面展示来源任务、实际调用的提供方、阶段、次数、重试时间、真实计数及心跳；服务在线不代表每个任务都活跃，材料处理不代表财务数字已核实。长队列与历史日志折叠，移动端侧栏不再遮挡进展。

缺主体请求现在以 `research_subject_missing` 明确失败，不再调用外部提供方或无限重试。入口会对未取得原文字面公司名的模型响应做一次有界重检，不推断股票代码/年份；可以显式写 `研究主体：公司名称；待核验材料：原始材料`。旧范围已冻结时，在会话发送 `调整范围：研究主体：公司名称；待核验材料：原始材料` 创建有父级记录的新一轮，不会改写旧研究。公司别名、相关证券代码、期间和指标仍必须满足证据准入，不因修复而放宽。

原 Google 研究已保留原文与历史并明确失败结束：50 项任务全部终态（9 部分完成、41 失败），无活跃租约、无授权准入证据。真实模型只读复测能提取 `谷歌`，没有创建重复研究。当前来源仍为 Gildata、上交所、深交所，缺少美国官方披露适配器，不能据此宣称已验证 Google 财报或产出报告。

验证：PostgreSQL 15 个专项文件 **403 通过**；研报出版身份/冻结/worker 补测 **164 通过**（与前述测试重叠）；前端 **76 通过**，类型检查与生产构建通过。研报去重继续保留出版机构与历史键，未因补齐 Gildata 渠道身份而混淆不同机构报告。真实浏览器确认 SSE 自动更新、刷新重连、明确原因展示，以及 1440/884/390px 下的进展可见性。详细修复与补测记录见 `docs/superpowers/plans/2026-09-05-research-execution-repair.md`。以下记录属于此前联调，测试集存在重叠，不相加为独立总数。

后端回归入口：`tests/test_research_gateway_*.py`、`tests/test_gateway_runtime_isolation.py`、`tests/test_operational_access_api.py`，以及原生 automatic research/worker 回归。前端使用 `npm test` 和 `npm run build`；人工联调验证首次发送、原生任务更新、断线重连及四个专业角色未伪绑定。

2026-09-05 自动本地身份补充验证：前端 57 项测试通过（包括真实 HTTP 代理、跨站/路径拒绝、缺少配置、预览入口关闭及未结束 SSE 的即时交付），类型检查与构建通过。给构建进程注入专用测试标记后，客户端产物不包含该标记或代理身份配置。真实浏览器首次打开与完整刷新后均自动显示研究员，历史快照/事件流恢复，浏览器请求不含 Authorization，页面没有令牌按钮或密码输入。运行中服务实测：自动身份请求 200，敌对 Origin 403，旧接口路径 404，直连后端无凭据请求仍为 401。

2026-09-05 实际联调记录：

- 在隔离 PostgreSQL 上，通过网页和真实模型创建私有对话与原生 Case/Run；运行研究 worker 后，网页未经手动刷新即收到来源任务事件及等待来源状态。
- 原生模型提取提示已明确 JSON 键与类型，修复供应商返回 `factors` 等别名导致启动被拒绝的问题；仍严格拒绝无效输出，不降级为模拟结果。
- 暂停请求取得明确拒绝回执，不会新增或取消原生研究。当前页面中的未知投递重试保留原文和同一个 key；刷新页面会清除内存，请先核对已有对话，不能把刷新当成取消或确定未送达。
- 最终核心后端回归：281 通过；PostgreSQL Gateway 专项：195 通过、3 跳过，后续流与隔离入口补测 20 通过、worker/adapter 补测 24 通过；前端 28 通过、类型检查及构建通过。这些测试集有重叠，不应相加为独立测试总数。
- SQLite API 与 worker 并发复测：既有真实研究恢复后，原生状态推进至 `waiting_for_sources`，开放的 SSE 从旧游标 9 收到序号 10。仅对 Gateway 绑定的 automatic SQLite worker 预先取得写事务，保留旧工作流并发行为；真实模型调用前仍释放事务。
- 全仓首次回归：4489 通过、41 跳过、9 失败。9 项失败已在干净基线 `6727a928` 完整复现：旧前端 Docker/验证脚本/OpenAPI 资产缺失、旧深层 JSON CLI 错误文案及 underwriting cutoff 预期不符，非本轮引入；未改写这些旧功能来掩盖失败。

这次联调样例最终以 `no_usable_evidence` 结束：首次启动的采集 worker 缺少冻结策略所需的 gildata 适配器。已将本地启动配置对齐三个来源，并验证适配器身份与策略一致；未重写失败历史或伪造成功。尚未产出经准入可展示的真实证据或最终研究报告；证据与草稿的授权、失效撤回由使用真实原生准入链的自动化测试验证。不能把有事件、已排队或单次采集结束视为完成研究。

此前待办中的受权证据详情与结果阅读，已由上方「过程、溯源与结果阅读闭环」交付。仍需独立开发：专业角色任务归属与调度、对既有证据的问答、可执行的暂停/取消/重试、团队共享。此版本不是完整的多专业 AI 投研执行系统，也不宣称复用了外部 OpenClaw 运行时。

## 2026-09-07：构建产物的本机容器交付

新增 `docker-compose.gateway.yml` 与 `scripts/gateway-runtime.sh`。这是当前网页的容器入口；旧 `docker-compose.one-click.yml` / `one-click-runtime.sh` 属于历史后台环境，不是当前 Gateway 网页的交付入口，不能混用数据库、身份配置或旧页面验收脚本。

支持单机单主体：Node24 服务静态构建产物，凭据仅通过容器运行时的 `GATEWAY_PROXY_TOKEN` 配置。Compose 只把网页端口发布在 `127.0.0.1`，API/PostgreSQL没有宿主机端口；网页代理校验精确本机 Host/Origin 和跨站请求标记，只转发明确的 Gateway GET/POST 路径。客户端 Authorization 不会改变主体，历史文档/Case API不可通过此代理访问。SSE立即发送上游头和数据，断开下游时关闭上游请求，45秒无数据才超时；当前后端每15秒心跳。

```sh
cp .env.gateway.example .env.gateway.local
chmod 600 .env.gateway.local
# 在编辑器中填写服务端凭据，不把文件提交到版本库。
scripts/gateway-runtime.sh validate
scripts/gateway-runtime.sh build
scripts/gateway-runtime.sh up
```

`GATEWAY_DB_PASSWORD` 使用URL安全随机值（例如十六进制），`RESEARCH_TENANT_TOKENS` 使用前文的JSON对象格式并包含稳定 `subject_id`；`GATEWAY_PROXY_TOKEN`必须恰好对应其中一名主体。默认 `GATEWAY_PUBLIC_PORT=8080`，浏览器打开 `http://localhost:8080`。改变端口需同步此变量；不要改成公开监听或把服务转发给其他机器。

专用Compose使用自身项目命名的 `gateway-data` / `gateway-files` 卷，API入口为 `app.gateway_main:app`，迁移始终 `alembic upgrade head`。API、研究、采集和专业角色worker共享同一专用数据库；采集适配器固定为 `gildata,sse,szse` 并要求相应Gildata配置，不降级为模拟研究。`migrate` 是唯一声明 backend `build` 的服务，其他 backend 服务只引用同一 `fundclaw-gateway-backend:local` 镜像，避免并行导出同一 tag。专业worker service支持水平复制，脚本 `up` 等价于 `docker compose ... up -d --build --scale professional-worker=2`；每个副本通过 `professional_team` 容器级心跳接受健康检查，连续loop异常会主动标记不可用并退出交由Docker重启。

`status`检查本项目状态；`down`停止本项目并保留数据卷。启动可能产生真实研究/模型费用，本文档的构建与离线验证未执行 `up`。首次部署必须使用新卷，不能把已有业务卷挂入。此服务信任本机OS用户及容器运行环境，不提供多人登录/SSO；公网/团队共享应单独实现逐用户认证代理，不允许复用单人固定令牌。

本轮HTTP验收使用 `node --test frontend/server/gatewayServer.test.mjs`，由随机回环端口的合成上游验证身份覆盖、路由拒绝、跨站拒绝、实时SSE与断开行为；它不声称验证了已退休的公司Case浏览器流程，也不连接真实模型。
