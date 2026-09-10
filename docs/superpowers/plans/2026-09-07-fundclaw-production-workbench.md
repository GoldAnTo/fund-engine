# FundClaw Production Workbench Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent modules and verification-before-completion for every delivery claim. User has authorized the audited direction and its implementation.

**Goal:** Deliver the formal private multi-role workbench, including actual LLM tasks, evidence visualization, lifecycle controls, review and deployable runtime.

**Architecture:** Keep native collection and frozen evidence authorization. Add a persistent professional task DAG, immutable outputs/attempts/reviews and a separately supervised worker. Expose explicit authenticated APIs and bind the formal React workbench to them.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL/SQLite test fixtures, existing OpenAI-compatible client, React/TypeScript/Vite, Node24.

## Ownership

- Root: professional-team models/schema/migration/repository/service/worker/API; formal task/controls/versions UI; transport/form reliability; integration.
- llm_production: `backend/app/ai/client.py`, new LLM policy/attempt modules and tests; plan `2026-09-07-llm-production.md`.
- evidence_ui: `GatewayResearchContent`, `GatewayAssessmentReview`, new evidence path view and tests; preserve authorization controller.
- llm_audit (delivery assignment): deployment assets and old 9-failure repairs, excluding frontend source/package and Gateway business code; plan `2026-09-07-gateway-delivery.md`.

## 1. Persistent professional task foundation

Files: create `backend/app/models/research_team.py`, `backend/app/schemas/v1/research_team.py`, `backend/app/services/research_team.py`, `backend/tests/test_research_team.py`, migration `0075_professional_research_team.py`; register models and append-only tables.

- [x] Add failing tests: initial team has four stable task IDs, industry/finance no mutual dependency, strategy binds both, quality binds all; repeating initialization returns identical IDs; foreign conversation rejected.
- [x] Implement `ResearchTeamService.ensure_team(spec)` in the current transaction. Team and tasks reference immutable run spec. Use unique `(run_spec_id, revision, role)` and task/output provenance checks; no in-memory-only tasks.
- [x] Add tests proving output/event/attempt/review rows reject update/delete and migration upgrade/downgrade installs real DB guards.
- [x] Execute targeted pytest in `APP_ENV=test DATABASE_URL=sqlite://`, then independent PG migration tests.

## 2. Authorized inputs and genuine role execution

Files: create `backend/app/services/research_team_worker.py`, `backend/app/services/research_team_generation.py`, `backend/app/scripts/run_professional_worker.py`, worker tests; connect initial task creation in `research_gateway.py`.

- [x] Test claim/lease dependency selection, concurrent distinct claims, stale claim recovery and cancelled attempt rejection.
- [x] Load current `ResearchGatewayContent` evidence, re-read frozen quotes, bind exact evidence/parent output IDs and hash; close transactions before LLM.
- [x] Call role-specific prompt with strict result validator and capture LLM attempts. Validate citation IDs/text and role-specific checks. Reopen transaction, re-authorize all input refs and fence current lease before immutable output commit.
- [x] Test malicious citation, changed/deauthorized source, length/refusal/provider failure, missing provider and quality blocker. No demo fallback.
- [x] Run two workers against a temporary database to prove industry and finance can independently claim and progress.

## 3. Controls, directed work and review

Files: team service/API/schema tests, `backend/app/api/v1/research_team.py`, `gateway_main.py` route registration.

- [x] Define authenticated team read, directed message, command and review endpoints with mandatory idempotency keys and payload fingerprints.
- [x] Test scope/run/owner authorization; paused tasks not claimed; cancel fences late results; retry preserves successful outputs; directed message creates affected successors with exact dependencies.
- [x] Implement command receipts atomically with actual state transitions; distinguish professional pause from native collection state and coordinate cancellation safely.
- [x] Implement immutable human review pinned to current output set and authenticated principal; stale version rejects with conflict.
- [x] Add safe revision notifications to live event updates; never put raw prompts/quotes/provider errors into SSE.

## 4. Formal frontend integration and transport

Files: `frontend/src/gateway` team contracts/client/controller, `ProfessionalRoleFrame`, `GatewayRoutes`, `GatewayConversationPanel`, team styles/tests.

- [x] Replace planning cards with authenticated real task data, dependency/attempt/usage/output panels and evidence links.
- [x] Route directed follow-ups through explicit team endpoint; add controls and review actions with real pending/success/failure handling.
- [x] Add native run/team revision selection; cancel obsolete requests and reset role/evidence selections when scope or authority changes.
- [x] Add SSE handshake/idle watchdog tests then implementation; preserve sustained heartbeat connections.
- [x] Validate 20,000-character input visibly; distinguish 422/409 from unknown send; preserve idempotency across recoverable navigation/reload without leaking body.
- [x] Run complete frontend tests, typecheck and production build.

## 5. Evidence, LLM and delivery integration

- [x] Integrate evidence_ui outputs with actual professional citations and inherited detail focus behavior.
- [x] Integrate LLM attempt capture and strict per-role budget; review changed legacy JSON behavior through existing AI test suites.
- [x] Integrate container/static same-origin runtime and dedicated role worker; migrate old asset tests to current real support contracts.
- [x] Verify all prior 9 full-suite failures are addressed without test removal/skips hiding errors.

## 6. End-to-end and completion audit

- [ ] Empty/0074 database migration, PostgreSQL concurrency, complete backend regression and relevant security tests.
- [ ] Temporary actual API + workers + actual frontend from initial request through four role outputs, directed follow-up, quality and human review. Offline provider fixture only for deterministic behavior; record provider checks separately.
- [ ] Browser desktop/mobile, evidence path→quote→focus return, stale/failed/blocked flow and historical version navigation.
- [ ] Dependency audit, build, Ruff and diff whitespace check; independent reviews of authorization, worker fences and UI.
- [ ] Update runtime docs and implementation/verification report with each requirement and authoritative evidence. Keep goal active while any required behavior remains unimplemented or unverified.


## 2026-09-09 状态校正

第 1–5 节已实现并有针对性验证，早期计划未同步勾选。最新界面对齐以 `2026-09-09-prototype-fidelity.md` 为准，保留原型的三栏、四角色卡与固定输入框。独立审查纠正了历史版本标签与后续复核误标为历史的问题。

验证记录：此前全量后端 5075 passed / 77 skipped；后续团队历史与 worker 62 passed，范围投影/API 41 passed，LLM 启动协议 199 passed；最新前端 273 passed / 15 files，typecheck 与 build 通过。上述后端批次存在重叠，不合并计数，也不视为最新工作树再次跑完全量后端。

第 6 节仍未全部验收。正式 Google 研究的真实采集已有冻结材料但无准入证据，仍需诊断数据源适配和准入失败；不能用合成证据四角色样例代替这一环节。当前持续目标状态由用户控制，不因为本轮界面交付而标记完成。
