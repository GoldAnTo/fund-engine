# 后继运行的研究来源类别设计

## 背景与目标

工业富联真实验收发现：一份带巨潮资讯原始链接的公司年报以“粘贴快照”接入后，`SourceContract.source_type` 被存为 `pasted_snapshot`。后继运行冻结的范围是 `company_disclosure`，worker 因此排除了该材料。接入方式（粘贴、上传、网页）被错误地当作研究来源类别（公司披露、授权供应商等）。

本次目标是让研究员在冻结材料时同时记录两种不可混淆的属性：接入方式继续保存为 `source_type`，研究来源类别单独保存并供运行范围、召回和待审原子陈述过滤使用。后继运行在原子陈述审核后恢复时必须复用最初冻结范围，不能创建来源范围为空的新手工运行。

## 范围

- 为 `SourceContract` 新增不可变的 `research_source_type`；现有 `source_type` 不改名、不改变权限语义。
- 后端接受并验证可选的 `research_source_type`；缺省时等于原有接入方式，保证历史数据和普通粘贴流程兼容。
- 已发布 Case 的新材料表单增加“研究来源类别”选择。只有提供可复核 HTTP(S) 来源链接、且类别为 `company_disclosure` 时，才允许把粘贴的正文声明为公司披露；没有链接时不得伪装为公司披露。
- 运行范围过滤、原子陈述暂停门禁和召回过滤改为使用 `research_source_type`。
- 原子陈述审核恢复时重用暂停的 `ResearchRun` 与它的 scope event；不再创建 `trigger=manual`、`allowed_source_types=[]` 的替代运行。

不在本次范围：自动验证 URL 真伪、下载或解析年报 PDF、把公司披露自动发布为结论、修改既有已冻结 contract、批量迁移历史业务含义。

## 数据与授权边界

`research_source_type` 是陈述的研究类别，不等于 URL 的真实性。系统仍保留原始 `source_type`、来源链接、权限、租户、可用时间和不可变文本。对 `company_disclosure` 的新声明要求 HTTP(S) 链接，只防止空链接或内部伪 URI 被标为公司披露；人工仍需审核正文、发布方、期间和数值，机器不得自动采纳。

新字段由数据库迁移新增为非空字段，历史行以其既有 `source_type` 回填。创建 contract 时写入该值；任何内容哈希去重冲突仍要求接入方式、权限和研究来源类别完全兼容，不能借另一 Case 的声明扩大用途。

## 运行语义

运行的 `allowed_source_types` 保持接口字段名，以避免扩大本次 contract 变更；其含义明确为允许的 `research_source_type` 集合。`_pending_versions_in_run_scope`、`_pending_atomic_claims`、证据召回与提议均按该字段筛选。运行事件继续记录这一冻结数组，确保历史回放的解释不变。

审核一条原子陈述后，只有同一 Case、同一原始 run 的所有待审原子陈述都已有审核记录时，恢复该 run 的持久化 job。恢复不得重新调用 `start_from_monitor` 或丢失 scope event。事件必须记录“原冻结范围已重新入队继续执行”，并保留相同 run ID 和允许来源数组。

## 前端体验

在“已发布结论 · 新材料决定”表单中，保留“来源接入方式”，并新增“研究来源类别”。默认值等于接入方式；选择“公司披露”时显示说明：须提供可复核公开链接，接入方式仅说明文本如何进入系统。没有有效 HTTP(S) 链接时，保存按钮禁用并给出明确提示。表单提交在 `source_metadata.research_source_type` 中发送声明，现有后端 DTO 映射不暴露新的顶级写字段。

## 验收与测试

1. 后端：带 `source_type=pasted_snapshot`、`research_source_type=company_disclosure` 和 HTTP(S) 链接的冻结材料，能被只允许 `company_disclosure` 的后继运行提取；同一材料仍在 UI/审计中显示接入方式为粘贴快照。
2. 后端：没有 HTTP(S) 链接的 `company_disclosure` 声明被 422 拒绝；`pasted_snapshot` 默认继续按原类别处理并被公司披露范围排除。
3. 后端：审核原子陈述后恢复原暂停运行，ID 与 scope 中的 `allowed_source_types` 不变；不得出现一个空来源范围的 `manual` 后继运行。
4. 前端：选择公司披露时，未填写来源链接不能提交；填写链接后 payload 带 `source_metadata.research_source_type=company_disclosure`。
5. 工业富联真实复跑：同一年报以“粘贴快照 + 公司披露”进入 `material_continuation`，source-scope 事件不再排除它；旧 2024 人工发布结论在 `/history` 仍可回放，任何新 AI 判断都必须经过人工审核。
