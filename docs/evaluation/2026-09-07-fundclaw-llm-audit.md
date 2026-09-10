# FundClaw LLM 调用链审计（2026-09-07）

审计工作区：`<project-root>`。以当前未提交文件为准，保留现有工作；未使用主工作区替换代码。范围为调用链静态检查、离线回归和永久错误重试修复。未请求真实模型、未读取生产数据库、未输出凭据。

## 结论与优先级

| 优先级 | 已验证事实与影响 | 位置 | 处理 |
| --- | --- | --- | --- |
| P1（已修复） | `chat_json` 原先捕获全部 OpenAIError/HTTPError 并耗尽重试次数；401/400 等永久错误也会重复请求，延长失败并增加上游请求量。离线复现配置3次时实际调用3次。 | `backend/app/ai/client.py:173`；`backend/tests/test_gateway_llm_retry_policy.py:31` | 仅连接/传输/超时类及408/409/429/5xx可重试；其他状态或未知SDK错误立即转为固定安全异常。 |
| P1（待做） | 客户端请求未设置输出token限制，也无统一输入字符/token检查；超长材料会由供应商拒绝或消耗较大预算。 | `backend/app/ai/client.py:163`；`backend/app/ai/extraction.py:130`；`backend/app/ai/proposal.py:91`；`backend/app/ai/assessment_gen.py:141` | 建立模型兼容的输出cap与每操作输入预算，超限明确失败或分块并保留来源映射；不能静默截断反证。准备阶段已有120000字符及字段上限，不能说整个系统完全没有限制。 |
| P1（待做） | 客户端JSON容错修复后只检查dict；不消费finish_reason/refusal，不验证业务schema。无效或截断但可修复为对象的输出可能交给下游。下游有各自验证，但不一致。 | `backend/app/ai/client.py:193`；`:212`；`:222`；`backend/app/ai/extraction.py:159`；`backend/app/ai/proposal.py:114` | 对各操作建立严格结果类型、字段长度/枚举/集合上限；对length/refusal显式失败；把repair标记纳入审计。使用异常、数组、空对象、截断和多对象夹杂fixture回归。 |
| P2（待做） | 可重试错误仍立即重试，无LLM层backoff、jitter、Retry-After和整次操作deadline。HTTP传输timeout不能证明端到端墙钟时间上限，默认90秒×2也不应称严格180秒总上限。 | `backend/app/ai/client.py:173`；`:189` | 后续加入有上限退避及总体deadline，测试可注入时钟；与采集任务层退避分别核算，不能把`acquisition_runner.py:1461`的退避误认为LLM已具备退避。 |
| P2（待做） | completion usage/model/request_id/finish_reason被丢弃；AIRun是操作级记录，无法核算每次重试、合规rewrite的实际token和费用。自动研究budget计操作，非token账本。 | `backend/app/ai/client.py:227`；`backend/app/models/ledger.py:1253`；`backend/app/ai/runs.py:19`；`backend/app/services/auto_research.py:490`；`:625` | 增加按attempt计量的安全元数据记录及聚合，明确missing usage不可当零；模型价格外部版本化，默认不存原始prompt/response。 |
| P2（待验证） | extract/preparation明确声明输入材料不是指令；propose/assess/rewrite尚无同等明确的不可信输入规则。材料与系统指令虽分role，JSON序列化本身不构成抗注入保证。 | `backend/app/ai/prompts.py:24`；`:44`；`:70`；`:91`；`:113` | 增加统一边界规则及离线恶意来源fixture，验证输出不能改变授权、引用或业务动作。此次未证明真实模型能抵抗注入。 |
| P2（待做） | max_attempts初始化只检查小于1，不检查整数/布尔且无配置上限；环境变量转int不等于防止极大预算。 | `backend/app/ai/client.py:71` | 独立配置校验变更，覆盖bool/float/大值；避免把本次错误分类修复扩成配置改造。 |

优先级依据是资源可靠性与证据结果完整性。本次没有发现并证明跨租户LLM数据泄露或已发生的提示词注入；这些不能从静态风险推导为已发生事故。

## 调用链与边界

