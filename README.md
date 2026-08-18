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
# 真实本地 API：先复制 frontend/.env.local.example 为 frontend/.env.local，
# 填写后端地址与 RESEARCH_TENANT_TOKENS 中已配置的 Bearer token。令牌由
# Vite 本地代理注入，浏览器不会读取该值，再启动。
npm run dev:live                                     # 默认真实 API，不会回退到 mock
npm run dev:mock                                     # 显式内存 mock，用于 UI 演示和隔离测试
npm test                                             # 62 vitest
npm run e2e                                          # 32 条 Playwright（macOS 12 用 PW_BROWSER_CHANNEL=chrome）

# 真实 HTTP 闭环：临时 SQLite + Uvicorn + Bearer tenant，创建并读取事件 Case
cd .. && python backend/scripts/verify_live_event_api.py

# 默认前端真实闭环：临时 API + Vite + Chrome，浏览器创建事件后在事件台读回
cd frontend && PYTHON=../backend/.venv/bin/python PW_BROWSER_CHANNEL=chrome node scripts/with-project-node.mjs scripts/verify-live-event-ui.mjs
```

本仓库要求 Node.js 20+（`.nvmrc` 固定为 24）。真实人工闭环需要同时运行 API
与后台 worker：`cd backend && python -m app.scripts.run_research_worker --loop`。

## 一键本地运行（Docker）

日常使用只需执行以下命令；它会启动 API、研究 worker、资料采集 worker 和前端，
首次启动会构建当前代码镜像并创建独立的本地 PostgreSQL 数据卷。

```bash
# `.env` 保留你已有的 LLM、资料提供商等外部凭证；init 只生成本机运行所需凭证。
scripts/one-click-runtime.sh init
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh
```

前端入口是 [http://127.0.0.1:8080/events/new](http://127.0.0.1:8080/events/new)，
API 地址是 [http://127.0.0.1:8000](http://127.0.0.1:8000)。查看状态、停止新运行环境或
恢复旧应用服务分别使用：

```bash
scripts/one-click-runtime.sh status
scripts/one-click-runtime.sh down
scripts/one-click-runtime.sh rollback
```

`init` 生成的本地凭证保存在忽略的 `.env.one-click.local`，不会打印密钥；不要提交或
手工分享该文件。`down` 保留一键运行环境的数据卷。`rollback` 停止一键运行环境后，只恢复
本工具此前停止的旧应用容器。旧版 PostgreSQL 和 Keycloak 始终保留，既有数据不会被迁移或删除。

### 一键自动研究运行条件

真实环境要让一键研究从排队持续推进到结论，需要由 supervisor 把 API、研究
worker 和受治理资料采集 worker 配成三个独立 program；手工启动时也必须使用
三个独立终端，不能在同一终端串行执行以下命令。

终端或 program 1（API）：

```bash
cd backend
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端或 program 2（研究 worker）：

```bash
cd backend
.venv/bin/python -m app.scripts.run_research_worker --loop
```

终端或 program 3（资料采集 worker）：

```bash
cd backend
.venv/bin/python -m app.scripts.run_acquisition_worker --loop
```

两个 worker 都必须持续受监督运行，并先配置所启用来源需要的凭证，系统才会真正
自动采集、校验证据并恢复研究任务。`research_preparation` worker 仅供旧版
`reviewed` Case 使用；一键自动研究不依赖它。

启动后可用 `curl http://127.0.0.1:8000/health` 检查 API，并用已配置的 Bearer
token 读取 `/api/v1/research-runs/worker-status` 检查研究 worker 心跳；资料采集
worker 目前没有独立健康端点，应由 supervisor 检查进程存活并观察采集任务日志。
停止时向两个 worker 发送 `SIGINT`（前台运行可按 Ctrl-C）；资料采集 worker 也会
处理 `SIGTERM`，完成当前 claim 后退出，外部 provider/LLM 调用可能延迟停止。API
按 Uvicorn 的正常停止信号优雅关闭。

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
- **e2e 三层覆盖**：结构锚点 → 只读断言（mock 测试显式 `?client=mock`；产品默认始终连接真实 API）

## 文档导航

- 想 30 分钟理解系统 → [技术报告](docs/evidence-driven-research-report.md)
- 想改代码 → [CONTEXT.md](CONTEXT.md)（词汇表 + 实现状态）
- 想做产品/设计 → [PRODUCT.md](PRODUCT.md)
- 想验证质量声明 → [docs/evaluation/](docs/evaluation/)（所有数字可复现）
