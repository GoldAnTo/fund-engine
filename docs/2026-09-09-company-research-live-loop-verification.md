# 公司研究闭环实施与验收记录

状态：**本次 Alphabet 历史公司研究已走完真实原文 → 真实 LLM → 事实核对 → 确定性模型 → 备忘录复核 → 冻结 → 刷新回放 → Markdown 导出。** 工作区为 completed / 100%。正式估值仍为 not_answerable，无方向、置信度、价值或回报区间。

运行分支为 `codex/company-research-prototype-delivery`，独立环境为 `company-live-verification`，资料历史截止日为 `2026-08-25T23:59:59Z`。这是一次有助手内容复核的流程验收，不能据此声称任意公司、最新资料或无人审核的投资判断已经可用。

## 直接查看

- 页面：<http://127.0.0.1:53342/research/projects/6da87dde-5d81-4cee-967e-8f9679cbab22/versions>
- 冻结导出：`outputs/company-research-live-20260909/Alphabet-研究报告-冻结版本.md`
- 全部调用收据：同目录 `execution-audit.json`、`ai-run-*.json`。
- 原始机器稿：`draft-c48b2aefe1db44c7a8a8720c7a292816.json`；复核稿：`reviewed-memo.md`；精确编辑映射：`reviewed-memo-edits.json`。
- 冻结版本和导出收据：`frozen-revision.json`、`frozen-export-receipt.json`。
- 可回放归档：`research-frozen.sqlite`、`sources/raw`、`sources/text`、`provider-responses`。已经从只读数据库副本及归档原文调用正式读取与导出服务验证，证据见 `archive-replay-verification.json`。

这些制品保存在工作树的 ignored outputs 目录，不包含密钥或认证配置。页面是本地验收服务；归档回放使用本仓库代码及既有治理基础数据，不依赖运行时临时目录的原文。

## 本次真实执行身份

| 对象 | 值 |
| --- | --- |
| Project | `6da87dde-5d81-4cee-967e-8f9679cbab22` |
| Preparation | `dc1e8b5b-7b13-477e-bd6c-5a355df91120` |
| Draft | `c48b2aef-e1db-44c7-a8a8-720c7a292816` |
| 成功 AIRun | `a6a1fb71-8071-4674-8f38-29c6d9ea9c47` |
| 冻结版本 | `091bc569-72d2-49fa-aea0-93043577a5be`，序号 1 |
| 冻结时间 | `2026-09-09T10:14:20.553235Z` |
| 模型 / Prompt | `MiniMax-M2.7-highspeed` / `company-research-grounded-draft.v3` |

成功生成执行包含两次真实调用：首轮返回后有一处正文年份，修正轮通过完整验证；耗时 153.64 秒。输入 57,469、输出 7,969、合计 **65,438 tokens**，均为 provider reported。该项目先前另有一次双调用失败，因此项目总报告用量为 130,592 tokens。

全轮调试保留 11 条 AIRun（10 次失败执行、1 次成功执行），实际 13 次传输；已报告用量总计 263,225 tokens，另有 4 次传输失败的用量 unavailable，不能当作零。独立的 79-token 连接检查不计入这些合计。失败记录与过期 claim 恢复事件均保留，没有把无效响应改写成成功稿。

## 真实来源与事实核验

SEC 直连返回 403 后，使用 Alphabet 官网财报组件实际提供的官方 PDF，不需要联系邮箱。每次重新 HTTP 抓取；本次两份均为 HTTP 200。

| 原件 | 字节 | SHA-256 |
| --- | ---: | --- |
| 2025 年 10-K PDF，99 页 | 940068 | `9953e8d2e70b18c5bf0af3b2b2a1e96de0555f51cbb39a625497b434e19fb43a` |
| 2025 年第四季度业绩 PDF | 151984 | `8bb2778772fc60c0290cfeac3b24d345a07b8918a7708ec8fcef7239243922f9` |

来源收据 v2 保存实际 PDF 身份与哈希，并以 `governed_source` 保留既有 SEC HTML 身份、以 `fact_verifications` 记录报表对应证明；不把不同原始字节视作同一原件。原有 v1 仍可读取。

本次模型输入为 8 个冻结片段、40,379 字符。确定性目录含 190 个精确原文 ID，覆盖全部非空白文本。独立复核最终 49 处引用（26 条独立引用）均逐字匹配，来源、页码/文本位置与哈希一致。

浏览器逐项确认了七项 USD million 披露事实：年度 Search 224532、YouTube ads 40367、Subscriptions/platforms/devices 48030、Cloud 58705、Other Bets 1537、资本支出 91447，以及第四季度合并收入 113828。年度收入对照 PDF 第 34 页，资本支出对照第 53 页现金流表的 (91,447) 现金流出、以正的支出金额表达；季度收入对照季度 PDF 第 2 页。证据最终版本为 8，reviewed_fact_count 为 7。

本次重新获取的研究原文只有上述两份；证券身份、历史市场快照等沿用项目已有的治理基础数据，未声称全部市场输入均在本轮重新联网获取。

## 内容复核与浏览器验收

