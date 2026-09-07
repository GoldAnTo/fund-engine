# 完整修改路线：本批实施进展

2026-09-06。接续首次检测报告与用户“按照修改路线开始修复”的授权。完整目标仍在进行；本文件记录已落地内容，不将局部验收替代整个产品闭环。

## 已实施

1. **真实前端入口**：默认同源事件API，显式 `?client=mock` 才是演示。列表、创建、详情、URL恢复、取消过期读取、可恢复错误；失败不显示示例研究。按当前Case隔离操作状态，校验响应基本结构/Case身份。中文状态与本地时间、手机堆叠及键盘跳转。
2. **研究操作首片**：补充材料真实入库；读取准备/运行记录、重试失败准备步骤；只有完整可见且已审协议/计划才显示计划授权。使用真实revision、plan sequence与稳定授权key。未接入的审核/发布流程明确显示边界。
3. **代理认证**：开发代理服务端读取 `RESEARCH_BEARER_TOKEN`，浏览器无需携带宿主Bearer；配置说明使用 `.env.local.example`。构建使用人工合成令牌验证，产物中不存在该令牌。开发代理只代表宿主配置的单个研究空间，多用户部署仍需宿主认证网关。
4. **创建幂等**：可选 `Idempotency-Key` 按可信tenant隔离；同内容重放原201，异内容409，非法key422。Case、来源admission、准备任务与响应cache同事务；失败完整回滚。前端只在sessionStorage存payload哈希/key，不保存原文，成功后清理。原201的lifecycle为创建时快照，页面随后读取最新工作台。
5. **权限闭环首片**：job读/events/取消/重试验证Case所有权；proposal/link/assessment审核解析真实Case与tenant；集合在LIMIT前过滤。共享文档仅返回当前Case引用。owner审核不会关闭foreign/NULL Case伪造同ref任务。全局文档、engine/causal、task/activity及共享目录仍待按矩阵实施。
6. **LLM恢复策略**：永久错误不重试，瞬态错误有限抖动退避并尊重Retry-After；配置有限范围校验；proposal/assessment严格必需字段与元素校验。评估和合规rewrite共用操作预算，迟到结果拒绝；同步I/O仍无法硬取消。CLI已知provider失败固定stderr、非零退出，不输出上游异常cause。
7. **可重复验收**：新增临时SQLite/Uvicorn/Vite真实HTTP浏览器入口测试及CI job；新增独立PostgreSQL连接并发幂等回归。重新生成OpenAPI。旧Company Research完整浏览器门禁仍保留，不以新入口测试冒充恢复。

## 最新验证

| 验证 | 结果 | 范围 |
|---|---:|---|
| 后端合并定向回归，17个文件 | 427 passed / 7 skipped | AI、事件、任务、审核、文档、worker、自动研究与流程 |
| 新审核边界与CLI安全测试 | 12 passed | owner/foreign/anonymous、拒绝无副作用、subprocess stderr |
| 隔离PostgreSQL（迁移至0070） | 63 passed | worker取消/完成竞争、job与review权限、文档、创建 |
| 独立PostgreSQL幂等竞争 | 3 passed | 同key重放、异内容冲突、winner回滚后接手；pg_blocking_pids确认真实锁等待 |
| 前端单元测试 | 30 passed | 真API/演示/动作/幂等/状态展示 |
| 演示桌面与移动浏览器 | 8 passed | 搜索、内容隔离、键盘、响应式 |
| 隔离真实HTTP浏览器 | 3 passed | 创建→数据库读回→刷新→补材料，401边界，键盘焦点 |
| TypeScript + Vite生产构建 | passed | synthetic代理令牌未进入3个产物文件 |
| git diff --check | passed | 格式检查 |

以上不同组有重叠，不能相加当作独立测试总数。本批没有再次跑原先约14分钟的全量套件；首次全量结果与三个旧前端闭环缺失见首次检测报告。PG首次组合运行发现worker测试未清理独立连接提交的数据，已让它使用session teardown并完整重跑63项通过。

未使用真实研究数据或在线付费模型；临时PG只用于本批测试。浏览器桌面/手机截图已检查，无横向溢出，跳转链接不再绘入未聚焦的整页截图。此覆盖不等于200%缩放/屏幕阅读器完整认证。

## 下一批仍须完成

- 剩余路由权限矩阵与全局文档/共享资源命令策略；job重试需进一步按实际worker类别核对恢复语义。
- 陈述审核、协议编辑确认、证据审核、运行取消与事件、发布、冻结回放和真实导出；恢复Company Research完整live verifier。
- usage、调用尝试/延迟与任务成本归因、输入输出预算、固定引用质量评测。
- 代表性数据分页/查询计划，解析资源边界，readiness/指标告警，200%缩放与阅读器验收。
- 完整集成后再跑全量后端和完整产品浏览器门禁，保留真实失败，不添加占位脚本或跳过旧契约。

完整状态：[路线执行跟踪](../superpowers/plans/2026-09-06-roadmap-execution.md)。


## 后续批次：engine权限与任务分页

- engine rerun/propose、causal step/edge 均已补可信租户与Case校验；因果边两端不能跨URL thesis。无权限请求在模型构造和任何账本写入前停止。已有正常研究流程回归保持通过。
- 任务事件分页修复：1003条历史记录、请求limit=3时，旧实现加载1003个ORM对象；红测复现后改成SQL有序LIMIT(limit+1)，现在最多加载4个对象。验证连续第二页、精确末页和空页，不把局部加载优化当作全站性能结论。
- job相关定向24 passed；独立engine/causal/jobs审查六文件69 passed/1 skipped。后端新全量套件已启动，结果完成后追加。


## 最新全量测试与生产准备链修复

新全量运行完成：**4264 passed / 6 failed / 41 skipped，700.52秒**。其中两项来自假OpenAI响应缺少finish_reason，已按真实成功响应补stop并保留严格客户端校验；一项来自scope被误改为模型必填字段，现保留既有Case派生scope规则，同时显式非法scope仍拒绝。recall/schema/AI engine/preparation integration合并回归72 passed。其余三项仍为Company Research verifier、旧事件完整verifier和研究归档UI源文件缺失，未加占位或跳过。

真实浏览器接入准备worker后额外复现生产会话bug：`autoflush=False`时，start_system_step写入的running状态在append_event重新读取时被populate_existing覆盖，Job随后误取消而准备状态永远queued。现先取得Case锁、flush已有修改，再保持原有preparation锁与刷新；不在repository提交事务。新增True/False两种会话全链参数化；独立审查29项通过，并临时还原旧实现再次证实红测。PostgreSQL新增锁交错尚未在本批复测。

前端已接逐条陈述确认/修订/拒绝与协议分组编辑确认。强制完整候选ID集、来源可展示、人工决定/理由、revision与draft_sequence。协议校验覆盖正式结果结构、基线来源/单位/实体范围及带时区可用时间；支持合法数值0，不把合法Case派生规则误当模型错误。前端51单测和构建通过；真实Uvicorn+生产Session配置+独立prepare worker的浏览器4项通过，包括创建→生成候选→人工提交→数据库状态读回→刷新。截图已目视检查。

协议的完整真实浏览器确认仍需合法机制/来源fixture，当前默认mock协议刻意不足以授权，页面会拒绝确认。不能据此宣称发布、回放、导出完整闭环已恢复。

机器可读记录：[roadmap-validation.json](2026-09-06-roadmap-validation.json)。完整目标保持active。


## 后续批次：服务就绪状态

新增 `/ready`，与 `/health` 进程存活检查分开。只读检测数据库连通及当前checkout全部Alembic heads，缺失/过期返回503，成功200，响应不包含连接串、异常正文或数据库版本详情，禁止缓存。没有把worker活跃度或外部模型可用性混入此检查。

先复现3项缺接口失败，再完成readiness+health五项回归。临时PostgreSQL真实迁移到当前head后200，改为过期版本后503，/health保持200，临时容器已清理。该测试不证明网络故障下严格响应时限；仍受数据库连接/池等待设置影响。


## 文档库读取隔离与就绪复核

文档库默认列表/详情现仅向已认证租户展示自有已准入Case附件；共享文档去重，引用不含外租户Case，跨租户游标拒绝。具体source/upload/content-quality正向测试显式建立归属，没有全局自动admit。广泛相关回归185 passed/1 skipped，独立审查24 passed，临时PostgreSQL实际读取/隔离回归24 passed。原有Case文档路径继续保留其附件和来源展示限制。

