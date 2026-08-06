# 自动研究后端能力对照

## 目标

用户确定主题后，系统自动启动一轮研究：自动拆命题、自动找支持/反方证据、识别缺口并迭代、生成临时判断，最后交给人工复核闸门。

## 当前支持矩阵

| 能力 | 后端现状 | 支持级别 |
|---|---|---|
| 研究案例与命题 | `ResearchCase` + `Thesis`，支持支持条件、证伪条件和下一验证事件 | 已支持 |
| 研究运行 | 没有 `ResearchRun` 模型，只有 `AIRun` AI 调用审计记录 | 缺失 |
| 自动拆命题 | 没有自动拆题服务，命题目前依赖用户填写或种子脚本 | 缺失 |
| 正反研究任务 | 没有专门的 `ResearchTask` 模型；`TaskItem` 是通用首页任务队列 | 缺失 |
| 数据接入 | `DocumentService.freeze` 和 ingest 命令，支持研报、公告、新闻、行情、宏观时序 | 已支持 |
| 陈述抽取 | `StatementExtractor` 和 extract 命令，支持按文档提取来源陈述 | 已支持 |
| 证据提议 | `EvidenceProposer` 和 propose 命令，AI 提议进入人工审核，不自动确认 | 已支持 |
| 反方证据 | 现有 propose 没有保证每条命题同时生成支持与反方任务 | 部分雏形 |
| 缺口识别 | `AIAssessment.gaps` 能记录缺口文本，但没有独立缺口检测器 | 部分雏形 |
| 缺口迭代 | 没有根据缺口自动追加下一轮研究任务的编排 | 缺失 |
| 临时判断 | `AIAssessment` 配合 `displayed_as_provisional` | 已支持 |
| 人工闸门 | `EvidenceReview` 和 `ReviewDecision` 支持证据关系、评估两级人工审核 | 已支持 |
| 证据版本与快照 | `DocumentVersion`、`EvidenceSnapshot` 和内容寻址去重 | 已支持 |
| 运行状态 | `Job` 能记录单个 extract/propose/assess 作业进度，但没有用户可见的整轮状态 | 部分雏形 |
| 自动编排 | `run_ai_engine.py` 能顺序执行 extract -> propose -> assess，但属于脚本，不是启动研究后的服务编排 | 缺失 |

## 代码证据

- `backend/app/models/ledger.py`：已有 `ResearchCase`、`Thesis`、`EvidenceLink`、`EvidenceSnapshot`、`AIAssessment`、`ReviewDecision`、`AIRun`。
- `backend/app/models/operational.py`：已有 `Job`、`JobEvent`、`TaskItem`。其中 `TaskItem` 注释明确说明它是首页任务队列摘要，不是研究运行任务模型。
- `backend/app/api/v1/commands/engine.py`：`/{thesis_id}/propose`、`/{document_version_id}/extract`、`/{thesis_id}/rerun` 是独立命令；propose 会创建 Job 并进入人工审核队列。
- `backend/app/api/v1/commands/ingest.py`：数据接入命令调用 ingest 管线，缺少与研究启动事件绑定的后续编排。
- `backend/app/scripts/run_ai_engine.py`：存在顺序执行 extract -> propose -> assess 的端到端脚本，但只对已有案例运行，没有 `ResearchRun` 状态、预算、轮次、缺口追加和用户可查询的运行对象。
- `backend/app/services/jobs.py`：JobService 提供单作业 queued/running/succeeded/failed/cancelled 状态，不能代表整轮研究生命周期。

## 结论

当前后端已经具备“研究账本、文档冻结、陈述抽取、证据提议、AI 临时评估、人工审核”的底层零件，但**尚未支持用户确认主题后自动完成整轮研究**。

目前实际能力更接近：

```text
人工/脚本触发 ingest
-> 人工/脚本触发 extract
-> 人工/脚本对单个 thesis 触发 propose
-> 人工/脚本触发 assess
-> 人工审核
```

目标能力应当是：

```text
启动 ResearchRun
-> 自动拆命题
-> 自动生成 support/contradict/result/alternative 任务
-> 自动并行 ingest/extract/propose
-> 自动检测正反平衡、矛盾和缺口
-> 自动追加下一轮任务
-> 生成 provisional assessment
-> waiting_for_review
-> 人工审核并冻结
```

## 最小实现顺序

1. 新增 `ResearchRun`：关联 `research_case_id`，记录 status、current_stage、round、budget、started_at、finished_at、stop_reason。
2. 新增 `ResearchTask`：关联 run/thesis，明确 `task_type` 为 support、contradict、result、alternative，记录 query、status、evidence_count、gap_reason。
3. 新增启动命令：`POST /research-cases/{case_id}/runs`，创建 ResearchRun 并投递首批自动任务。
4. 新增编排器：顺序或异步调用 thesis generation、ingest、extract、propose、assess，并把每一步挂到 run/job。
5. 新增缺口检测器：检查正反证据数量、结果指标、一手来源、时间覆盖和矛盾组，生成下一轮 ResearchTask。
6. 新增停止规则：最多 3 轮、连续无新增证据停止、预算耗尽停止、重大缺口转人工。
7. 新增运行查询：返回阶段、进度、支持/反方数量、矛盾组、缺口、临时判断和下一动作。
8. 保留人工闸门：AI 只能写 machine_generated/provisional，只有人工 ReviewDecision 才能进入正式结论。
