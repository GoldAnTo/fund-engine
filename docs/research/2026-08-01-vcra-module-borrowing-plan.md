# VCRA 模块级借鉴落地清单

> 调研日期：2026-08-01
> 对象：[Verifiable-Company-Research-Agent](https://github.com/Yaoniguan-Money/Verifiable-Company-Research-Agent)（MIT，main 分支浅克隆，10 commits）
> 本地参考副本：`.reference/vcra/`（未跟踪，仅供读代码，用完可删）
> 结论：不重写架构、不 fork、不引依赖；按下表逐模块"读代码 → 重写进我们的账本模型"。

## 映射总览

| VCRA 工作流节点 | Fund Engine 对应 | 借鉴方向 |
|---|---|---|
| Collect Sources | `app/datasources/gildata/` + 文档冻结 | 不借（我们更强） |
| Ingest Chunks | `DocumentVersion` + `SourceSpan` | 借分块策略思想（见 §3） |
| Retrieve | **无**（proposer 全库 LIMIT 20） | **借整条混合检索（见 §1）** |
| Extract Facts | `app/ai/extraction.py`（纯 LLM） | **借表格规则链路（见 §2）** |
| Verify Facts | `ReviewDecision`（人工） | 借单位/指标归一化（见 §4） |
| Grounded Report | `AIAssessment` + 冻结快照 | 不借（我们更强） |
| Compliance Check | 无（只有产品自律） | **借合规判定层（见 §5）** |
| 评测交付 | `docs/evaluation/` 半成品 | **借证据包结构（见 §6）** |

---

## §1 混合检索管道（P0，治"跨案例污染"）

**借：** `backend/app/services/rag/hybrid_retrieval.py`（202 行）、`rrf.py`（26 行）、`reranker.py`（143 行）

**它做什么：** 查询优化 → Dense 向量 + BM25 双路召回 → RRF（k=60）融合 → 重排。重排三后端可切换：词面重叠（LexicalReranker，无依赖，CI 用）、ONNX cross-encoder（`BAAI/bge-reranker-base`，CPU ~527ms，中文财报实测最优）、Embedding API。BM25 语料按内容哈希缓存。

**改我们哪里：**

- 新建 `backend/app/services/recall.py`：以 `SourceSpan.verbatim_text`（或其关联 `SourceStatement.normalized_text`）为语料，输入 thesis 文本，输出按相关性排序的 statement 列表
- 改造 `backend/app/ai/proposal.py`：`select(SourceStatement).limit(20)` → `recall.for_thesis(thesis, scope=case, cutoff=...)`，同时补上案例范围和 cutoff 约束
- 删除硬编码 scope 缺省值 `{"segment": "AI算力"}`（改为必填或由 thesis 推导）

**移植注意：**

- `rrf.py` 是纯函数，直接搬
- 第一版只用 `LexicalReranker`（零依赖）+ BM25（`rank_bm25` 包）即可闭环；ONNX reranker 等接真实材料后再加，别一开始就背 transformers 依赖
- 它的 score 只用于排序，**不要**把检索分数写进账本或展示为证据强度（与我们删除置信度的决定一致）
- 需要决定 embedding 后端；MVP 可先 BM25-only + LexicalReranker，Dense 路留接口

**工作量：** 约 1-2 天（含测试）

## §2 表格规则抽取链路（P0，财报数字不靠 LLM 猜）

**借：** `backend/app/services/financial_table_extraction.py`（351 行）+ `fact_plausibility.py` + `fact_patterns.py`

**它做什么：** 一个状态机处理"表头年份行 + 指标行"结构：跟踪当前年份表头和单位行（`单位：亿元`），按行识别指标（营业收入/归母净利润/扣非/研发费用/产能产量销量），抽数值并绑定期间；带一整套防呆——噪声维度过滤（"其中""增值税"等）、百分比值不进绝对值指标、合理性校验、宽表特例。

**改我们哪里：**

- 新建 `backend/app/services/table_extraction.py`：输入 `SourceSpan`（表格类），输出 `SourceStatement` 候选（`kind=disclosed_fact`，`observed_period` 从表头年份来）
- 改造 `app/ai/extraction.py`：表格 span 先走规则链路，LLM 只处理叙述句；规则抽出的也走同样的 pending/审核门
- 它的 `confidence=0.78` 字段**不要带过来**——我们的陈述强度由审核状态和来源等级表达

**移植注意：**

- 它的维度模型（`revenue_segment:云计算` 这种 `指标:维度` 键）正好补我们 `SourceStatement` 缺指标语义的短板，但应落到我们的 scope JSON 里，不要新造字段体系
- "禁止 LLM 做运算、单位必须原文出现"的 prompt 纪律合并进 `app/ai/prompts.py`（prompt 版本升到 extract-v2）
- 它配套的 450 个测试里有大量中文财报表格用例，测试模式一并参考

**工作量：** 约 2-3 天（含中文表格样例测试）

## §3 语义分块策略（P1）

**借：** `backend/app/services/chunking_strategy/section_aware.py`（150 行）

**它做什么：** 利用财报章节标注在章节边界处分块，而不是固定窗口硬切。

**改我们哪里：** 影响的是 ingest 时 `SourceSpan` 的切分策略（`app/services/ingest.py` 或未来的 Docling 适配器）：公告/财报按章节/表格边界切 span，让每条 span 语义完整、定位稳定。

**注意：** 等我们接了 Docling 后，章节信息来自解析器，这里借的是"按结构边界切"的策略，不是代码本身。

## §4 指标注册表与归一化（P1，矛盾检测的地基）

**借：** `backend/app/domain/metric_registry.py`（138 行）+ `backend/app/services/fact_metric_normalization.py`（70 行）

**它做什么：** 7 个指标族（研发/利润/收入/收入结构/产能/业务/风险），每族定义意图词、陈述词、首选指标、单位族；归一化器把"研发费用/rd_expense/R&D 费用"等别名收敛成稳定的可比较键，同时保留会计口径边界（归母≠净利润≠扣非），维度部分单独归一化。

**改我们哪里：**

- 新建 `backend/app/domain/metrics.py`：这是未来"矛盾检测"和估值口径治理的地基——两条"营收 5 亿"和"营收 50000 万"的陈述先归一化再比对，否则全是假矛盾
- 与文档 1 的"信息有左有右"直接相关：正反识别的前提是可比性
- 单位归一化（元/千元/万元/亿元）同步放在这里

**注意：** 它的族划分偏车企/制造业样例，我们要按 AI 算力链的实际材料重订指标族，结构照搬、内容重填。

**工作量：** 约 1 天

## §5 合规判定层（P1，把"不做推荐"从文案变成机制）

**借：** `backend/app/compliance/rules.py`（183 行）

**它做什么：** 6 类违规（买卖建议/目标价/收益承诺/荐股/仓位指导/个性化投顾），三级动作（ALLOW/REWRITE/REFUSE），默认拒绝的保守口径；工程细节扎实：先剥离 base64 图片再扫、ASCII 关键词整词匹配防误伤、每类只记首个命中。

**改我们哪里：**

- 新建 `backend/app/services/compliance.py`：`AIAssessment.rationale`、propose 的 reason 等所有 AI 生成文本入库前过一道判定；命中 REFUSE 类直接拒绝该次输出并记录到 AIRun
- 将来若开放 API 输出文本（如 wiki 页的 AI 摘要），复用同一入口

**注意：** 关键词词典按我们的语境裁剪（保留全部 6 类，补"配置方案""调仓建议"等）；它是规则层，不要期待语义层判断，够用即可。

**工作量：** 约半天

## §6 评测证据包结构（P1，升级现有 evaluation）

**借：** `evidence/` 目录的组织方式和 README 写法

**它做什么：** `dataset-manifest.json` + `experiment-config.json` + `datasets/` + `raw/` + `failures/` + `reports/` + 一键复现脚本；README 里直写局限性和"面试问答"（数据是合成的、不能外推、no-answer 全误命中已保留）；缺数据集时评测**fail-closed**（以前缺数据返回假分数，现在直接报错）。

**改我们哪里：**

- 把 `docs/evaluation/` 重组成同构证据包：gold set 进 `datasets/`，发布门禁结果进 `reports/`，`verify_ai_compute_slice.py` 输出进 `raw/`（目前 runs/ 被 gitignore，应改为保留汇总 JSON、忽略大文件）
- 发布门禁加一条 fail-closed：gold set 缺失时脚本必须非零退出
- 门禁指标从纯结构检查扩到质量检查：抽取准确率、提议采纳率（人工审核 confirmed/rejected 比例）——这是回答"AI 提议质量够不够"的唯一办法

**工作量：** 约 1 天

## §7 Provider 失败纪律（P0，一行改动防生产事故）

**借：** 它的 ProviderFactory 原则：真实 provider 缺 key **直接失败**，绝不隐式 fallback 到 mock。

**改我们哪里：** `backend/app/ai/client.py` 的 `LLMClient.from_env()`：当前无 `LLM_API_KEY` 静默进 mock。改为：默认仍允许 mock（开发/测试），但生产模式（如 `APP_ENV=production`）下缺 key 直接 raise；AIRun 里 `mock-` 前缀已经有了，保留。

**工作量：** 小时级

## §8 Workflow 节点级审计（P2，接完整管道时再做）

**借：** 它的 17 节点 StateGraph 每节点记 step result、关键分支记 WorkflowDecision 的做法。

**改我们哪里：** 当我们把"收集→抽取→提议→评估"串成作业（jobs）时，`AIRun` 从"一次 AI 调用一条"扩成"一次作业 + 每个节点一条 step 记录"。现在管道只有三步直连，先不动。

**注意：** 只借"节点级记录"的思想，**不引 LangGraph**。

---

## 明确不借

- `confidence=1.0` 直通 verified（与我们认识论根本冲突）
- task/report 单次研究模型（我们的持续 ResearchCase + 快照是壁垒）
- LangGraph / 追问聊天 / chat memory（不是我们的产品形态）
- 它的 SQLite/向量库选型（我们用 PG + 账本）

## 建议落地顺序

1. **§7 Provider 纪律**（小时级，先防事故）
2. **§1 检索召回进 proposer**（治当前最大的正确性缺口）
3. **§2 表格规则抽取**（接真实财报前必须具备）
4. **§5 合规层**（任何对外输出前就位）
5. **§6 评测证据包**（与 §1§2 的金标同步建）
6. **§4 指标归一化**（矛盾检测开工前）
7. **§3 §8**（接 Docling / 建 jobs 时顺带做）