readiness补充空迁移头、多余分支头与禁止缓存断言后，health/readiness七项通过，独立审查当前源码/Docker部署无阻塞发现。该入口假设部署包包含与app同级的alembic目录（现有Docker明确复制）；仅安装不含迁移资源的wheel不在这次已验证的部署形态中。

临时PostgreSQL与连接均已清理。文档写入、task/activity策略、完整发布回放与成本/性能其余项仍按路线继续；没有据此将完整目标标记完成。


## 文档写入权限修复

已复现并修复匿名抽取进入模型构造、外租户创建补充正文的问题。supplements先认证、校验Case归属与原文附件关系，再执行合同/冻结/写入；extract只允许自有已准入Case附件，显式case_id进一步收窄，未附属文档拒绝。共享源抽取仍只生成或复用不可变候选，不自动批准陈述或推进任何Case。

新增8项权限和副作用测试覆盖匿名、外租户、未附属文档、同租户错误Case、共享源不推进；与engine commands/event research合并回归88 passed/1 skipped。独立只读审查无阻塞发现，建议的同租户错误Case场景已补测通过。OpenAPI已重新生成。本批未调用真实付费模型，未重新跑全量或PostgreSQL写入测试。

下一片仍需处理task/activity访问与共享资源策略，完整产品闭环和成本/性能路线保持未完成。


## LLM 输入与响应解析边界

客户端新增 UTF-8 序列化消息 1 MiB、响应正文 2 MiB 默认限制，环境变量可配置 1 字节至 16 MiB 的整数上限。超大输入在模型调用前拒绝，响应在 JSON 解析/修复前拒绝；非法 Unicode 同样归一为固定模型错误，不暴露正文。先验证缺失限制的14项红测，后补Unicode两项红测并修复。最终16项新增测试通过，与retry/determinism/engine/recall/preparation/extraction合并183 passed；独立规格和质量复核通过。

响应已由SDK缓冲后才检查，该限制不等于HTTP接收内存上限、token费用上限或任务总预算；usage/cost归因仍未完成。未调用真实provider。配置说明已写入README。


## 任务与活动流权限、游标修复

`/tasks`读/创建/修改及`/activity`、`/evidence-changes`均要求可信tenant。任务必须归属自有已准入Case，可选引用必须通过数据库关系落在同一Case；拒绝发生在写入之前。列表先在SQL按归属过滤再LIMIT，空归属和孤立资源不公开。活动按真实事件发出者的aggregate解析归属，共享文档事件同时需要明确Case引用与持久附件，不能借共享原文读取别的研究活动。

游标改为与排序一致的(created_at,id)比较，修复原先按随机UUID单独比较导致漏页的问题。无效UUID422；不存在、外租户或不匹配当前筛选的游标404。保留原有内部无租户查询调用，所有公开接口显式传入认证tenant。提案目标一致性与审核接口共用，历史提案指向外租户、同租户另一个Case或不存在的thesis时，也不公开活动或接纳任务引用。

最终合并后端回归142 passed；临时PostgreSQL迁移后任务/活动与文档写入32 passed，readiness实际200→过期503通过，容器清理完成。规格及质量独立审查通过，OpenAPI更新，git diff --check通过。没有把这些局部回归当作全量通过；共享目录策略、完整研究闭环、成本审计及其他性能工作仍未完成。


## 运行详情、事件分页与取消闭环

研究运行卡片已接真实详情、阶段事件与更多历史运行。取消要求操作人和原因，仅在后端支持的queued/running/waiting_for_sources/waiting_for_review状态且最新详情读取成功后开放。取消后读取服务端状态再显示成功，错误保留原因；刷新失败不继续使用旧详情授权取消。切换研究/运行或卸载时中止请求，终态不能重复操作。

后端事件接口新增after_seq（非负）和next_cursor，按seq续读，修复原接口只有has_more却无法翻页的问题。保留run/tenant校验，移除每次事件翻页无关的完整任务/证据详情加载。先红测证明重复读第一页和额外详情查询，后端相关37 passed/2 skipped。

前端最终62单测通过、构建通过。隔离SQLite+真实API+准备worker+Vite浏览器5项通过，新用例使用实际服务创建排队运行及55条历史事件，页面翻页、填写取消原因、HTTP取消、数据库读回、刷新终态均验证。390px无横向溢出，移动截图已检查；不代表屏幕阅读器或200%缩放全认证。前后端独立审查通过，OpenAPI和diff格式检查完成。未调用真实付费provider，未跑本批全量后端或PG。

运行重试语义、完整协议/证据审核、发布冻结回放与导出仍待完成，不能把本片取消闭环当作完整研究闭环。


## 证据人工审核闭环与来源展示限制

新增真实待审核证据面板，展示来源/原文/定位、候选因素与AI提议，必须人工选择接受、拒绝或修改并填写理由。修改保留原始statement关联，明确输入角色和适用范围；来源不可采纳或禁止展示时不能接受/修改。提交版本及幂等key，验证服务端返回的决定和发布实体，失败保留输入；切换Case中止请求。审核可能推动后续研究，页面明确说明。

同时红测复现禁止展示的SourceContract仍在事件审核队列返回原文，已在共享上下文隐藏原文/陈述/文档信息并清空AI衍生reason/scope；禁止展示、未生效、过期三种情形均通过HTTP验证。完整legacy审核/原始proposal列表的展示策略仍需后续审计，不能将当前event队列修复作为所有旧读路径均已处理的证明。

本批66后端相关回归、69前端单元测试、生产构建通过。6项隔离真实HTTP浏览器通过，新用例实际审核冻结来源、生成正式证据实体、刷新队列并读回工作台证据；390px无横向溢出，截图已检查。测试中审核确实推进了后续研究，因此为审核和取消建立独立Case，避免共享状态互扰。无外部provider/真实站点访问。

前端由子代理实施、主代理复核并补矛盾来源标志拒绝测试；额外独立后端审查调用受运行时agent thread limit限制未完成，不能声称本片已有独立后端审查。主代理完成相关回归与代码复核，目标继续active。


## 旧审核读取入口来源限制

继续修复 `/review-queue` 与 `/review-proposals`：旧machine-generated证据在SQL分页前过滤禁止展示、未生效和过期来源；公开查询还要求原文已附属目标Case。原始evidence-link提案列表保留编号/版本等审核元数据，但隐藏受限payload/target_context并标记display_withheld，不再返回受限AI理由。

扩展三组合同红测和一个附件归属往返测试。初次附件测试的辅助函数按时间选择了错误的初始文档，已改为显式admission初始文档并断言不同于证据文档；随后临时还原旧查询，再次确认真实红测，恢复实现后通过。相关SQLite87 passed，临时PostgreSQL15 passed并验证迁移就绪状态；容器已清理，OpenAPI更新、diff检查通过。

本片是读取保护，不声称所有旧审核写命令的来源合同门禁均已完备；legacy link review的直接决定路径仍须后续核对。完整协议/冻结/导出、费用归因、共享目录及其余路线仍未完成。


## 旧证据审核写入的来源门禁

红测确认旧link review可直接采纳禁止展示、过期或未生效合同的证据。现ReviewService确认前验证文档属于目标Case，并应用合同AI处理/展示/生效日期门禁，覆盖HTTP与内部服务调用；拒绝和要求补证据保留，不自动采纳受限资料。历史无合同来源保持既有兼容，但需要明确附件关系。合法来源显式附属后仍可确认。

相关71项回归通过，临时PostgreSQL15项（含直接服务拒绝）通过，容器已清理。旧种子后来人工审核的正向测试显式建立附件，不修改种子账本或放宽生产门禁。

新一轮全量后端测试已启动，日志 `/tmp/fund-engine-audit-full-followup-20260906.log`，JUnit `/tmp/fund-engine-audit-full-followup-20260906.xml`。尚未完成，不声称全量通过；完成后更新最终计数和遗留失败。


## 全量测试期间的发布闭环核对

全量测试进程仍在运行（exec session 46647），未重新启动。已核对发布边界：事件研究`conclusion/publish`只生成EventResearchConclusion；公司研究`/api/underwriting/v1/product/company-research/projects/{id}`下另有workspace、evidence-reviews、judgment-confirmations、publication-preview、publish、revisions/{id}及其export。完整冻结与Markdown验收必须走后者，不能以事件结论发布代替。

