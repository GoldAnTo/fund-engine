# Plan B · 主题研究（横切主题）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端 `/topics` 占位入口接上真实闭环：案例级主题标签（append-only 事件表 + 受控词汇 + 标签命令 API），主题中心聚合读模型（ThemeView），前端主题列表与主题视图两页。

**Spec:** [2026-08-02-theme-company-research.md](../specs/2026-08-02-theme-company-research.md)（§4.1、§4.4、§4.5、§5、§6、§7）。

**Architecture:** 主题标签不落 `research_cases` 列（该表 append-only，UPDATE 被 guard 拒绝），改为新建 append-only 事件表 `case_theme_tag_events`（op = add/remove），有效标签由事件折叠派生——与账本"只追加、可审计"原则一致。主题读模型是纯投影：不新建 Theme 聚合根，不存储主题级结论，所有聚合字段携带 `derived_from` 引用列表。

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, React 18, TypeScript, Vitest, Playwright。

**Depends on:** Plan A（`company_roles` 复用 `queries/companies.py` 的角色组装与过滤逻辑）。

---

## Scope

Included:

- `case_theme_tag_events` 模型 + Alembic 迁移 0007（PG 同步，append-only trigger 命名沿用既有约定）。
- `PATCH /api/v1/research-cases/{case_id}/theme-tags` 命令 API：diff 当前有效标签 → 追加 add/remove 事件；标签不在受控词汇 → 422；受控词汇为代码内显式 frozenset（版本控制、可评审），初值覆盖现有金标案例主题。
- `GET /api/v1/themes` 与 `GET /api/v1/themes/{tag}?cutoff=`（ThemeView）。
- 前端 `ThemeListPage` / `ThemeViewPage`，替换 `/topics` 占位路由。

Not included:

- 主题级 AIAssessment / ReviewDecision / 总结论（永不做，见 spec 非目标）。
- 案例创建向导中的标签选择 UI（标签先经 API 管理，UI 入口后续补）。
- CausalStep/CausalEdge 命令 API。

## File map

```text
backend/app/
  models/ledger.py                    # + CaseThemeTagEvent（append-only）
  repositories/research.py            # + add_theme_tag_event / theme_tag_events_for_case(s)
  services/themes.py                  # 新增：受控词汇 + diff/追加逻辑（ValidationError → 422）
  schemas/v1/themes.py                # 新增：UpdateThemeTagsRequest + ThemeView 线网 DTO
  api/v1/commands/themes.py           # 新增：PATCH theme-tags（tag theme-commands-v1）
  queries/themes.py                   # 新增：ThemeReadQueries（list + view 组装）
  api/v1/themes.py                    # 新增：读路由 themes-v1
  api/v1/router.py                    # 注册 themes 读写路由
backend/alembic/versions/
  0007_case_theme_tag_events.py       # PG 迁移 + append-only trigger
backend/tests/
  test_theme_tags_command_api.py      # 新增：PATCH 语义、422、事件追加不覆盖
  test_theme_read_api_v1.py           # 新增：列表聚合、ThemeView 三段、cutoff、derived_from 完整性
frontend/src/
  domain/types.ts                     # + ThemeListItem, ThemeView 等领域类型
  data/researchClient.ts              # + listThemes / getThemeView
  data/mockResearchAdapter.ts         # + 主题场景（含空主题、跨案例冲突）
  data/httpResearchAdapter.ts         # + 映射
  pages/ThemeListPage.tsx             # 新增
  pages/ThemeViewPage.tsx             # 新增：案例×命题状态矩阵、公司×角色表、基金暴露构成
  main.tsx                            # /topics、/topics/:tag 路由替换占位
```

## 读模型语义（实现时不得偏离）

- 有效标签 = 按 `created_at` 折叠事件：add 入集、remove 出集；同一 (case, tag) 重复 add 幂等（命令层先 diff，不产生无意义事件）。
- `GET /themes`：从全部有效标签聚合（tag、case 数、公司数（去重 ThemeRole.company_id）、命题数）；空标签不出现。
- `GET /themes/{tag}?cutoff=`：
  - `cases`：参与案例（`created_at ≤ cutoff`），各案例命题有效状态计数（supported / contradicted / insufficient_evidence / ai_pending）；有效状态派生与 Plan A 公司页同源；**不合成主题级结论**；
  - `company_roles`：主题内全部案例的 ThemeRole 按公司归组，过滤口径（applicable + cutoff）与 Plan A 完全一致，每行带 case / role / 适用期间 / 来源 statement 回链；
  - `fund_exposure`：主题内已映射股票 ∪ 的披露聚合（latest per (fund, stock)，published_at ≤ cutoff），返回构成明细（基金 × 股票 × 权重 × 报告期），不单给一个数字；
  - 顶层 `derived_from`：case_ids / thesis_ids / theme_role_ids / disclosure_ids 全量引用列表。
- cutoff 语义 = 系统账本时间（spec §4.5），页面不得暗示市场时间回放。

## Tasks

- [ ] **Task 1 · 模型与迁移**
  `CaseThemeTagEvent`（id / research_case_id FK / tag / op / created_at）；Alembic 0007：建表 + PG append-only trigger（命名沿用 `no_update_*` / `no_delete_*` 约定）；SQLite 测试走 `Base.metadata.create_all` 自动覆盖。

- [ ] **Task 2 · 标签命令 API**
  `services/themes.py`：`THEME_TAG_VOCABULARY` frozenset + `apply_theme_tags(case, desired)`（diff → add/remove 事件，未收录标签 → 422 并列出合法值）；`PATCH /research-cases/{case_id}/theme-tags`（case 不存在 → 404；body `tags: list[str]`）。响应返回变更后有效标签与追加事件数。

- [ ] **Task 3 · 主题读模型**
  `schemas/v1/themes.py` + `queries/themes.py` + `api/v1/themes.py`，按上述语义组装；注册路由。

- [ ] **Task 4 · 后端测试**
  命令测试（cmd_* fixtures）：PATCH diff 正确、重复 PATCH 幂等零事件、非法标签 422、case 404、事件只增不改。读测试：两案例共享标签聚合正确、空标签不出现、cutoff 后案例/角色/披露不可见、`derived_from` 覆盖全部聚合行、有效状态计数与案例侧 dossier 一致。

- [ ] **Task 5 · 前端两页**
  `ThemeListPage`：主题表格（标签/案例数/公司数/命题状态摘要）；`ThemeViewPage`：顶部固定"聚合投影、非主题级结论"提示；案例×命题状态矩阵（颜色+文字双编码）、公司×角色表（复用 Plan A 角色行组件）、基金暴露构成表（可展开 `derived_from` 明细）；空主题/历史回放/后端不可用状态齐全。

- [ ] **Task 6 · 前端测试与契约**
  Mock/HTTP 适配器扩展 + 契约测试；vitest + Playwright（mock 只读双模式）；重新生成 `contracts/v1.ts`。

- [ ] **Task 7 · 质量闭环与文档**
  后端 pytest + 发布门禁不回退；前端 tsc/vitest/e2e 全绿；更新 CONTEXT.md 实现状态与 `docs/integration/frontend-api-binding.md`；spec §4.1 已按"事件表"实现回填修正。

## 停止条件

- 主题视图出现任何不来自案例层有效状态派生的数字或结论，则 plan 未完成。
- 标签事件表出现 UPDATE/DELETE（而非 add/remove 事件追加），则 plan 未完成。
