# 公司研究主线整合计划

> For agentic workers: use subagent-driven-development for independent patches and verify the combined result before integration.

**Goal:** 将已有公司研究交付能力及可独立验证的稳定性补丁收敛到 `main`，继续保持旧前端退役状态。

**Architecture:** 以 `main` 的 `a0c37143` 为基底，保留现有租户隔离、LLM 预算与证据约束。提取公司研究交付分支已清理后的后端和纯 API 客户端增量；其他分支按能力移植，不导入其旧数据库迁移链、重复研究入口或历史祖先。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy/Alembic、SQLite/PostgreSQL、TypeScript API 客户端、pytest/Vitest。

## 合并取舍

| 来源 | 本轮采用 | 后续专项整合 |
| --- | --- | --- |
| `codex/company-research-prototype-delivery` (`d5bcbd92`) | 官方资料获取、带引用的 AI 草稿、研究进度、条件财务模型保存/回放/导出、0073/0074 迁移和 API 契约 | 输入人工复核、正式研究 V2 发布和任意公司适配仍按原验收记录保留边界 |
| `codex/ai-company-research-mainline-plan` (`d33b4053`) | worker 与 heartbeat 的 SQLite 临时写锁恢复 | 关键输入确认整链、CATL 适配、新 run 投影；需统一字段、研究状态和迁移编号 |
| Gildata 两个开发分支 | 精确引文定位、占位研报正文过滤、报价身份校验的独立补丁 | 授权治理、正式证据准入整链；需与当前主线语义约束统一 |
| `codex/fundclaw-gateway-p0-plan` | 本轮不引入额外研究入口 | 对话和团队作为公司研究上的协作能力另行整合；其 0071/0072 等迁移与主线冲突 |
| `codex/event-research-mainline` | 本轮保留当前主线事件能力 | 新事件编排作为公司研究的跟踪能力另行整合；其 0055–0062 迁移编号已被主线占用 |

本次选择分批按能力整合。整分支合并会带入重叠入口与迁移冲突；只保存而不整合则继续使公司研究交付能力留在旁支。原分支保留用于追溯，不删除分支、不重写历史，不改动已有运行数据库。

## 执行步骤

- [x] 基线：确认主线和源分支状态；运行现有公司研究 API、市场输入及引擎回归。记录原有 CI 三项失败。
- [x] 公司研究增量：用 `git diff --name-only -z main codex/company-research-prototype-delivery -- backend clients/research` 得到白名单；只恢复该白名单在源分支的最终内容。保留 main 的文档、部署、清理门禁及其他后端代码。
- [x] 独立补丁：按 `d33b4053` 移植 worker/heartbeat 临时锁恢复，测试锁后继续运行、非锁错误继续抛出；Gildata 按 `477d7fc5`、`212b65de`、`38037d65`/`474748b4`/`80022961` 的行为移植，并使用当前主线 fixture 验证引文边界、占位正文和报价主体。禁止覆盖 main 的 LLM client 预算实现。
- [x] CI 根因修复：保持生产归档压缩率保护；修正跨平台测试 fixture；诊断真实 HTTP 启动失败；让运行检查器从当前迁移链取得期望版本，并验证错误版本会失败。
- [x] 合并验证：Python 3.11 干净环境执行 `python -m pytest -q`；执行 `npm ci && npm run typecheck && npm test`（`clients/research`）；重新生成并核对 OpenAPI/TypeScript 契约。
- [x] 数据库验证：隔离 SQLite 和一次性 PostgreSQL 执行完整升级及 0072 → 0074 → 0072 → 0074；验证单一 head 和追加记录约束，运行 `pytest tests -m pg_only -q`。不连接现有业务数据库。
- [x] 边界验证：执行 `backend/scripts/verify_live_event_api.py` 与 `backend/scripts/verify_upload_proxy.py`；检查旧页面目录和客户端页面入口仍不存在。审查合并 diff，确认没有私有运行产物进入新增提交。
- [x] 集成：补全实际检查结果与剩余边界，提交到隔离分支；确认 main 未被其他工作改变后快进合入并正常推送 main。检查该提交的远端 CI；不强制推送，不推送其他历史分支。

## 完成条件

本轮采用的能力在 `main` 上通过合并后的实际验证，远端保存相同提交，工作目录无遗留改动。新前端、知识图谱产品设计、真实 LLM 研究质量和原验收中尚未闭合的研究判断不计为本轮完成项。