1. Gateway使用`backend/app/services/research_gateway_automatic_adapter.py:77`把native run投影到角色事件，`:108`检查租户/研究作用域，`:125`初始化角色运行。角色区域不能据此被描述为多个独立模型分别完成了分析；本轮设计`docs/superpowers/specs/2026-09-06-gateway-research-desk-design.md`明确独立角色调度不在范围。
2. 自动研究`backend/app/services/auto_research.py:62`懒构建LLMClient，`:484`抽取，`:533`构建提议与评估器。资料采集`backend/app/services/acquisition_runner.py:188`也使用StatementExtractor。研究准备`backend/app/ai/research_preparation.py:461`共用客户端协议。
3. 抽取将冻结span ID/原文放入user JSON（`extraction.py:138`），检查输出span属于输入并逐字定位quote（`:178`）；提议只接受输入statement ID且去重（`proposal.py:130`）。评估冻结输入link ID（`assessment_gen.py:136`），读取对应陈述，形成临时判断。它的prompt只含role/reason/statement_text，不能凭此宣称模型拥有全部来源等级、审核、时间元数据。
4. 上述三个引擎在调用模型前结束读事务（`extraction.py:157`、`proposal.py:112`、`assessment_gen.py:165`），并在输出持久化前检查当前输出槽，避免长时间占用读事务和过期任务污染结果。这是已有保护；本次回归包含相关引擎测试。
5. 准备生成器有明确上下文限制（`research_preparation.py:53`、`:510`）和严格结果验证（`:562`、`:608`、`:647`）。业务层验证不可被客户端“解析为dict”替代。
6. 合规rewrite通过同一client请求（`compliance_graph.py:159`），有一次rewrite的图状态和数组长度检查（`:161`、`:177`）。它也产生资源消耗，但目前不单独计量usage。

## 错误与审计安全

客户端对上游错误保留固定公开文本`LLM provider request failed`，对响应形状错误使用另一固定文本。原始异常作为`__cause__`保留用于诊断，调用方不能把完整traceback/response向用户或共享日志输出。新增永久错误用例使用哨兵私密文本并断言公开文本固定，原有客户端测试检查cause保留与消息脱敏。

`assessment_gen.py:275`在异常后回滚并使用固定分类写入AIRun；`backend/app/ai/error_safety.py:16`提供固定安全payload。AIRun记录模型配置名、prompt版本、输入引用、摘要、状态及时间；这不是供应商请求级账单，也不记录真实返回的模型版本。本次未做生产日志端到端采样，不能保证所有日志采集器均不展开cause。

## 实施与验证

唯一生产代码修改：`backend/app/ai/client.py`导入SDK的连接/状态错误类型，在既有重试循环区分状态与传输错误。保留原来的最大尝试次数、SDK隐式重试关闭、异常因果链和固定公开文本；本次没有增加等待策略或改动模型选择。

新增`backend/tests/test_gateway_llm_retry_policy.py`，25项离线测试覆盖：

- SDK和httpx的400/401/403/404/422立即失败；未知SDK错误不假定可恢复。
- SDK和httpx的408/409/429/500/503第二次成功时返回正确对象。
- SDK连接、httpx连接、Python超时及连接错误在2次预算内停止。

红阶段：新测试 **11 failed, 14 passed**，失败原因均为应调用1次而实际3次。绿阶段：新测试和既有客户端测试 **44 passed**。扩展回归 **119 passed, 2 warnings，5.45秒**，命令（cwd为本工作树backend）：

```sh
env -u TEST_DATABASE_URL -u NEO4J_URL -u LLM_API_KEY DATABASE_URL=sqlite:// APP_ENV=test .venv/bin/python -m pytest tests/test_gateway_llm_retry_policy.py tests/test_ai_client_determinism.py tests/test_ai_engine.py tests/test_research_preparation_generator.py -q
```

两条warning为既有Starlette anyio弃用提示和SourceLocatorV1字段schema遮蔽提示；没有失败。测试均使用注入completion、mock或SQLite隔离环境，未发出真实付费模型请求。此验证不涵盖PostgreSQL事务触发器、生产供应商兼容性、实际模型事实准确率、长上下文质量、真实限流恢复、延迟/成本分位数或多角色独立执行效果。

建议下一轮按顺序落地：严格输出协议及输入/输出上限 → 请求级usage/attempt账本与总体deadline → 提示词对抗fixture → 获得明确预算后的小样本真实模型评测。真实评测应冻结输入和预期证据ID，对引用可追溯性、反证保留、财务数字归属、缺证拒答、合规rewrite事实保持分别计分，不能仅以JSON可解析或页面显示“完成”判定研究正确。
