# Fund Engine · 证据驱动的行业研究系统

[![backend-ci](https://github.com/GoldAnTo/fund-engine/actions/workflows/backend.yml/badge.svg)](https://github.com/GoldAnTo/fund-engine/actions/workflows/backend.yml)
[![frontend-ci](https://github.com/GoldAnTo/fund-engine/actions/workflows/frontend.yml/badge.svg)](https://github.com/GoldAnTo/fund-engine/actions/workflows/frontend.yml)

把原始资料变成**可审计的行业研究判断**：每个结论都能沿
`评估 → 证据快照 → 证据关系 → 原子陈述 → 原文片段`
回溯到冻结原文；AI 判断与人类复核以分离记录共存，机器结论永不被覆盖；
历史时点可回放，后公开的材料绝不泄漏。

系统的成功标准不是"生成一份看起来完整的研究报告"，而是让研究员持续回答：
当前命题得到什么支持、受到什么反驳、仍缺什么证据、判断如何随时间变化。

## 三条不可妥协的原则

1. **证据始终可追溯** — 每个判断可下钻到带确切位置的原文片段（门禁强制）
2. **AI 与人工边界可见** — AI 草案永久标记为临时，人工复核独立追加、不覆盖
3. **时点可回放** — 历史截止日之后的材料从所有视图中消失

## 架构

```
┌─────────────────────────────────────────────────────┐
│ 前端（React 18 + Vite，主题化研究外壳）                 │
│ 主题 → 工作台 → 审核中心 → 数据中心 → 快照版本           │
├─────────────────────────────────────────────────────┤
│ 契约层（OpenAPI → openapi-typescript 生成类型）        │
├─────────────────────────────────────────────────────┤
│ 后端（FastAPI + SQLAlchemy，/api/v1）                 │
│ 混合召回 │ 敞口计算 │ 合规门 │ 研究效能 KPI │ PDF 解析   │
├─────────────────────────────────────────────────────┤
│ 不可变账本（sqlite/PostgreSQL，Alembic 迁移）          │
│ 可选投影（Neo4j，可从账本完整重建）                     │
└─────────────────────────────────────────────────────┘
```

## 快速开始

```bash
# 后端：安装与测试（sqlite 默认，pg/neo4j 测试自动跳过）
pip install -e "./backend[dev]"
cd backend && python -m pytest -q                    # 218 passed

# 发布门禁：10 项金标检查（fail-closed）
docs/evaluation/reproduce.sh                         # 9 PASS + 1 SKIP

# 召回 A/B 评估（混合召回 vs BM25 基线）
cd backend && python scripts/eval_recall_ab.py       # recall@20: 0.7333 → 1.0000

# 前端：开发与测试
cd frontend && npm ci
npm run dev                                          # 默认 mock 模式，无需后端
npm test                                             # 62 vitest
npm run e2e                                          # 32 条 Playwright（macOS 12 用 PW_BROWSER_CHANNEL=chrome）
```

## 仓库结构

| 路径 | 内容 |
|---|---|
| `backend/` | FastAPI 账本服务、召回/合规/KPI 引擎、218 个测试 |
| `frontend/` | React 研究外壳、mock/HTTP 双适配器、62 vitest + 32 e2e |
| `docs/evaluation/` | 证据包：数据集清单、金标数据集、门禁报告、一键复现 |
| `docs/evidence-driven-research-report.md` | 技术报告（[PDF 版](docs/evidence-driven-research-report.pdf)） |
| `CONTEXT.md` | 研究上下文：核心词汇表、实现状态、验证体系 |
| `PRODUCT.md` | 产品基线：用户、设计原则、WCAG 2.2 AA |

## 质量保障

- **CI**：`backend-ci`（pytest + 发布门禁）与 `frontend-ci`（tsc + vitest + e2e）
  双流水线，按目录变更触发
- **分支保护**：main 要求 4 项检查全部通过方可合并（strict 模式），
  禁止 force push 与删除
- **e2e 三层覆盖**：结构锚点 → 只读断言（mock/真实后端皆可）→
  写入闭环（`?client=mock` 强制内存适配器，零真实 API 调用）

## 文档导航

- 想 30 分钟理解系统 → [技术报告](docs/evidence-driven-research-report.md)
- 想改代码 → [CONTEXT.md](CONTEXT.md)（词汇表 + 实现状态）
- 想做产品/设计 → [PRODUCT.md](PRODUCT.md)
- 想验证质量声明 → [docs/evaluation/](docs/evaluation/)（所有数字可复现）
