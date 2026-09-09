# 公司研究原型落地：第一阶段执行记录

> 按已确认的七页公司研究原型执行。当前阶段交付真实入口、研究问题、产品状态和七页阅读路径；精确原文、关键输入重算与版本比较继续按总方案后续阶段完成。

**目标：** 用户可以搜索公司、确认研究范围与可选关注问题、创建并继续真实研究，通过固定七页阅读已有结果和缺口。

**架构：** 复用现有 project/scope/basis/preparation/job/artifact/publication，研究问题进入不可变研究范围。服务端投影产品状态，前端保持严格契约校验和冻结版本验证。React 页面按原型组织已有模块，不在浏览器生成研究事实。

**技术栈：** FastAPI / Pydantic / SQLAlchemy，React / React Router / TypeScript，pytest / Vitest。

## 执行任务

- [x] 1. 后端范围与状态：先测试关注问题规范化、预览哈希、幂等重试、工作区恢复、稳定产品状态和发布身份验证，再扩展现有契约与服务，更新生成类型与客户端校验。
- [x] 2. 七页阅读路径：先测试导航、直接访问、返回与页面内容归组，再接入研究库、概览、商业、预测、价值、证据和版本路径；保留全部发布/审核/回放防护。
- [x] 3. 研究入口：先测试问题输入与预览绑定、重试保持范围、目录加载及继续研究，再完成入口文案、关注问题、研究库和应用导航。
- [x] 4. 整体验证：跑相关后端/前端行为测试、类型检查和构建；独立检查规格与代码；浏览器验证实际路由和可访问状态，记录本轮已完成能力与后续缺口。

## 验证命令

```bash
# 在 frontend 内，使用仓库固定版本的 Node
node scripts/with-project-node.mjs node_modules/vitest/vitest.mjs run src/features/investment-research src/data/InvestmentResearchApi.test.ts
node scripts/with-project-node.mjs node_modules/typescript/bin/tsc --noEmit
node scripts/with-project-node.mjs node_modules/vite/bin/vite.js build

# 在 backend 内
.venv/bin/python -m pytest tests/underwriting/test_company_research_run_contract.py tests/underwriting/test_company_research_api.py tests/underwriting/test_company_research_initializer.py tests/underwriting/test_company_research_policy.py tests/underwriting/test_company_research_workbench.py tests/underwriting/test_company_research_publication.py -q
```

## 基线与边界

- 起点：`16780259`，隔离分支 `codex/company-research-prototype-delivery`。
- 后端 API 与初始化基线：80 passed；已有两条依赖警告。
- 首次前端运行误用系统 Node 18，5 条 Web Crypto 相关失败；后续统一使用仓库 Node 启动器，不据此修改业务代码。
- 当前正式公司初始化仍有受支持适配器与历史截止约束。本轮如实展示这些边界，不能将原型双案例视为已完成的通用真实研究。
- 不覆盖原目录未提交文件，不修改静态原型，不部署外部服务。

## 已接通的实际行为

- 公司搜索和证券确认继续调用正式接口；编辑搜索词或关注问题会立即废弃旧预览，避免在迟到响应上创建错误对象。
- 关注问题规范化后进入现有不可变 scope，同时参与预览、初始化、恢复和发布的身份验证；旧版未填写问题的哈希保持兼容。问题目前用于保存研究范围和展示，尚未参与模型生成。
- 请求截止时间与实际可用资料截止时间分别核验。服务端把现有 preparation 投影为稳定产品状态，初始化、紧凑状态读取、恢复和工作台使用同一投影。
- 研究库显示已有研究、保存的问题和状态，并可继续同一项目。单项状态读取失败不会隐藏研究；旧版缺少新进度字段时保留已有错误信息和可用的重试入口。
- 七页 URL 可以直接进入、刷新和返回。商业、预测、价值、证据内容来自已有真实工作台数据；保存和回放操作集中在备忘录与版本页。
- 概览把同一指标的基线与驱动缺口合并为中文说明，全部原始阻塞记录保留在折叠审计详情；未知代码保持通用提示，不改变任何计算或发布门槛。

## 验证记录

- 后端相关契约、API、初始化、政策、工作台及发布测试：**296 passed，1 skipped**。跳过项要求 `TEST_DATABASE_URL`，本轮未配置该独立数据库；另有两条已有依赖警告。
- 前端入口、工作台、视图和 API 契约综合测试：最终 **233 passed（5 个文件）**；TypeScript 类型检查、Vite 生产构建及 `git diff --check` 全部通过。
- 独立审查提出研究范围替换和旧版失败重试两项 P2，均已修复；审查代理独立重跑三条相关回归测试通过，确认两项关闭，复核范围内无遗留阻断问题。
- 浏览器使用独立临时 SQLite、正式 API 和 worker，以及仓库自带 Alphabet 样例资料。完整走通公司搜索 → 填写关注问题 → 预览并创建 → 证据确认 → 生成已有模型与缺口 → 修改备忘录并确认 → 冻结保存 → 刷新回放 → 验证后导出 Markdown → 从研究库继续同一版本。
- 保存后问题“云业务增长能否改善长期现金流？”及资料截止时间均保留；冻结版本 1 刷新后自动验证并载入，导出显示“Markdown 已验证并下载。”。样例资料不足，最终保留“当前不可回答”和研究缺口，未生成估值。
- 浏览器检查 1280 px 和 390 px 两种宽度，页面均无横向溢出；测试后恢复默认视窗。
- 本地预览：[研究库](http://127.0.0.1:55714/research)。临时预览服务需保持运行，样例内容仅用于功能验证。

## 后续阶段（本轮未实现）

1. 取消普通资料必须全部处理才能构建初稿的整体门槛，按依赖关系保留已有可读分析与计算缺口。
2. 原文阅读器定位到实际冻结材料的精确引用和上下文。
3. 关键输入决策、用户假设与可追溯重算，以及真实版本差异比较。
4. CATL 正式来源与模型初始化适配、双案例完整验收。目前不宣称支持任意公司，也不把样例资料当作实时采集结果。