真实机器稿保留独立不可变记录，仍标记 machine_draft。助手按用户授权复核可编辑备忘录，修正集团/云分部现金流混用、折旧导致现金回报的错误因果、自研 GPU 误述、收入与调用量混淆、风险已发生的过度表述，并补充本次已捕获年报中实际披露的 revenue backlog。新增摘录位于 PDF 第 61 页（报告第 60 页），未自动提升为正式数值输入。

复核稿明确执行者为 Codex，不声称用户本人逐项审核；原始引用与事实附录保留。页面编辑器内容哈希与复核文件一致后才确认，冻结预览及刷新后的备忘录再次比对一致。

浏览器实际完成：七项事实确认 → 等待模型阶段至 85% → 编辑并确认判断 → 预览冻结 → 冻结发布至 100% → 刷新自动验证载入 → 手动查看冻结版本 → 导出。页面最后显示“Markdown 已验证并下载。”，正式 API 导出又与复核文件及本地保存内容逐字节比对。

| 完整性对象 | 哈希 |
| --- | --- |
| 初始输入 | `55a486c41b1a6cbc261825a3246b585ac687afd5b3947c6b3c5b46104f4594da` |
| 双调用输入链 | `71abfa1f3e09973eb91d046910da27cccd9963c8a4c432f861e5b73c946a63c5` |
| 调用收据 | `8f22324e45718f4f6946c29b802d49525540f22a9042f1be4773d7002800db0a` |
| 来源 bundle | `be03a8f78835a051383ad74449e8715c5d9604cf555924b3ef9bd63f2c8785a8` |
| 机器初稿内容 | `4ae679165f5b1b164bac384b379168c9ba35bcc0b0efb949d2939df5db4139a3` |
| 生成输出 | `40c7fa4d4aca312e497b50b3c2e69ed6663db8cba6c2f2b53748d67e3a25b734` |
| 复核稿文件（含末尾换行） | `cece9d3d918b414230ded1198bf3abcf10104904e2e1b26184ac872721f7263f` |
| 冻结 manifest | `9bfc1fbfb31c23e9c5d48af5540b0296ac48a360dd61fdc5a73d657ceb313775` |
| Markdown 导出，45887 bytes | `d480d4e6587061392b6f5a094af69944e0d75087d1c688323b86b107215c1670` |

## 本轮修复与验证

- 官方 IR 来源及旧来源：55 项测试通过；实际 PDF 文本提取、哈希、期间/单位/报表行和页面渲染核对通过。
- 生成器与真实运行集成：127 项测试通过，相关四文件 Ruff 通过。独立审查另执行 34 项调用、收据和历史兼容测试，全部通过。
- Provider 引用改为选择 ID，系统恢复精确摘录后仍执行原校验；未知 ID、跨来源事实绑定、数值正文等继续拒绝。
- v3 单次生成最多两次 chat_json，共用预算；第二次收到原响应和完整字段反馈。每次请求、响应、校验结果、用量与最终输入链均可核验，失败同样保留。旧 v1/v2 回放兼容，v3 初始 recipe 固定。原有 preparation 最多三次尝试仍有效；本地 max_attempts=1，因此单次生成最多两次实际传输。
- 原配置 60 秒读取超时已被安全诊断证实。本地运行改为单次 timeout 180 秒、总 budget 360 秒、max_completion_tokens 16384，模型不变。
- Worker 自动 claim 清除当前失败字段，保留历史事件；176 项测试通过、1 项跳过。相关两旧文件仍有 10 项基线 Ruff 诊断，无新增。
- 页面自动重试计划期间保持轮询，并在严格身份、版本和步骤守卫内接受来源完成状态；不再停在先前失败画面。工作台与视图 122 项测试通过，独立审查复跑全部通过；TypeScript 检查与最终 Vite 生产构建通过（88 modules，878ms）。前端 API 100 项此前回归通过。
- 完整 SQLite 升级至 0073；本次从只读归档数据库与归档原文验证正式回放、导出一致。PostgreSQL 尚未连接执行验证。

## 仍然存在的研究边界

当前正式 judgment 为 not_answerable。财务桥接结构已闭合，市场与证券输入已绑定；主要缺口是 15 项经营指标缺少已审核经营基线及对应确认数值，界面记录为 30 项核验任务。早期 research_gaps 制品中的市场缺失描述属于源准备时快照，应以最终 judgment 与绑定状态判断实际缺口。

下一步优先把云增长、云利润率、基础设施折旧/运营费用、自由现金流等披露整理为可审核基线，再确认情景预测输入并重算正式估值。来源/公司适配、最新披露覆盖、无人审核的分析质量均未完成；本轮的真实稿依然需要内容修订，不能宣称 LLM 已能直接给出可靠投资结论。

## 启用条件

执行数据库 0073 迁移，worker 设置 `COMPANY_RESEARCH_LIVE=1`，沿用现有 `LLM_*`；`COMPANY_RESEARCH_SOURCE_DIR` 指定原文目录，`COMPANY_RESEARCH_SOURCE_PROFILE=official_ir` 使用官方 PDF。默认 sec 路径仍支持原有访问标识配置。真实执行禁止 APP_ENV=test 和 mock client，未启用开关的历史工作流继续兼容。密钥及联系信息不进入仓库。