历史commit24a38c24整体移除了旧前端（104文件），包括验收启动器、研究归档页面与API适配器。已从该提交父版本恢复独立with-project-node.mjs启动器，并修正为严格匹配.nvmrc主版本、按数值选择nvm补丁版本。默认shell实际执行返回24.15.0。未恢复占位verifier，未修改package来冒充完整验收；公司研究当前入口及其冻结回放导出仍待实施。


## 公司研究完整页面及冻结导出闭环恢复

后端全量续跑已结束：4334 passed、3 failed、41 skipped，667.67秒。失败为公司verifier package命令、事件verifier源文件、归档UI源文件缺失。该计数先于本批公司入口恢复，不能解释为当前仍有同样三项失败，也未伪称最终全量绿。

选择性恢复历史公司目录、搜索与创建、工作台、严格API校验、冻结回放与Markdown导出页面；当前FundClaw顶部可进入`/research`，公司导航可返回事件研究。公司路由和CSS按需加载，当前首页JS为210.58kB（gzip67.72kB），公司独立块166.15kB（gzip44.86kB）。适配当前noUncheckedIndexedAccess而不放宽编译选项，重新从当前OpenAPI生成类型。恢复初版路由依赖审计发现2项moderate，升级react-router-dom7.18.3后npm审计0项漏洞。

真实隔离公司verifier完整经过Alphabet创建、证据审核、判断确认、发布预览、冻结、重载回放和校验下载Markdown哈希；升级依赖和分包后再次通过。恢复的是完整脚本与真实业务页面，无占位通过。其支持库71项通过；首次与公司verifier并行导致全机进程快照把另一个验收栈误认为泄漏，顺序运行71项全过。以后这些进程归属测试须与其他live栈顺序执行。

最终前端278项单元测试通过（API94、公司工作台75、创建12、视图28，加既有69）；生产构建通过；真实浏览器7项通过，包含新公司入口桌面/390px无横向溢出，以及原有全部6项。截图位于frontend/test-results/company-create-{desktop,mobile}.png并已查看。恢复入口时曾替换“真实研究空间”标识导致旧浏览器断言失败，已保留真实状态标识并新增公司链接，全部复验通过。后端公司verifier测试49passed、1skipped；跳过的是pytest中显式开关控制的live包装测试，独立npm真实闭环已通过。

仍未完成：事件完整verifier与归档UI、完整准备协议的真实浏览器闭环、共享目录策略、运行重试语义、LLM用量/成本归因、其余性能与运维路线。未发布、未提交，未使用付费LLM服务。本目标继续active。


## 研究归档页面恢复

上一目标轮完成公司研究真实闭环与依赖升级，属于有状态进展。本轮基于当前文件核对剩余归档源文件失败，选择性恢复归档目录、历史版本时间线、相邻差异、候选证据与边界验证，挂载`/underwriting/research`及`/:objectId/:versionKind`，公司导航有明确入口和返回链接；模块独立懒加载。

移除旧归档API客户端的VITE_RESEARCH_BEARER_TOKEN及可变跨域基址，固定同源服务端代理，新增测试证明即使浏览器配置含令牌/外域也不会发送或使用。保留当前严格TypeScript选项，为有边界证明的数组读取增加断言，恢复59项归档行为测试。新增公司导航第四项后补手机换行，真实390px截图已查看，无横向溢出。

验证：前端338passed；生产构建通过，首页JS211.06kB/gzip67.83kB；后端OpenAPI/归档契约11passed，原归档源文件缺失失败消除；真实导航浏览器1passed（公司、归档空态、创建和返回），截图frontend/test-results/research-archive-mobile.png。本轮浏览器只覆盖归档目录空态，不代替有真实历史版本和证据差异的数据验收；相应行为当前由恢复的组件测试覆盖，真实数据浏览器仍待补。未重跑全后端或全部浏览器，未宣称全路线完成。


## CI覆盖公司完整闭环

