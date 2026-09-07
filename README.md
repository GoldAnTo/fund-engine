# Fund Engine · 证据驱动的行业研究系统

[![backend-ci](https://github.com/GoldAnTo/fund-engine/actions/workflows/backend.yml/badge.svg)](https://github.com/GoldAnTo/fund-engine/actions/workflows/backend.yml)
[![frontend-ci](https://github.com/GoldAnTo/fund-engine/actions/workflows/frontend.yml/badge.svg)](https://github.com/GoldAnTo/fund-engine/actions/workflows/frontend.yml)

把原始资料变成**可审计的行业研究判断**：每个结论都能沿
`评估 → 证据快照 → 证据关系 → 原子陈述 → 原文片段`
回溯到冻结原文；AI 判断与人类复核以分离记录共存，机器结论永不被覆盖；
历史时点可回放，后公开的材料绝不泄漏。

系统的成功标准不是"生成一份看起来完整的研究报告"，而是让研究员持续回答：
当前命题得到什么支持、受到什么反驳、仍缺什么证据、判断如何随时间变化。

## 当前 checkout 状态（2026-09-06）

前端默认使用 **FundClaw 真实事件研究工作台**，已接入研究列表、创建与详情；`/?client=mock` 显式打开演示。API 失败不会回退示例。完整研究审核、冻结回放与导出仍在恢复中，详见实施跟踪；下述架构包含目标能力。

旧 `POST /api/v1/research-cases` 无来源创建入口现已退役：未认证返回 401，认证后返回 409，并指引使用 `POST /api/v1/event-research` 建立有原始资料和租户归属的 Case；旧 workbench 与新增 thesis 均检查归属。

完整检查、已实施修复和后续优先级见 [项目检测报告](docs/evaluation/2026-09-06-project-audit.md)。旧 Company Research 浏览器验收脚本随旧前端退役而缺失，相关验收目前不能通过，不能用演示 E2E 替代。

## 三条不可妥协的原则

1. **证据始终可追溯** — 每个判断可下钻到带确切位置的原文片段（门禁强制）
2. **AI 与人工边界可见** — AI 草案永久标记为临时，人工复核独立追加、不覆盖
3. **时点可回放** — 历史截止日之后的材料从所有视图中消失

## 已审阅行业候选证据：发布边界

行业候选证据是可审计的资料记录，不是行业结论或模型输入。一个候选 dossier 必须
绑定冻结的来源清单和历史截止日：每个来源、精确 locator、可得时间、授权/留存边界
与内容哈希都要通过检查。发布还需要两位不同、可审计身份的独立批准：
`provenance` 审阅来源、授权、定位和时间；`methodology` 审阅范围、统计口径、图表
转录误差、假设/Unknown 标签及禁止拼接规则。

即使两项审阅均已批准，发布结果仍只是 `industry_evidence_candidate` 和
`not_answerable` 研究债务。它不会写入正式机制、IndustryState、情景、敞口、Earnings
或预测，也不会产生估值、价格或投资建议。以后若要形成正式 IndustryState，必须另行
冻结独立的、可复核的指标定义与观察值，完成来源覆盖/口径核对，并由因果审阅明确
传导方向、替代解释和 falsifier；候选本身不能自动满足其中任何一项。因而系统不把
候选数值表述为实际利用率或价格机制。

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
# 仓库根目录：后端隔离测试环境
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e "./backend[dev]"
(cd backend && .venv/bin/python -m pytest -q)

# 冻结账本发布门禁，使用临时 SQLite；非在线 LLM 质量测试
APP_ENV=test bash docs/evaluation/reproduce.sh

# 前端需要 .nvmrc 指定的 Node 24
nvm install
nvm use
cd frontend
npm ci
npm run dev                         # 配置 frontend/.env.local 后连接真实 API
npm test
npm run build
npx playwright install chromium
npm run e2e                         # 桌面/移动演示交互回归
npm run e2e:live                    # 隔离后端真实 HTTP 创建/刷新
```

后端全量测试仍保留旧前端联调契约，缺失脚本相关失败见检测报告。当前不提供 `dev:live`、`dev:mock` 或 `verify:live-company-research` 命令。后端真实 HTTP 事件检查可独立运行 `backend/.venv/bin/python backend/scripts/verify_live_event_api.py`。

### 灌入示例业务数据

一键运行起来的 Postgres 默认是空账本。`/api/v1/research-cases` 走 tenant
admission 过滤，所以跑完 seed 还必须显式 admit 这个 case 才能在列表接口看到。
下面以一键运行 (`fund-engine-one-click-*` Compose project) 为例，租户
`local-one-click` 已在 `.env.one-click.local` 的 `RESEARCH_TENANT_TOKENS` 里
预置：

```bash
# 1) 在 backend 容器镜像里跑三个 seed 脚本（fixture 来自宿主 backend 目录）
docker run --rm --network fund-engine-one-click_default \
  -e DATABASE_URL='postgresql+psycopg://one_click:$(grep ^ONE_CLICK_POSTGRES_PASSWORD .env.one-click.local | cut -d= -f2)@postgres:5432/fund_engine_one_click' \
  -v "$(pwd)/backend":/app -w /app \
  fund-engine-one-click-backend:local \
  python -m app.scripts.seed_semiconductor_complete_theme_case

docker run --rm --network fund-engine-one-click_default \
  -e DATABASE_URL='postgresql+psycopg://one_click:$(grep ^ONE_CLICK_POSTGRES_PASSWORD .env.one-click.local | cut -d= -f2)@postgres:5432/fund_engine_one_click' \
  -v "$(pwd)/backend":/app -w /app \
  fund-engine-one-click-backend:local \
  python -m app.scripts.seed_storage_chain_case

docker run --rm --network fund-engine-one-click_default \
  -e DATABASE_URL='postgresql+psycopg://one_click:$(grep ^ONE_CLICK_POSTGRES_PASSWORD .env.one-click.local | cut -d= -f2)@postgres:5432/fund_engine_one_click' \
  -v "$(pwd)/backend":/app -w /app \
  fund-engine-one-click-backend:local \
  python -m app.scripts.seed_ai_compute_case
```

`seed_*` 脚本只 freeze 文档、建 case/theses/evidence，并把 frozen document 写进
`case_document_versions`。**它不会建 `case_tenant_admissions`**，因此
`/api/v1/research-cases` 默认看不到这些 case——还差一步：

```bash
# 2) 为每个 seed 出来的 case 建 tenant admission，让本地租户能看到
docker run --rm --network fund-engine-one-click_default \
  -e DATABASE_URL='postgresql+psycopg://one_click:$(grep ^ONE_CLICK_POSTGRES_PASSWORD .env.one-click.local | cut -d= -f2)@postgres:5432/fund_engine_one_click' \
  -v "$(pwd)/backend":/app \
  -w /app \
  fund-engine-one-click-backend:local \
  python - <<'PY'
import os, uuid
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.services.case_tenant_access import CaseTenantAccess
from app.models.ledger import CaseDocumentVersion, ResearchCase

engine = create_engine(os.environ["DATABASE_URL"], future=True)
with Session(engine) as session:
    cases = session.scalars(select(ResearchCase)).all()
    for case in cases:
        attached = session.scalar(
            select(CaseDocumentVersion.document_version_id)
            .where(CaseDocumentVersion.research_case_id == case.id)
            .limit(1)
        )
        if attached is None:
            print(f"SKIP (no doc attached): {case.id} {case.title}")
            continue
        try:
            CaseTenantAccess(session).admit_legacy_case(
                case_id=case.id,
                tenant_id="local-one-click",
                initial_document_version_id=attached,
                admitted_by="seed-script",
                admission_reason="auto-admit seeded fixtures",
            )
            print(f"ADMIT: {case.id} {case.title}")
        except Exception as exc:
            print(f"FAIL {case.id}: {exc}")
    session.commit()
PY
```

完成后用前端投资研究入口 (http://127.0.0.1:8080/research) 或直接调 API 验证：

```bash
TOKEN=$(grep ^RESEARCH_BEARER_TOKEN .env.one-click.local | cut -d= -f2)
curl -sS -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8000/api/v1/research-cases?limit=10'
```

### 旧 Company Research 浏览器验收待恢复

旧验收覆盖临时 SQLite、真实 API/worker、Bearer 代理、人工复核、冻结版本回放和 Markdown 导出。新前端尚未接入这些流程，相应 `frontend/scripts` 已不存在。`.github/workflows/backend.yml` 中的真实浏览器门禁仍会失败，保留这个缺口有助于避免把演示发布误认为产品验收。

研究列表、创建与详情已接入，`npm run e2e:live` 验证隔离后端上的创建/刷新。下一步继续恢复任务进度、证据复核、版本回放和导出；`npm run e2e` 仍只验证演示交互。

## 一键本地运行（Docker）

日常使用只需执行以下命令；它会启动 API、研究 worker、资料采集 worker 和前端，
首次启动会构建当前代码镜像并创建独立的本地 PostgreSQL 数据卷。

```bash
# `.env` 保留你已有的 LLM、资料提供商等外部凭证；init 只生成本机运行所需凭证。
scripts/one-click-runtime.sh init
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh --stability-seconds 600
```

默认只启动一个资料采集 worker，适合 16 GiB Mac、Docker Desktop 分配约 8 GiB
的本地环境。Docker Desktop 至少分配 6 GiB；不足时 `up` 会在停止旧服务前失败关闭。

资料采集 worker 数量由 `ONE_CLICK_ACQUISITION_REPLICAS` 控制，该变量默认值为 1：

```dotenv
ONE_CLICK_ACQUISITION_REPLICAS=1
```

需要提高采集吞吐时，编辑受保护的 `.env.one-click.local`；提高吞吐时请改为 2–4，
例如先扩为两个 worker：

```dotenv
ONE_CLICK_ACQUISITION_REPLICAS=2
```

允许值为 1–4。扩容后仍必须运行同一稳定性门禁，持续检查容器健康、重启、OOM、数据库
连接预算和 HTTP 端点：

```bash
scripts/verify-one-click-runtime.sh --stability-seconds 600
```

`DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW`、`DATABASE_POOL_TIMEOUT_SECONDS` 和
`DATABASE_POOL_RECYCLE_SECONDS` 仅用于高级本地调优。各服务的内存、CPU 资源上限及
默认值在 `.env.one-click.example` 中列出，可通过同名变量配置。

独立投资研究入口是 [http://127.0.0.1:8080/research](http://127.0.0.1:8080/research)；
旧事件研究入口仍是 [http://127.0.0.1:8080/events/new](http://127.0.0.1:8080/events/new)。
API 地址是 [http://127.0.0.1:8000](http://127.0.0.1:8000)。查看状态、停止新运行环境或
恢复旧应用服务分别使用：

```bash
scripts/one-click-runtime.sh status
scripts/one-click-runtime.sh down
scripts/one-click-runtime.sh rollback
```

备份和恢复都要求 API、前端和 worker 已停止，但 PostgreSQL 保持运行，以保证数据库与
文件处于同一个静止边界。备份目标必须是尚不存在的具体绝对目录。恢复会先校验精确的
三件套、SHA-256 和 tar 路径，再在隔离的暂存数据库与文件卷中验证迁移、身份 fixture
和研究版本回放，通过后才切换。恢复只会写入带有当前 Compose project/logical-volume
标签的文件卷；临时卷由 Docker 随机命名，并以本次恢复的高熵 operation UUID 加上
project、purpose 标签限定清理范围：

```bash
scripts/one-click-runtime.sh backup /absolute/path/to/new-backup
scripts/one-click-runtime.sh restore /absolute/path/to/backup
```

`up` 也会在启动 PostgreSQL 或 migrate 前核对数据库卷的 Compose project 和 logical
volume 标签；新环境先只执行 `compose create postgres`，标签复核通过后才启动服务。
恢复暂存库和原库快照名使用独立的 128-bit operation UUID。若 `createdb` 因同名库而
失败，清理逻辑不会删除那个既有数据库。

tar 校验默认限制为：压缩文件 1 GiB、100,000 个成员、单文件 512 MiB、解压总量
2 GiB、最大压缩比 200；同时只接受普通文件和零负载目录组成的简单 USTAR，不接受
PAX/GNU 扩展头、链接或特殊文件。checksum manifest 上限为 64 KiB，PostgreSQL dump
上限为 8 GiB。可用同名的 `ONE_CLICK_BACKUP_MAX_COMPRESSED_BYTES`、
`ONE_CLICK_BACKUP_MAX_MEMBERS`、`ONE_CLICK_BACKUP_MAX_SINGLE_FILE_BYTES`、
`ONE_CLICK_BACKUP_MAX_TOTAL_BYTES` 和 `ONE_CLICK_BACKUP_MAX_COMPRESSION_RATIO`
环境变量设置其他正整数上限；manifest 和 dump 对应
`ONE_CLICK_BACKUP_MAX_MANIFEST_BYTES`、`ONE_CLICK_BACKUP_MAX_POSTGRES_DUMP_BYTES`。

`init` 生成的本地凭证保存在忽略的 `.env.one-click.local`，不会打印密钥；不要提交或
手工分享该文件。脚本拒绝符号链接、非普通文件或非当前用户持有的运行环境文件，并将
权限收紧为 `0600`。`down` 保留一键运行环境的数据卷。`rollback` 停止一键运行环境后，只恢复
本工具此前停止的旧应用容器。旧版 PostgreSQL 和 Keycloak 始终保留，既有数据不会被迁移或删除。
LLM 与 Gildata 凭证是可选的：未配置时产品壳、CATL/Alphabet 身份底座和 worker
心跳仍可运行，但任何实际 AI 操作会失败关闭，不会回退为 mock 研究结果。

LLM 客户端默认限制序列化消息为 1 MiB、响应正文为 2 MiB，可通过
`LLM_MAX_INPUT_BYTES` 和 `LLM_MAX_RESPONSE_BYTES` 设置为 1 字节至 16 MiB 范围内的整数
字节数。按 UTF-8 字节计量；超大输入在调用前拒绝，超大响应在 JSON 解析或修复前拒绝。
响应检查发生在 SDK 接收正文之后，因此不限制网络下载量或已产生的模型费用。
这两项也不代替模型 token 上限和研究任务总费用预算。

每次模型请求（包含重试）还会发送 `max_completion_tokens`，默认 16,384；
`LLM_MAX_COMPLETION_TOKENS` 可配置为 1 至 131,072 的整数。该值限制单次生成，
支持该协议的推理模型通常把推理 token 计入上限；模型自身可能要求更小的上限。
达到上限而截断的输出仍按无效响应拒绝，不会放宽后自动重试。兼容服务必须支持此参数；
参数被拒绝时调用失败，不会自动移除限制。多次调用、输入 token 和费用仍需计入任务预算，
此设置不代表整个研究任务的花费已受控。当前未进行付费 provider 验证。

迁移 `0071` 为只追加的 `ai_runs` 增加可空 `usage` 字段。抽取、证据提议和评估
会在各自操作上下文中记录每次供应商请求的用量，评估中的合规改写也归入同一操作。
供应商返回的 prompt/completion/total token 数必须是非负整数且总和一致；缺失、
不一致或网络失败记为 `unavailable` 和空值，不能作为零费用计算。旧记录的 `usage`
为空，新操作没有外部调用时 attempts 为空；mock 不会虚构供应商用量。
输出被拒绝但已收到有效用量时，失败操作仍保留这些数字。记录不包含提示词或供应商错误正文。
这些数据随原有操作事务保存；取消而不落审计行、事务回滚、进程终止以及尚未接入的调用路径
仍可能遗漏消耗。因此它是操作审计信息，尚不能用作完整账单或任务总费用门禁。

准备 worker 的 claims/protocol/evidence-plan 调用另以 `kind=prepare` 保存用量，关联
Case、准备版本、输入指纹及 Job。此记录在供应商阶段结束后独立提交，后续研究产物被
丢弃或事务失败不会抹掉已记录用量。这里的 success/failed 只描述供应商及草案验证阶段，
准备流程的最终状态仍以 Job 和 preparation 为准。进程在收到响应与审计提交之间退出
仍可能遗漏；没有外部请求时不新增该记录。

创建前的 `/event-research/extract` 以 `kind=event_extract` 记录供应商用量，
关联已认证租户和独立抽取 ID；此时尚无 Case，不会虚构 Case 归属或为了审计创建研究。
审计不存储材料原文或来源链接，失败响应前也提交已报告用量。抽取 ID 尚未与之后用户
确认创建的 Case 串联，仍不能据此给出完整的每 Case 成本。

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

启动后用 `curl -f http://127.0.0.1:8000/ready` 检查数据库与迁移版本是否就绪；`curl http://127.0.0.1:8000/health` 仅检查进程存活，并用已配置的 Bearer
token 读取 `/api/v1/research-runs/worker-status` 检查研究 worker 心跳；资料采集
worker 目前没有独立健康端点，应由 supervisor 检查进程存活并观察采集任务日志。
停止时向两个 worker 发送 `SIGINT`（前台运行可按 Ctrl-C）；资料采集 worker 也会
处理 `SIGTERM`，完成当前 claim 后退出，外部 provider/LLM 调用可能延迟停止。API
按 Uvicorn 的正常停止信号优雅关闭。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `backend/` | FastAPI 账本服务、召回/合规/KPI 引擎与后端测试 |
| `frontend/` | FundClaw 真实事件入口与显式演示、单元和浏览器回归 |
| `docs/evaluation/` | 证据包：数据集清单、金标数据集、门禁报告、一键复现 |
| `docs/evidence-driven-research-report.md` | 技术报告（[PDF 版](docs/evidence-driven-research-report.pdf)） |
| `CONTEXT.md` | 研究上下文：核心词汇表、实现状态、验证体系 |
| `PRODUCT.md` | 产品基线：用户、设计原则、WCAG 2.2 AA |

## 质量保障

- **CI**：`backend-ci`（pytest + 发布门禁）与 `frontend-ci`（tsc + vitest + e2e）
  双流水线，按目录变更触发
- **远端分支保护**：需在托管平台独立核实，本地配置不能证明远端保护状态。
- **浏览器覆盖**：桌面/移动演示交互、隔离后端真实事件入口；旧 Company Research 完整闭环尚待恢复。

## 文档导航

- 想 30 分钟理解系统 → [技术报告](docs/evidence-driven-research-report.md)
- 想改代码 → [CONTEXT.md](CONTEXT.md)（词汇表 + 实现状态）
- 想做产品/设计 → [PRODUCT.md](PRODUCT.md)
- 想验证质量声明 → [docs/evaluation/](docs/evaluation/)（所有数字可复现）

上传原件的 20 MiB 上限在 HTTP 有界读取和底层解析入口共同检查。Pypdf 文本解析
默认最多 1,000 页、累计 2,000,000 个文本字符和 20,000 个段落；超过上限会失败，
不会把截断文本当作完整解析结果。上传流程保留失败原件，供后续人工处理。
这些上限在读取页树、单页文本提取之后检查相应规模，尚不是对解压内存或 CPU 时间
的硬限制；不应将其解释为解析进程隔离已经完成。
