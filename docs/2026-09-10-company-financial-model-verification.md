# 公司研究条件财务模型验收记录

本轮在已认可的七页公司工作台中接通了：历史财务基线 → 三情景五年预测 → 条件估值 → 草稿保存 → 刷新、历史回放与导出。新增的是附着原冻结研究的模型草稿，状态始终为 `unreviewed`。未生成正式研究 V2，未把条件模型提升为人工确认的投资结论。

分支：`codex/company-research-prototype-delivery`。资料截止日：`2026-08-25T23:59:59Z`。

## 查看结果

- [预测与情景](http://127.0.0.1:53342/research/projects/6da87dde-5d81-4cee-967e-8f9679cbab22/forecast)
- [价值判断](http://127.0.0.1:53342/research/projects/6da87dde-5d81-4cee-967e-8f9679cbab22/valuation)
- 工作树 `outputs/company-financial-model-20260910/Alphabet-条件估值模型草稿-1.md`、`Alphabet-条件估值模型草稿-2.md` 为可阅读导出，包含计算、输入理由、来源、局限和完整 JSON 附录。

本地运行数据及导出位于 ignored outputs 目录，不随代码提交；历史原文和财务基线的 v1 数据包随代码保存。归档回放依赖本仓库代码、既有治理基础数据及上一轮已归档原文，不依赖在线服务或临时运行目录中的原文。

## 实现范围

四份 Alphabet 官方原件支持 150 项历史财务事实，其中 126 项直接披露、24 项确定性推导。覆盖 FY2024/FY2025、Q2 2025/Q2 2026、H1 2025/H1 2026。每项保留期间、单位、集团或分部口径、原文定位和证明；读取时核对原始字节哈希、表格与推导。17 项研究缺口继续明确披露。

预测使用集团收入、营业利润率、现金税率、固定资产折旧、资本开支、营运资金变化六项驱动。基准、乐观、谨慎分别有完整的五年路径及情景机制；所有未来值与折现、终值、首年现金流比例均为显式研究假设。历史 FCF 与模型 FCFF 区分；终值包含增长所需再投资；首年剩余现金流金额与实际折现时点区分。价格比较标记为“价值与价格差”，不解释为年化收益率。

模型草稿绑定已认证的父研究、市场快照、财务基线、输入、结果及内容哈希。保存只追加记录，包含乐观并发与幂等校验。读取和导出重新认证记录链，并按保存时的 v1 基线和计算版本重算。更晚捕获的市场快照或未来默认计算版本不会改变已有草稿。

## 真实执行

| 对象 | 值 |
| --- | --- |
| 项目 | `6da87dde-5d81-4cee-967e-8f9679cbab22` |
| 原冻结研究 | `091bc569-72d2-49fa-aea0-93043577a5be` |
| 模型草稿 1 | `2221cd23-7825-44d7-a20c-a5168584a75d`，折现假设 10% |
| 模型草稿 2 | `1f38747a-b914-4c9f-bfdf-af0e14513c7a`，折现假设 12% |
| 数据库版本 | `0074` |
| 本轮新增 LLM 调用 | 0；原有 11 条 AIRun 保留 |

两份草稿由浏览器实际保存。第二份仅修改折现率及对应理由，其余经营路径完全一致，作为资本成本敏感性检查；这两个利率均未被认证为公司的正式资本成本。

浏览器验收完成：编辑并保存计算 → 刷新恢复草稿 2 与 12% 输入 → 加载历史草稿 1 恢复 10% 输入 → 导出成功提示 → 价值页载入草稿 2 → 基准、乐观、谨慎切换。页面明确保留“未复核”，原正式判断仍显示“当前不可回答”。

从 `research-with-financial-model.sqlite` 的只读连接及归档原文重放两份草稿，按正式 API 序列化后的记录与在线读取完全一致；两份 Markdown 导出逐字节一致。原冻结研究与其 Markdown 导出哈希亦一致。归档数据库 SHA-256 在回放前后相同，没有在线请求。

| 完整性对象 | SHA-256 |
| --- | --- |
| 历史基线 | `bbf540d45bc0b99535f9ece3c948e8a1dc36adafe904e76ca020856fe4ea6295` |
| 草稿 1 内容 | `cd982fa0a9f33367a4e46f60eb1e1613278845b16e50dc31eac5ba1121857abb` |
| 草稿 2 内容 | `993a1135340bc7bb804016cfd9c7edd9832a0ef69438adc3d5544d349425f6b5` |
| 草稿 1 导出 | `f00181c55ae8bf0d02351cd00fac4ef1ff2d2f84f83627e19e4757f21680c7af` |
| 草稿 2 导出 | `f8cdcb1fe3f31acb3594ff4338b1ea726208690c1e76e5b33f43016a516333a4` |
| 原冻结研究 manifest | `9bfc1fbfb31c23e9c5d48af5540b0296ac48a360dd61fdc5a73d657ceb313775` |
| 原冻结研究导出（未变化） | `d480d4e6587061392b6f5a094af69944e0d75087d1c688323b86b107215c1670` |

收据及可重跑脚本位于同一 outputs 目录：`execution-audit.json`、`archive-replay-verification.json`、`migration-roundtrip-verification.json`、`verify_archive_replay.py`、`verify_migration_roundtrip.py`。初次归档脚本 `capture_financial_model_audit.py` 为避免覆盖既有快照，拒绝重复写入相同备份路径。

## 检查结果

- 最终后端四组 101 项通过：计算 26、基线 26、草稿/API 21、市场输入 28。覆盖独立算例、篡改/截断原文、字段与单位边界、固定版本回放、迟到市场快照、跨项目/父版本校验、并发冲突、幂等重试及追加限制。
- 本轮另执行旧引擎、模型构建、发布回归及当时的模型/基线集，共 205 项通过、1 项跳过；与最终 101 项有重叠，不累加为独立用例数。
- 最终前端五组 250 项通过：模型面板 13、财务 API 13、工作台 91、原 API 100、视图 33。包括异常时保留输入、迟到响应隔离、历史恢复、导出哈希与身份校验。
- TypeScript 无错误，Vite 生产构建成功。重新生成 OpenAPI 和 TypeScript 契约到临时目录，两份文件均与仓库逐字节一致。
- 11 个本轮 Python 文件 Ruff 通过；ledger、原 router、原 engine 的 19 条规则问题经 HEAD 对照为既存问题。`git diff --check` 通过。
- SQLite 已在备份后从 0073 升至 0074。另一份隔离副本完成 0073 → 0074 → 0073 → 0074，表、追加限制触发器与完整性检查通过。PostgreSQL 分支尚未连接执行。
- 独立审查发现并修复损坏 gzip 异常边界、所得税费用名称、历史市场与默认版本漂移风险；全部相关测试随后通过。

复验命令（后端目录）：

```sh
.venv/bin/python -m pytest tests/test_company_research_financial_model.py tests/test_company_research_financial_baseline.py tests/underwriting/test_company_research_financial_workspace.py tests/underwriting/test_company_research_market_inputs.py -q
```

复验命令（前端目录）：

```sh
node scripts/with-project-node.mjs node_modules/vitest/vitest.mjs run src/features/investment-research/CompanyFinancialModelPanel.test.tsx src/data/companyFinancialModelApi.test.ts src/features/investment-research/ResearchWorkbenchPage.test.tsx src/data/InvestmentResearchApi.test.ts src/features/investment-research/companyResearchView.test.ts
node scripts/with-project-node.mjs node_modules/typescript/bin/tsc --noEmit
node scripts/with-project-node.mjs node_modules/vite/bin/vite.js build
```

## 下一阶段

先审视收入增长、集团利润率、资本开支/折旧、现金税、营运资金、折现率与终值回报率的依据，重点解释估值对终值的高依赖。拆清 Cloud 业务推理与集团财务路径的关系，把反证转成可检查的情景条件。

其后才能设计并接入正式输入复核与研究 V2 的发布边界。本轮没有补造未披露的经营指标，也没有消除原正式研究的 30 项阻塞记录。任意公司适配、最新资料刷新、无人复核的可靠投资判断仍未完成。