上一轮归档页面恢复及验证属于实际进展。本轮检查当前frontend.yml发现仅frontend改动触发，且没有恢复的公司完整verifier。已增加backend/**与.nvmrc触发范围，将live-entry扩展为事件与公司完整验收，顺序运行e2e:live、71项进程隔离支持测试、公司审核冻结回放导出专项，保持任一步失败阻断。超时上限15分钟。同步前端README，删除公司完整验收仍缺失的过期说明，标明受控Alphabet范围和同机禁止并行的进程检测约束。

验证：YAML解析、触发集合、步骤先后和失败传播检查通过；本地顺序支持测试71passed（20.95秒），随后公司完整verifier PASS；git diff --check通过。未提交/推送，因此未声称GitHub托管runner已执行。原事件完整verifier还要求market factor与company-stock-fund链和定时计划操作，当前入口浏览器测试不足以代替，继续保留为待办。


## LLM单次生成预算

本轮检查当前LLMClient与AIRun发现usage字段未收集，单次请求也未发送生成上限；此前字节检查仅在响应完成后限制解析。先补实际调用的max_completion_tokens参数，默认16384，LLM_MAX_COMPLETION_TOKENS配置1..131072整数，每次重试均携带同值；不支持参数的provider失败关闭，不自动移除限制。SDK本地签名确认支持此参数，未发起付费调用。

新增8个红测先失败（默认值、env、重试携带和非法配置），实现后客户端资源/重试/确定性、AI引擎、输出scope与content quality合计131passed。既有截断拒绝逻辑保留，不把length视为可修复成功。README明确单次生成与总任务费用不同，输入和多次调用仍可能收费。usage持久化、关联run/Case、价格版本和任务总预算未完成；不以本片替代费用审计。


## AI操作用量持久化

上一轮生成预算为实际进展，本轮继续核对AIRun写入与provider事务边界。新增0071可空JSON usage列；旧行保持NULL，升级保留历史数据，兼容ORM已建列的adoption。抽取、提议、评估方法使用ContextVar独立操作收集器，评估rewrite共享其操作；record_run深复制用量快照到只追加记录。每次真实SDK响应在deadline和输出验证前收集，因此迟到/无效内容仍保留已报告的消耗。传输错误单独记录为unavailable，不含异常正文；usage缺失、布尔值、负数或总和不一致不转成零。mock与没有外部请求的操作不虚构usage。

证据：新测试先因模块不存在红；实现后失败提议真实服务路径提交的AIRun关联原thesis且持久化token数据。最终相关客户端/引擎/SQLite迁移/就绪189passed（82.23秒），补迟到响应后usage专项9passed；临时PostgreSQL迁移head及usage7passed，同时ready200/旧迁移503检查通过，容器已清理。初始回归10项失败均为head断言仍写0070，核对各处upgrade head后更新0071，复验全过。git diff --check通过。未对用户现有数据库执行迁移，部署前需正常升级至head。

边界：这些记录随原业务事务保存，不是独立耐久账单；scope取消不落AIRun、进程终止或事务回滚可能遗漏。准备阶段等其他chat_json调用未全量接入；跨操作run/Case聚合、价格版本、货币费用及总预算门禁仍待完成，不能以本片标记usage/预算路线完成。无付费调用。


## 准备worker的独立用量记录

本轮继续usage路径审计，发现准备生成器的claims/protocol/evidence-plan没有AIRun落点。新增worker供应商阶段收集与独立事务记录，kind=prepare，使用真实prompt版本，关联research_case_id、preparation_id/version、input_fingerprint和job_id；实际请求发生后才记录，mock/未请求不产生虚构消费。供应商阶段成功/失败与后续产物状态区分；方法在finally保存，所以已返回但无效/截断的输出也被记录。之后产物丢弃或写入事务失败不撤销已提交消费记录。进程在响应和审计提交之间退出仍可能遗漏，不宣称exactly-once账单。

验证：失败准备调用缺失审计红测先失败；真实LLMClient假SDK返回用量与length，worker安全失败且AIRun保留8tokens及正确Case/准备/Job关联。新增后续_complete异常测试证明用量已提交且无研究产物。准备worker、集成、usage共40passed（9.44秒）；git diff --check通过。未调用付费provider，未全后端重跑。本片未增加新迁移，仍使用0071；事件抽取和成本聚合门禁等继续待办。


## 创建前事件抽取用量

核对剩余chat_json调用，事件抽取在Case创建前且无原有AIRun。接口新增context-local收集，用event_extract记录供应商阶段状态、模型/提示版本、已认证tenant_id和独立extraction_id；不存材料原文或source_url，不创建Case。响应无效返回503前也提交已报告用量；未发请求不新增虚构记录。新增服务model_version只读属性。仍沿用0071，无额外迁移。

验证：事件API、事件抽取服务、usage共119passed（7.37秒），成功与无效输出均记录10tokens且tenant精确匹配test-team，Case数量不变。测试初版误用session而非cmd_session查询了独立测试库，修正后又纠正一次机械替换的变量拼写，再完成全组选定回归；不把这些测试辅助错误说成生产缺陷。git diff --check通过。未全量后端重跑，未收费调用。抽取ID到后来确认创建Case的关联及费用聚合/预算门禁继续待办。


## 组合回归与CI去重

启动0071与所有usage改动后的全后端回归，exec session82662，日志/tmp/fund-engine-full-usage-20260906.log、JUnit/tmp/fund-engine-full-usage-20260906.xml；本轮已重新poll确认进程仍活跃，尚无最终结果。禁止仅因等待超时而重启。前端组合复验338passed、生产构建passed、真实浏览器7passed（17.8秒）。

检查完整CI时发现backend.yml原已有company-research-live，以RUN_LIVE_COMPANY_RESEARCH=1启用pytest真实包装验收。此前仅检查frontend.yml而增设相同完整流程造成重复；现删除前端重复步骤，把71项支持测试并入既有backend公司任务并顺序执行。前端保留e2e:live与backend触发范围。YAML检查验证去重、顺序和显式live开关，diff检查通过。README更新实际任务分工。未声称GitHub托管运行成功。


## 公司与归档键盘入口

完整后端session82662再次poll确认活跃，进度44%，未重启。检查旧事件verifier确认需要资料、原子陈述审核、监控版本及市场关系完整交互，不能仅恢复脚本使其通过。

公司与归档Shell新增首个键盘跳到内容链接，复用现有focus-only样式，目标容器支持程序焦点并保留页面main语义。真实浏览器验证Tab首先进入可见skip、Enter移动到内容、下一Tab进入表单第一字段；桌面1280px和手机390px均覆盖公司、归档及原事件页面，最终6passed（19.1秒），生产构建通过。初版归档断言误用textbox而非searchbox角色，修正后重跑全专项通过。diff检查通过。200%浏览器缩放和屏幕阅读器测试仍未完成。


## 页面模块失败恢复

检查main发现lazy公司/归档仅Suspense等待态，没有渲染错误边界，模块下载失败会使页面不可用。新增AppErrorBoundary覆盖三套入口，固定安全中文提示，不渲染内部异常内容；故障时焦点进入内容，重载当前URL（保留path/query/hash）或返回研究空间。明确未提交输入可能丢失。

前端新错误边界测试在组件缺失时红，实现后全340passed；生产构建通过。真实浏览器拦截CompanyResearchApp模块下载，断言错误提示和焦点，解除拦截后点击重载，原/research/new?recovery-check=1恢复正常，新专项1passed（13.8秒）。diff检查通过。该边界处理渲染/模块加载异常，不代替已有API错误状态或宣称捕获任意异步事件错误。

完整后端session82662仍live，最新约49%；未重新启动或宣称完整通过。


## 故障恢复手机与归档验证

在上一轮恢复入口基础上补齐公司与归档两个真实模块的下载失败，桌面1280和手机390共4项浏览器故障注入通过（18.4秒）。均验证错误主区焦点、Tab到重试按钮、Enter恢复相同path/query、无横向溢出；手机故障截图已查看，提示与按钮完整可见。测试从单条公司鼠标恢复扩展为实际键盘流程，不宣称覆盖生产CDN或离线缓存。完整后端session82662仍活跃约58%，保留同一执行，等待结果。


## 完整后端结果与PostgreSQL全专项

完整session82662已终止：4357passed、1failed、41skipped、738.24秒。唯一失败是事件完整live verifier缺失。此轮完整运行始于PG修复之前，不把其结果宣称为之后全部文件的最终全量验证。

发现backend CI仅SQLite会跳过pg_only，新增独立PostgreSQL16服务任务：先alembic upgrade head，再pytest tests -m pg_only，失败阻断，JUnit保存。对应本地临时数据库首次27passed/8failed，未隐藏失败或缩小选择集合。

修复：独立连接提交的测试未请求session fixture导致跨用例数据残留，pg_only增加统一前后TRUNCATE隔离；0063/0065迁移触发器SQL未限定schema，误包含public后续迁移触发器，限定current_schema；协议并发夹具改用已存在的完整_materializable_protocol；取消与provider失败测试先等provider真正进入，避免取消抢先导致模型根本不被调用。真实生产修复是prepare authorize幂等重放：并发等待后Session仍保留旧preparation，可能返回research_run_id=None；重新读取前expire_all，保留当前展示限制过滤而非直接重放可能过期的内容。既有同key双连接测试验证两个201返回同一个run。

复验：完整pg_only35passed（77.47秒），无跳过；SQLite准备API/service54passed/4pg跳过；临时PG readiness当前200、旧迁移503、health200；容器已清理。CI YAML/环境/迁移顺序检查与diff检查通过。未在GitHub托管runner执行，也未触及用户数据库。后续继续事件完整流程、费用总预算、共享目录、性能及可访问性路线。


## 上传与PDF规模限制

核对上传HTTP已有20MiB+1有界读取，但直接服务入口未重复检查，Pypdf没有页数/累计文本/段落上限。将MAX_UPLOAD_BYTES共享给三个HTTP入口与底层_parse，超大文件在parser启动前拒绝。Pypdf默认1000页、2000000文本字符、20000段落，页数超限先于extract_text；累计文本超限后不访问下一页；段落超限抛解析错误，不返回部分spans。上传现有解析失败流程保留原件并标记失败。

新增4个红测分别复现缺口，实施后PDF adapters、docling adapter、上传HTTP及原件不可变共47passed（3.03秒）。diff检查通过。无迁移、无外部provider调用。页树建立/单页文本解压之前尚无进程级内存和CPU硬限，HTTP multipart入栈前的整体网络/临时磁盘限制及清理故障注入仍须继续；不以此片完成上传解析路线。


## 上传请求结束后的临时文件清理验证

使用真实multipart请求追踪Starlette UploadFile，分别上传超过1MiB的正常文本、无效PDF、不支持格式和20MiB+1超限文件。断言实际SpooledTemporaryFile已rolled到磁盘，并在响应结束后closed；正常/解析失败各保留一个原件，超限/类型拒绝不新增原件。新增4种情形与现有上传回归共14passed（3.46秒）。确认框架正常清理，无需重复实现close；未把正确行为描述为生产缺陷。diff检查通过。此验证仅覆盖请求完成的四类路径，不覆盖断流、进程被终止或multipart解析前总磁盘预算，路线保留其余限制待办。


## 部署代理的上传总量边界

真实Nginx镜像验收复现原模板缺失client_max_body_size，默认限制拒绝2MiB，低于应用20MiB承诺。设置21m总HTTP请求上限并显式proxy_request_buffering on；应用仍严格20MiB文件上限，额外空间用于multipart元数据。Nginx先完成请求体缓冲/上限检查，再向后端发送，不以Content-Length作为唯一判断。

新增backend/scripts/verify_upload_proxy.py，读取当前Dockerfile的Nginx镜像，随机命名临时容器/临时配置，内部模拟上游，无宿主凭据，finally清理。验证2MiB、20MiB+64KiB转发成功；仅发送超限Content-Length头即得到413；无Content-Length的chunked超限也413；上游日志恰好2次有效请求。脚本真实运行通过，容器已清理；运行资产5passed、CI YAML/diff检查通过。已接入frontend live-entry，未声称托管CI执行过。总并发磁盘额度、慢传输总时长和直接API访问的前置限制仍待评估。

## 监控计划操作入口与无效频率校验

真实事件工作台新增按需加载的监控计划面板：已有计划状态/版本/频率/预算/下一核验事件/最近变更及下一计划时间，支持具名操作人和原因的暂停、恢复。写入后重新GET核实状态；刷新失败保留原因并禁用再次写入，显式刷新可恢复。Case切换中止请求并忽略旧响应；无计划不提供状态操作，异常响应不启用写入。明确暂停仅影响未来调度、运行需独立取消，恢复可能消耗已配置服务。

浏览器验收发现服务接受调度器不认识的frequency，能存为active却永不调度。新增共享MONITOR_TARGETS供保存校验与调度器使用；3个无效频率测试从失败转为通过。原归档测试使用未实现的weekly_monday，改为已支持的daily_20_00，保留旧运行范围不受新配置影响的断言；未添加未经实现的每周调度承诺。

验证：前端347passed、生产构建通过；监控service/API/scheduler26passed；完整当前live浏览器16passed（19.1秒），含新增暂停→重载→恢复、审计历史3版本、390px无横向溢出及现有全部页面流程。diff检查通过。隔离夹具不访问外部模型/数据，临时服务已结束。新增7项组件测试覆盖写后读失败、空计划、格式错误与切换Case旧响应。

范围仍未完成：新建/编辑监控配置、因素/市场链与完整旧事件验收脚本、已有非法频率记录治理、监控并发版本写入与调度精度审计。此次未运行新的完整backend suite，最近完整backend结果仍4357passed/1failed/41skipped，唯一失败为缺失完整事件验收脚本；不能将本次专项和当前live16项代替该验收。

## 自动监控每日去重语义修复

新增三组反例复现并修复：手动研究占用自动监控名额；暂停/恢复生成新配置版本后同一天重复调度；UTC日期边界与北京时间计划不一致。去重改为同一Case、北京时间当天的半开区间、scope事件trigger=schedule，不再按单个monitor version或任意运行判断。历史运行的monitor_version_id及冻结范围保持可追溯，次日仍可启动新调度。

SQLite监控service/API/scheduler29passed（4.56秒）。新增测试用固定仓储时钟，不依赖实际运行日期；覆盖手动与自动分离、暂停恢复同日不重复/次日可运行、北京时间零点含边界和次日零点排除。并发调度器之间仍需事务锁或持久化唯一slot约束；本修复不声称解决跨进程竞争。

真实隔离PostgreSQL16运行同组29passed（8.60秒），迁移head readiness200/旧迁移503/liveness200复验通过，临时容器清理完成。无生产数据库变更、无provider调用。

## PostgreSQL监控调度与配置变更串行化

双独立连接反例复现：第一个dispatcher进入start前暂停，第二个可先创建并提交同日运行，随后第一个又创建一条。复用研究Case事务锁，将监控save/set_status和dispatcher纳入同一锁顺序。dispatcher仅先查询去重排序Case ID，逐个锁住Case后重新读取最新monitor，再核对当天调度记录并创建任务；锁随外层事务提交释放。不会缓存锁等待前的active配置，也不再把全部历史monitor实体加载到内存。

新增pg_only双dispatcher、未提交暂停与dispatch竞争、未提交暂停与resume竞争。SQLite顺序回归29passed/3pg跳过；SQLite现有FOR UPDATE为no-op，未宣称跨SQLite多进程具有相同串行保证。调度批次仍会持有已访问Case锁直到外层commit，大批次吞吐与SQLite部署并发策略仍需继续评估。

真实隔离PostgreSQL最终32passed（16.16秒），包含全部3项竞争用例，原双dispatcher红测已转绿。diff检查通过；本次没有新的完整backend suite结果。

## 调度日期与实际入库时间分离

冻结scope事件新增scheduled_local_date（Asia/Shanghai日期），去重优先使用明确的调度日期；只有旧记录缺少该字段时才回退到created_at的北京时间半开区间。这样批次等待锁或跨零点插入不会把前一日调度归入次日，也不修改真实创建时间。新增固定未来日期的反例先失败后通过，并扩展为将首条运行created_at放到次日零点，确认原日期仍去重而次日仍可排队。

SQLite监控30passed/3pg跳过，PostgreSQL监控33passed（18.79秒，含并发与历史回退回归），追加跨零点时间变更边界专项1passed。隔离PG迁移/readiness复验通过且容器已清理。无迁移或外部provider调用；SQLite跨进程策略、配置UI及完整事件验收仍未完成。

## 历史非法监控频率的恢复保护

复现并修复旧weekly_monday等非法频率可以通过set_status(active)绕过保存校验。恢复前检查现有频率受支持，拒绝时不追加版本；暂停仍允许，以便关闭历史计划。页面保留历史记录展示，明确提示频率不受支持；active非法配置显示“配置无效”，不再显示“调度中”，paused非法配置禁用恢复按钮，active仍可具名填写原因后暂停。

新增后端1项与前端active/paused两项红测均转绿；后端监控31passed/3pg跳过，前端全量349passed，生产构建与diff检查通过。未改写历史不可变记录。修复非法配置的编辑入口仍待接入；此次未重跑PostgreSQL或浏览器全量，不引用旧结果冒充本次验证。

## 监控配置创建与编辑入口

工作台新增按需读取的配置表单，基于available_confirmed_factors选择已确认因素，显式选择允许来源、受支持北京时间频率、正整数单次任务额度、下一核验事件和变更原因。范围/参数修改会清除启用核对复选框。保存明确标为“保存并启用监控”，提示暂停中的计划也会启用及可能使用模型/数据服务；额度不冒称人民币。

通过共用request客户端增加显式PUT选项，保留same-origin代理鉴权和既有安全错误处理。提交后GET重新核实配置，再刷新状态面板；失败保留草稿并禁用重复提交，重新读取前提示舍弃草稿。无已确认因素无法保存。历史非法频率可选择受支持值后追加新版本修复，旧版本不被改写。

新增组件3项回归（创建请求/核对门槛、无确认因素、失败后保留草稿并阻止重复写），前端全量352passed，构建通过。浏览器暴露select隐式标签包含选项文本，补充明确可访问名称；真实HTTP配置修改验证版本4、预算30、晨间频率、审计原因和390px页面重载后回填均通过。后续仍需完整旧事件因素/市场链流程、新建配置真实浏览器覆盖、乐观版本冲突提示及整体视觉验收。

本轮完整live浏览器16passed（25.0秒），构建和diff检查通过。隔离服务已终止，无外部provider调用。

## 监控配置保存的乐观版本核对

UpdateCaseMonitorRequest新增可选expected_version（0表示读取时尚无配置），service取得Case事务锁后比较当前max version；不一致返回409且不创建新版本。新配置表单始终提交它读取的版本。旧调用方未提供该字段时仍保留既有追加语义，不宣称所有旧客户端都获得冲突保护；状态暂停/恢复接口目前尚未加入expected_version。

新增HTTP回归：v1读取后其他人暂停到v2，v1保存409，v2暂停及历史2条不变，读到v2再保存成功生成v3。SQLite监控32passed/3pg跳过；真实PG监控35passed（15.75秒）；组件3passed；真实浏览器已有配置读到v4后另一个HTTP调用暂停为v5，旧表单保存报冲突、禁用重试，服务端保持v5 paused，专项1passed（16.6秒）。OpenAPI及v1.ts重新生成，生产构建/diff检查通过，临时PG和浏览器服务已结束。整体事件验收、状态操作版本核对及其他原路线继续推进。

## 暂停和恢复操作的版本核对

SetCaseMonitorStatusRequest新增可选expected_version（>=1），Case事务锁取得后核对最新版本再执行状态变更。工作台暂停/恢复始终携带当前展示版本。新增HTTP反例先红后绿：他人保存v2后旧v1暂停409且不追加历史；核对v2可暂停到v3，旧v2恢复仍409且保持paused。旧调用方省略版本字段仍兼容，未将其描述为强制全客户端协议。

SQLite监控33passed/3pg跳过，MonitorPanel9passed，OpenAPI和v1.ts已同步。完整路线仍未完成，特别是旧事件全流程验收脚本、SQLite多进程策略、费用总预算、共享目录策略及性能/视觉收尾。

本轮真实PostgreSQL监控36passed（16.60秒），浏览器专项1passed（13.4秒），构建/diff检查通过，临时服务清理结束。未运行新的完整backend suite。

## 首次监控创建的真实流程与移动端修正

隔离浏览器夹具新增无监控计划的已审核研究。真实页面从monitor=null创建：选择因素和公司披露、填写下一事件与原因、核对启用；修改预算清除核对并禁用保存；重新核对后PUT expected_version=0成功。验证version=1、history恰1条、budget=12、指定来源与单一因素，重载后状态和表单回填保持一致。

截图发现通用.live-create输入样式使checkbox横向拉满、独立居中，标签换行；对.live-monitor-config限定修正复选框18px与文字同行、标签44px可点高度，下拉框44px高度及统一边框。修复前后均实际查看390px截图，修复后对齐清楚且无横向溢出。完整live17passed（25.4秒），构建/diff检查通过，隔离服务已清理；没有外部provider调用。

## 事件手动启动入口差距核对与新全量回归

启动新后端全量回归（pytest backend/tests -q --tb=short，XML=/tmp/fund-engine-full-monitor-20260906.xml，日志同名前缀.log）。开始时包含全部监控配置、状态版本、并发与跨零点修复；运行中不改变后端实现，结果尚未完成。

对照旧完整事件verifier实际交互和当前API：monitor/runs是无请求体命令，start_from_monitor读取当时最新版本，不接收操作人和expected_version；页面直接增加按钮将缺乏用户核对版本与启动审计。现有/theses/{id}/researchability提供not_applicable/blocked/single_metric_monitoring/ready，服务start已执行协议门禁。下一步需要在Case锁内核对版本，将操作人/原因冻结到scope，避免响应不确定时重复启动，再让页面读取选定因素的协议状态、显示阻断理由、明确授权一次运行；暂停调度不等同于禁止人工单次运行，保留既有业务语义。完整旧verifier还要求冻结资料抽取、人审、市场因素/公司股票基金链、基金披露失败回放，不能用监控验收替代。

## 监控范围与研究协议快照

新增按需“核对监控范围与协议”：读取有效monitor版本及所选已确认因素，只查询这些thesis的researchability，显示无需协议/阻断/单指标/就绪与后端next_action。所选因素不再确认时明确报错，不跳过缺失项或虚报就绪；单批最多4个并发查询。展示明确为具体版本快照，配置/审核改变后需重读，实际启动仍需服务端门禁。

组件2项红测转绿，前端全量354passed，监控真实浏览器2passed（20.0秒，含单一选定因素的not_applicable及下一动作展示），构建通过。此片仅建立范围核对基础，不宣称已完成手动启动命令的审计/幂等/版本核对；全量后端session52193仍在运行，日志已到43%，无最终结果。后端代码在该全量运行期间未修改。

## 手动监控命令的失败回归

新增test_manual_monitor_command.py两项真实HTTP红测，均复现缺口：同idempotency_key重试创建了不同run；监控已从v1暂停为v2后携带expected_version=1仍成功启动。用例同时要求scope保留requested_by/request_reason/monitor_expected_version，以及同键不同内容409。当前实现尚未修复，2failed是有意保留的待实施回归，不能称通过。

该测试文件在全量pytest完成收集后新增，因此不包含在当前全量52193的收集范围内；后端实现未变。全量日志已推进到53%，进程仍在运行。下一步在该基线结束后接入可选结构化手动请求、事务内幂等与版本检查，再运行此组至通过并接入UI。

## 手动启动命令的版本、审计与重复提交保护

monitor/runs新增可选结构化请求，包含actor/change_reason/expected_version/idempotency_key，保留无请求体旧入口兼容。新命令在同一事务内获取租户命名空间幂等槽、Case锁、核对有效monitor版本，再按冻结计划启动；scope写入requested_by/request_reason/monitor_expected_version。相同键相同内容重放原run_id并重新生成当前显示响应，槽只缓存run_id；同键不同内容409。失败回滚槽与运行，SQLite显式外层BEGIN避免savepoint提前提交。

原2项红测转绿；新增失败后修正版本复用键成功验证，专项3passed。SQLite相关25passed/3pg跳过（新增失败恢复专项另跑），真实PG手动/API/scheduler29passed（12.17秒），合同生成/前端构建通过。新命令的真正双连接同键竞争测试与UI启动按钮仍待补齐；旧无体调用方不享有新审计与幂等承诺。

## 本轮后端全量基线结果

session52193已终止：4375passed、1failed、44skipped、4warnings，677.79秒。唯一失败为test_verify_live_event_ui.py缺失完整事件verifier。该批在新手动命令之前收集，新增文件及新命令语义不在全量基线保证内，后续专项单独报告；不能称全后端已绿。日志/tmp/fund-engine-full-monitor-20260906.log、XML同名前缀.xml。

## 手动启动的真实并发验证

新增两个pg_only独立连接场景：首请求获取幂等槽后停在创建运行前，次请求确认已尝试获取同键且尚未完成，再释放首事务。同内容返回相同run id；不同reason返回Conflict。两者均验证数据库只有1run、4tasks（单因素）、1job和1scope事件，避免仅检查响应遗漏重复副作用。

首轮因测试把命令返回dict当对象读取失败，修正result['id']后PG相关31passed（15.27秒）；SQLite顺序3passed/2pg跳过。未据测试错误虚报生产缺陷，无新增生产修改。隔离PG readiness通过并清理，diff检查通过。下一步接入手动启动UI及实际浏览器重试场景；完整事件verifier仍未恢复。

## 工作台单次研究启动接入

在监控范围与协议快照后提供显式单次启动，展示版本、任务额度与来源，要求具名操作人、启动原因和核对授权；任一所选协议blocked禁用。请求携带expected_version和持久到sessionStorage的幂等身份，保存同一内容的网络重试身份；本视图成功后禁用再次提交，刷新运行列表。明确暂停未来调度仍允许人工一次启动，额度不等同人民币。

新增组件验证授权/协议阻断和不确定响应同键重试。真实浏览器先route.fetch让后端成功创建，再abort丢弃响应；重试201返回同一run id，列表仅1条，scope审计含操作人。专项2passed（15.4秒），前端全量356passed。初次构建为测试数组取值类型错误，修正后生产构建通过。sessionStorage不可用时仍保留当前视图内重试身份，跨重载保证受限；用户改写内容视为新的意图，错误提示要求不确定结果保持内容重试。旧事件验收还有冻结资料抽取/市场链等范围，不能以此替代完整verifier。

本轮完整live17passed（26.7秒），diff检查通过，隔离服务已结束。完整目标仍active，未执行新全量backend回归。

## 跨重载重试与真实协议阻断

将真实响应丢失场景扩展为页面reload后重新读取范围、填写相同actor/reason并核对额度再重试，仍返回首个run id且仅1条运行，验证可用sessionStorage下的重载身份恢复。新增协议required但结果指标/基线/窗口未确认的隔离Case，页面显示blocked与具体下一动作，授权框选中仍不可启动；直接真实HTTP结构化命令也422且运行列表为空。

监控浏览器3passed（17.3秒），390px无横向溢出，diff检查通过。没有新增生产代码修改；未把正常阻断行为称为已修复的后端缺陷。此验证不等于完整“创建并审核协议至ready”的浏览器闭环，也不等于sessionStorage不可用时的跨重载保证。

## 不确定提交期间冻结重试内容

新增反例：网络失败后外层actor可变、原因可改，原实现会生成新fingerprint/key。现在网络/5xx/异常成功响应等不确定结果锁住启动原因与核对框，重试使用首次捕获actor/reason/version，并显示原操作人；明确4xx拒绝允许修正。不会仅依赖提示语要求用户保持内容。

新增不确定请求actor变更回归从失败转绿，另验证422允许修正；启动组件4passed，真实监控浏览器3passed（15.8秒，含重载丢响应重试与协议阻断），构建/diff通过。此片增强当前视图内不确定请求保护；跨重载仍依赖可用sessionStorage与相同内容重试，完整事件/其他原路线继续。

## 事件原始资料列表分页

核对资料入口发现/{case}/documents固定limit100/cursorNone，底层虽返回has_more却无法访问后续页。新增limit（1..100，默认100）与cursor（最长2048）参数，传递tenant_id启用游标所属研究检查。三条附件以limit1逐页读取不重不漏；非法limit/游标422，其他Case附件游标404。新增分页红测转绿，与event_research_api共63passed（7.60秒）。OpenAPI/v1.ts重新生成，前端构建通过；资料列表及冻结原文UI仍待接入。

## 事件资料列表与冻结原文入口

工作台新增按需资料列表（每页20条，可继续加载），显示解析状态、可见段落数、可用时间；详情通过Case限定接口读取冻结段落和定位，校验返回document id及每条span归属，错误不展示错配原文。来源链接仅允许http/https；无可见段落明确提示核对解析状态与权限，不编造正文。新列表/详情请求清理旧详情，父级按Case key隔离并中止卸载请求。

详情完成后焦点移至article，长原文保留换行且可键盘聚焦滚动，UUID及定位长文本可换行。组件分页/错配段落2项红测转绿；前端全量360passed、生产构建通过；真实冻结来源浏览器1passed（11.7秒），逐字核对原文、定位paragraph、焦点及390px无横向溢出，实际查看截图确认可读。尚未接入冻结资料抽取候选、上传/原件下载及完整旧事件verifier，不能把段落查看等同于完整原件操作流程。

## 资料列表的原文/陈述查询批量化

查询计数红测复现无股票定位元数据的fixture中limit1=9SELECT、limit10=26SELECT。列表逐文档读取spans和完整statements；改为按页可展示document IDs批量读取spans、SQL按document聚合statement count，列表不再实例化全部陈述对象。合同限制在正文查询前过滤，详情行为保持原样。

回归记录（相同测试执行顺序/缓存环境）优化后limit1=9、limit10=8；断言增长有界，未将该计数推广为所有资料类型。SQLite资料/来源治理52passed（5.76秒）、真实PG同组52passed（13.05秒），原文数/陈述数及受限显示回归通过，PG容器已清理。指标写入JUnit testsuite properties，避免xunit2 testcase property警告。股票实体识别仍有独立查询，单份大量spans为质量评估仍需载入，整体性能路线未完成。

## 重复股票实体识别的请求内缓存

扩展查询计数fixture为10份不同标题但相同股票代码，红测复现limit1=10SELECT/limit10=18SELECT。_resolve_entity增加由单次list调用创建并传递的stock_cache，对代码和名称查询分别缓存命中与未命中，保持代码优先、简称再标题前缀的规则，不改SQL候选匹配顺序。新请求新建缓存，不保存跨请求旧结果。

优化后该fixture limit10=9SELECT；无实体fixture仍为8。新增正向代码/简称/标题回退及前一请求查无后新请求可识别新增股票的测试；资料与来源回归54passed（5.22秒），diff通过。此优化针对重复实体，不承诺不同股票数很多时恒定查询，也未消除大量spans内存开销。本轮没有重新运行PG，不沿用旧PG结果作为本次声明。

## 资料抽取的模型初始化失败边界

核对抽取入口发现LLMClient.from_env在try之外，配置ValueError/RuntimeError未转为依赖不可用，也没有AIRun记录。新增两项红测复现；现在仅在客户端初始化范围捕获这两类已知配置异常，提交kind=extract/model_version=not_run/status=failed的通用审计，使用真实EXTRACT_PROMPT_VERSION，503响应和审计均不包含异常敏感配置，候选数量不变。原授权/合同检查保持先于模型初始化，未授权不写审计。

文档读写与配置失败31passed，engine commands21passed/1skipped（3.76秒），diff通过，无provider调用。尚未接入抽取按钮及原子陈述审核闭环，此修复不代表抽取入口整体完成。


## 冻结资料候选抽取入口

资料详情接入显式同意后的Case限定抽取请求，显示候选原文及字符范围，保持awaiting_review，不自动发布正式陈述。来源不允许AI、无可见原文或已有成功抽取记录时禁用；单次尝试后锁定，失败提示刷新核对，响应校验文档归属与候选结构。此为UI防重，不代表服务端持久幂等。

浏览器首次验收误用了已有正式陈述的资料，入口按预期禁用；补充内容独立的未抽取fixture后，真实隔离后端与Mock模型验证候选生成、正式陈述数不变及刷新后禁止重复抽取。所有live浏览器19passed（36.5秒），前端364passed（23文件），生产构建通过。网络不确定失败与错文档响应有单测覆盖。未调用外部模型。后端全量旧记录仍有缺失旧事件verifier的1失败，未宣称全项目通过。原子候选审核发布闭环与成本汇总等路线仍待实施。


## 原子候选审核的来源展示权限边界

接入候选审核前复查API，红测确认atomic-claims列表会返回禁止展示来源的原文；直接reviews命令对禁止展示、已过期、未来生效三类来源都可201发布正式陈述。现列表在SQL LIMIT之前过滤allow_display与生效区间，直接审核在租户归属校验后、锁定/写入前拒绝受限来源404。无合同历史来源保持既有兼容，正常生效合同仍可查看。新增断言拒绝后无审核/正式陈述记录，并验证较新的受限候选不会占满limit=1遮住可见候选。

SQLite原子陈述/API/审核租户回归26passed（6.41秒）；真实隔离PostgreSQL16同组26passed（5.71秒），迁移ready验证通过，容器已清理。未调用模型。审核UI接入暂未实施；列表完整分页及review_state过滤在LIMIT之后的问题仍需处理。该修复不代表全部来源展示入口已完成审计。


## 原子候选审核状态筛选先于数量上限

新增红测复现limit=1时最新已审核候选遮蔽较旧待审核候选，旧逻辑先limit再Python过滤导致空队列。改为相关子查询取得最新审核outcome，无审核coalesce为awaiting_review，在SQL LIMIT之前筛选；DTO审核历史与子查询统一created_at/id排序。新增先驳回再确认的用例验证按最新决定筛选，不误匹配旧决定。

SQLite原子陈述/API/审核租户28passed（4.02秒），真实隔离PostgreSQL16同组28passed（4.53秒），迁移ready验证通过并清理容器。未修改前端和调用模型。完整分页、审核UI及全项目最终回归仍未完成。


## 原子候选队列后端分页

atomic-claims新增可选UUID cursor和has_more/next_cursor响应。按created_at/id降序使用键集分页，取limit+1判定后续数据；游标只在当前Case及可见来源内解析，跨Case/受限游标404、非法UUID422。游标定位先于review_state过滤，允许已审核当前页后继续翻页。新增三页不重不漏、跨Case拒绝、翻页期间审核与新插入用例。

SQLite相关回归30passed（5.21秒），真实隔离PG16同组30passed（5.15秒），容器清理；OpenAPI与TS契约同步后生产构建通过。分页不是事务快照，其他候选并发状态变化需刷新获取；游标来源失效会404要求重新开始。页面加载更多和审核操作尚未接入，未宣称完整页面闭环完成。


## 原子候选审核页面与抽取发布闭环

ResearchActions新增AtomicClaimQueue：按需读取每页20项与加载更多，展示冻结引文/字符范围/定位/历史/正式陈述。仅待审核项可提交确认、修订、驳回；操作人与理由必填，修订必须有文本，确认保持原文标准化陈述，服务器仍负责授权和发布。网络或异常响应时冻结决定与操作人，锁住队列刷新和其他行操作，沿用原body/idempotency key重试。已确认成功的行不再提供提交按钮。提示审核可能恢复等待中的研究运行。

前端367passed（24文件，11.33秒），生产构建通过，live浏览器19passed（40.1秒）。隔离documents浏览器串联冻结资料→mock抽取→人工修订→正式陈述数仅加1→刷新仍保留修订和无提交入口。确认原陈述、驳回及网络中断后操作人变更不改变重试body由单测覆盖；截图390px实际查看可读无横向溢出。未调用外部模型。

重试身份仅当前页面内存，不支持跨刷新恢复未确认请求；跨租户共享候选的全局审核仍由服务端拒绝。整体路线、历史事件verifier、成本归集等仍未完成，此处不作为全项目最终回归证据。


## 原子候选审核重试的内容一致性

六项红测确认：同key修改outcome/reviewer/reason/normalized_text/observed_period仍静默返回旧审核；首尾空格key查询未规范化而插入时strip，重试触发唯一约束错误。AtomicClaimService.review现查询前统一规范化reviewer/reason/key，并对已有审核的决定、操作人、理由及实际发布文本/期间做语义一致性比较，不一致ConflictError→409，一致返回原记录。候选锁顺序不变，无额外模型或发布调用。

SQLite相关36passed（5.07秒），真实隔离PostgreSQL16相关36passed（6.08秒），准备API/service/integration56passed/4skipped（10.01秒）；容器已清理、diff通过。规范化后语义等价的请求允许重放，不比较原始字节；本批未新增并发压力测试。浏览器跨刷新未确认请求恢复与其他总路线剩余项仍未完成。


## 人工审核模式研究执行的AI审计归属

用量汇总前核对发现AIRun.input_ref仅有thesis/document等引用，无法可靠区分同一thesis的多次运行。新增research_audit_context（ContextVar、finally恢复），record_run追加执行时research_case_id/research_run_id；reviewed执行的extract绑定run，propose/assess绑定run及research_task_id。原input_ref字段保持，未凭thesis猜测历史归属。上下文不包含prompt或原文。

新增嵌套/异常恢复/独立调用不泄漏测试与真实reviewed服务执行归属测试；usage+auto研究API+AI engine92passed/2skipped（7.21秒），diff通过。此改动尚未覆盖automatic pipeline与独立命令归属，不解决取消回滚或崩溃后的审计持久化，也尚未提供token聚合或货币预算。整体LLM路线继续保持未完成。


## 自动管线及采集worker的AI审计归属

AcquisitionRunner抽取时从当前AcquisitionJob读取Case/run，记录acquisition_job_id；独立采集run=None时仅记录Case/采集任务，隔离外层上下文，不把None字符串或其他运行归属写入。AutomaticResearchPipeline最终评估在准确result task上下文中调用generator。

真实材料治理抽取测试先红（缺少研究运行归属）；实现首轮因漏导入AcquisitionJob造成抽取失败，已修正并完整重跑。自动管线+采集runner+usage101passed/1skipped（21.41秒），上下文隔离+reviewed回归48passed/2skipped（11.46秒），diff通过，无外部provider。本轮未重跑PG。用量汇总、初始准备/独立命令覆盖及取消回滚/崩溃后的审计持久化仍未完成。


## 研究运行已记录LLM用量汇总（2026-09-07）

新增GET /research-runs/{run_id}/ai-usage，经现有run/Case租户校验后，只查询同时匹配run与Case的AIRun.usage，按200条流式读取，不读取原文、输出摘要或provider错误。返回operation_count、reported/unavailable attempt数、缺少记录的operation数、已报告prompt/completion/total小计；任何未知或无操作时recorded_total_tokens=null，有效空attempts操作才可为0。coverage固定说明仅已持久化且明确归属的操作，不是完整账单。异常schema、布尔/不一致token字段按未知处理。

SQLite用量/审计/研究API57passed/2skipped（7.11秒），PG16用量/审计20passed（4.30秒），真实其他租户404，容器清理。OpenAPI和TS契约更新，生产构建通过。UI、Case/task汇总、历史/缺失审计覆盖和货币预算仍未完成。


## 运行详情的LLM用量面板（2026-09-07）

RunAIUsage接入运行详情，按需查看/刷新，展示已报告输入/输出token小计、操作/尝试数量、未知调用/无用量记录数量。无记录提示不能认定为零；记录不完整时不显示完整总量，始终说明统计范围并区别任务预算。运行ID、计数、token求和及未知总量语义均作响应校验，卸载中止读请求。

前端370passed（25文件，10.04秒），live浏览器19passed（28.5秒），生产构建通过。浏览器真实接口验证无记录提示，原运行取消/分页流程及390px无横向溢出断言通过。货币成本、Case/task分组、历史与未保存调用覆盖仍未完成，不作为账单或完整用量声明。


## Case累计LLM用量与页面（2026-09-07）

新增Case ai-usage接口，现有Case归属校验后仅按明确research_case_id读取AIRun.usage，复用未知/小计处理。通用AIUsageSummaryDTO由Run与Case分别扩展标识。研究操作区接入累计用量，复用运行面板渲染与校验，区分Case与run身份/标题/读取路径；按需读取，不自动请求。

后端57passed/2skipped（5.31秒），PG用量/审计20passed（26秒），容器清理；前端371passed（10.21秒），live19passed（35.1秒）。契约更新后首次构建报测试mock.calls可空，补可选访问后构建通过。任务/模型分组、货币预算、全量审计覆盖及整体最终回归仍未完成。


## 用量查询归属索引及验证口径更正（2026-09-07）

SQLite真实参数化计划显示SCAN ai_runs，只有主键索引。新增0072和模型表达式索引ix_ai_runs_research_scope（Case/run），共享固定JSON路径表达式以避免SQLite参数化JSON路径无法匹配索引。SQLiteCase/运行查询计划验证使用索引；迁移重复upgrade与downgrade覆盖，修复初版缺少Table元数据及SQLite表达式索引无法通用反射的问题。真实PG迁移通过，在5000条审计数据的自然执行计划中Case/运行查询均用索引。

**验证口径更正：**发现conftest.cmd_session始终SQLite，先前仅通过设置TEST_DATABASE_URL运行的混合批次，不能宣称所有API测试在PG上完成。先前原子审核及用量接口的PG总数声明以此更正为准；迁移验证确实连接PG，部分session测试也确实连接PG，但cmd_client测试未连接。此次用量API改为api_client/session，PG计划测试也用session，真实PG批次23passed（5.59秒），容器已清理。原子审核等历史cmd_client测试需要后续针对性PG复验。

后台全量后端测试已启动，session62521，日志/tmp/fund-engine-full-20260907.log，JUnit /tmp/fund-engine-full-20260907.xml；最近确认仍在运行。该批启动早于索引改动，不是冻结最终代码的完整验证，尚无通过结论。


## 原子审核API真实PostgreSQL复验（2026-09-07）

test_atomic_claims_api增加局部cmd_session夹具转接选定的session，并断言TEST_DATABASE_URL对应postgresql、默认对应sqlite。原子API文件22项在真实PG16升级至0072后通过（批次还含原子service及租户测试，总36passed/14.70秒；不将混合总数当成全PG接口计数），SQLite同文件22passed/3.34秒。临时PG容器已清理。该结果替代先前原子API误用固定SQLite夹具的PG声明。

后台全量测试session62521最新确认仍运行，约77%，已观察失败标记，尚未输出最终失败详情。继续等待同一进程，不重启，不宣称全量通过。日志/tmp/fund-engine-full-20260907.log，JUnit预定/tmp/fund-engine-full-20260907.xml。


## 全量结果与迁移失败修复（2026-09-07）

session62521已结束exit1：4403passed、11failed、46skipped（668.11秒）。10项为新增0072后升级head测试仍断言0071；1项仍是frontend/scripts/verify-live-event-ui.mjs缺失。现升级head断言读取真实Alembic脚本head，保留迁移内容/数据/触发器断言。0072表达式冻结到迁移文件，不再导入应用查询helper。

额外红测发现完整但未管理的旧ORM数据库会直接stamphead，缺少0072索引；现接管时先安装归属索引再stamp。迁移bootstrap+用量查询72passed/1skipped（79.18秒），真实PG用量/计划23passed（7.12秒），临时容器已清理，diff通过。此为针对性复验，不宣称当前全量通过；旧事件UI verifier仍需恢复真实完整流程，禁止用stub替代。


## 恢复市场研究入口与主张登记（2026-09-07）

中断后确认没有相关pytest/Playwright进程存活，续接eventMarket恢复文件。复用24a38c24^既有MarketExpressionContent、MarketInstrumentProfiles、FundDisclosureSyncTask与来源展示逻辑，隔离目录及CSS，新增懒加载EventMarketApp与工作台入口，market/stock/fund路径可加载。先读取当前Case已确认因素，再由用户明确填写市场操作人；恢复组件中的hardcoded human:researcher改为明确上下文。客户端固定/api/v1、same-origin，无浏览器bearer或任意VITE_API_URL；清除未使用旧接口。

浏览器初验确认登记成功但未关联因素的主张刷新后不可见，新增已审核主张列表并对受限来源隐藏内容。最终隔离浏览器验证冻结候选→fixture人工确认来源→页面明确操作人登记研究意见→API记录审核人→刷新可见；所有浏览器API请求无Authorization，390px截图实际查看可读无溢出。371单测通过（最终客户端简化前），全live20passed/28.3秒（最终简化前），客户端最终版重新跑市场场景1passed/13.3秒，最终build通过，diff通过。

恢复模块的全部按钮不等于完整验收通过：预测验证、公司股票基金传导、基金披露失败与运行/暂停整条旧脚本仍未恢复；旧verify-live-event-ui.mjs继续缺失，不生成虚假PASS。恢复模块还需API边界与来源有效期专项复查。
