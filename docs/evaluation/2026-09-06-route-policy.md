# Legacy route authorization policy, 2026-09-06

Scope: repository-only inspection of the generated route-auth inventory and actual ORM/API/query code. No production database, provider, or credentials were used. Header presence in OpenAPI is an inventory signal, not proof of row-level authorization. FastAPI lazy routing requires inspecting OpenAPI rather than counting `app.routes`.

## Trusted ownership root

Use `require_research_tenant` and `CaseTenantAccess.require_case`. Ownership is `case_tenant_admissions(research_case_id, tenant_id)` with one immutable admission per Case. Neither `created_by`, reviewer strings, provider metadata, content hashes, nor request JSON establishes ownership. Missing/foreign/unadmitted resources return indistinguishable 404; missing credentials return 401, invalid configured credentials 403. Authenticate before database/provider/side-effect work. Tenant roles do not imply cross-tenant access.

Reusable SQL boundary:

```sql
SELECT a.research_case_id
FROM case_tenant_admissions a
WHERE a.research_case_id = :resolved_case_id AND a.tenant_id = :trusted_tenant;
```

Collection queries must join/filter against admitted IDs before ORDER/LIMIT/cursor evaluation. An absent case filter never means all tenants. Missing ownership on historical rows means exclude from collections and deny mutations until explicit administrative admission; do not auto-admit on read.

## Resource to Case mapping

| Resource / old routes | Real relation / required validation |
| --- | --- |
| thesis; `/theses/{id}/rerun`, `/propose`, `/causal-steps`, `/causal-edges` | `theses.research_case_id -> case_tenant_admissions.research_case_id`. Validate source/target causal steps both have the URL thesis ID before publication. Authorize before LLMClient construction, AIRun/Job creation, or provider call. |
| evidence link review | `evidence_links.thesis_id -> theses.id -> research_case_id`. Do not infer ownership through shared source statement/document. |
| assessment review | `ai_assessments.snapshot_id -> evidence_snapshots.id -> thesis_id -> theses.id -> research_case_id`. Check before closing review tasks/reconciling runs. |
| proposal list/claim/decision | `proposals.research_case_id -> admission`. For thesis-targeted kinds validate persisted `target_context.thesis_id -> theses.research_case_id` agrees. Nullable Case without explicitly validated migration remains denied. Do not fall back to arbitrary payload Case strings. |
| document version | `case_document_versions.document_version_id -> document_versions.id`, attachment `research_case_id -> admission`. A document can be attached to multiple Cases and tenants; global content dedup is not ownership. |
| task | `task_items.research_case_id -> admission`; nullable historical tasks denied. If ref_type/ref_id supplied, resolve from a closed known resource map and require same Case, even when both Cases are owned by the tenant. Unknown reference types must not authorize mutations. |
| domain event | No Case FK exists. `aggregate_type/aggregate_id` are typed resource references, while payload is untrusted for access. Resolve known types through the above joins; unknown/malformed/orphaned aggregates excluded. Event IDs, actor strings and secondary refs alone never confer access. |

Proposal evidence publication additionally requires `payload.source_statement_id -> source_statements.source_span_id -> source_spans.document_version_id -> case_document_versions` attached to the resolved target Case. Existing `proposal_evidence_context` validates this only when proposal Case is non-null. Apply ownership before rendering payload/source context, claiming leases, or writing decisions. For modified decisions repeat source/target checks against replacement payload. Keep review source-governance/admission rules independent from tenant checks.

## Shared document caveat and implemented repair

Existing event-scoped document routes authorize Case and attachment. However `detail_for_case` previously called global `detail`, whose citation query included links from all theses using the shared statements. Two tenants sharing one frozen document could see each other's thesis/link IDs. The bounded repair passes Case scope into detail and filters citations to `theses.research_case_id = :case_id`; it preserves the existing reviewed/research-mode filtering. Regression uses two admitted tenants, one document/span/statement, and one distinct thesis/link per tenant; both owners see only their own citation, foreign Case reads return 404 and anonymous reads 401. Global document endpoints remain a separate access-policy follow-up.

For `/documents` require tenant and filter versions through EXISTS attachment/admission; do not duplicate versions joined to multiple owned Cases. For `/documents/{id}`, either require explicit Case context and reuse `detail_for_case`, or return only citations from all admitted Cases owned by the tenant. Never call global detail after only checking that *one* attachment belongs to tenant. Unattached versions are not public by default.

Supplement requests already contain case_id: authorize Case first, then require attachment, then write supplement/contract. Extract accepts only version ID and creates document-scoped candidates; safe rollout needs explicit Case context or rigorously defined shared-write semantics. Prefer a Case-scoped extraction route and preserve source contracts, artifact constraints, and pre-commit guards. Unknown/unattached/shared-write ambiguities fail closed; no extraction to infer ownership.

## Activity/task and global directory distinctions

Activity query currently compares raw aggregate_id with Case ID regardless of aggregate_type. This both misses thesis/proposal/job events and is not an ownership projection. Build a typed SQL ownership projection (or persisted trusted Case field on newly emitted events with backfill rules) before exposing the global feed. Unknown aggregates excluded. Existing UUID-only pagination also disagrees with model's monotonic `seq`; separately fix cursor semantics with tenant filtering applied first, including foreign cursor rejection.

Task create must authorize the requested Case and validate reference consistency before insert. Patch resolves persisted task Case; list scopes all rows including cursor lookup. Never trust assignee as tenant. Proposal/link/assessment writes that close tasks must not mutate a task whose persisted Case conflicts with resolved output ownership.

Companies/stocks/funds/themes/metric catalogs are shared directory/instrument records without direct immutable Case ownership. Joining a stock or company through an arbitrary Case would fabricate ownership. Define explicit authenticated catalog-read and separate host-granted catalog-editor policies; do not give ordinary Case owners global directory writes. Document content is also shared storage, but attached research relations are tenant-scoped and cannot follow catalog-read rules. Underwriting objects/projects use another domain and need their own tenant root; do not infer via research Case.

## Next independent implementation slices

1. **Event workbench review closure:** add resource resolvers for proposal, evidence link, assessment, thesis. Scope `/review-queue` and `/review-proposals` before limits; authorize claim/decisions and all legacy review commands. Reuse admitted Case root. This is the most useful next slice after document citation isolation because existing protected event queue leads into legacy review mutation URLs.
2. **Document commands/library:** Case-scoped supplements/extract and tenant-scoped global library read contract, including nested citations. Preserve provider refusal and artifact semantics.
3. **Engine/causal:** authorize thesis commands and all nested step IDs; verify denied calls create no Job/AIRun/provider request.
4. **Tasks/activity:** task ownership and typed event projection, with pagination tests. Keep unrelated catalog writes and underwriting out of this resolver.

## Existing tests and required cases

Read fixtures `api_client` and command fixtures `cmd_client` already send configured `test-team` credentials. New Cases produced inside tests still need explicit admission using `tests.tenant_admission.admit_case`; adding bearer headers alone does not create ownership. Keep pure repository/domain tests independent of HTTP authorization.

Adapt API tests in `test_document_read_api_v1.py`, `test_review_commands_api.py`, `test_proposal_review_api.py`, `test_engine_commands_api.py`, `test_causal_commands_api.py`, `test_operational_api.py`. Audit integrated callers in `test_event_review_queue.py`, `test_event_research_api.py`, `test_event_source_governance.py`, `test_auto_research_api.py`, `test_gap_fill_api.py`, `test_research_flow_e2e.py`, and `test_cambricon_profitability_case_e2e.py`. Do not silently admit all historical test rows; preserve explicit orphan/unadmitted rejection fixtures.

Every slice needs owner success, foreign 404, anonymous 401, invalid-token 403, missing/orphan 404, collection non-disclosure with and without case filter, denied side-effect counts unchanged, and provider spy never invoked on rejection. Add owner Case + foreign nested resource, mismatched same-tenant Case, malformed persisted reference, shared document citation isolation, replacement proposal payload from foreign source, and cross-tenant cursor cases. Reviewer fields remain descriptive until a host-owned individual principal is introduced; `human:anonymous` must not be represented as authenticated human attribution.

## Implemented review slice and verification

Added `ReviewTenantAccess` with persisted thesis/link/assessment/proposal resolution. The six legacy review operations now require trusted tenant credentials: both queue reads, evidence-link reviews, assessment reviews, proposal claim, and proposal decisions. Queues apply tenant admission and (for proposals) target-thesis consistency predicates in SQL before LIMIT. JSON UUID comparison normalizes hyphens on both SQLite and PostgreSQL without casting arbitrary JSON into UUID. A missing, unadmitted, foreign or inconsistent target cannot reach mutation services. An explicitly requested foreign Case returns 404 rather than an empty filtered response.

Proposal test fixtures now construct actual source/span/statement/thesis/Case graphs and explicitly admit ownership. Separate orphan rejection tests remain. Existing same-tenant but mismatched proposal/target test now expects 404 at the ownership boundary; cross-Case replacement source publication remains a 422 domain refusal with no decision/entity writes.

Also reproduced owner review incorrectly closing another Case's task when that task contained the same resource ref. `close_review_task` accepts an explicit resolved Case filter; assessment/proposal HTTP reviews and event-queue reconciliation pass that filter. Tasks with foreign or NULL Case remain open. Legitimate owner tasks have explicit fixture ownership and still close. The optional parameter preserves compatibility for separate internal callers; document-scoped atomic-claim review/task semantics are outside this slice and should be reviewed separately.

Evidence: initial new HTTP tests failed on unauthorized writes, global queue exposure, and orphan claiming; both task-side-effect regressions failed with foreign task status `done`. After repair, the combined command below passed **125 tests** (14.81 seconds), and scoped `git diff --check` passed. Only existing Starlette TestClient and Pydantic locator warnings were emitted.

```sh
backend/.venv/bin/pytest -q \
  backend/tests/test_review_tenant_access.py \
  backend/tests/test_review_commands_api.py \
  backend/tests/test_proposal_review_api.py \
  backend/tests/test_event_review_queue.py \
  backend/tests/test_event_research_api.py \
  backend/tests/test_operational_api.py \
  backend/tests/test_research_flow_e2e.py
```

No production database or provider was contacted. Authorization tests count EvidenceReview, ReviewDecision, ProposalReviewDecision, ReviewAssignment and DomainEvent rows before/after rejected calls. Owner claim, existing publication/rejection/concurrency/reconciliation tests, foreign/anonymous/invalid credentials, unowned proposals, limit-before-filter regressions, inconsistent target, cross-Case replacement source, and foreign/NULL task side effects are covered.


## 后续 engine / causal 实施

`commands/engine.py` 的 rerun/propose 在初始化模型或创建任务之前，复用 `ReviewTenantAccess.require_thesis`。`commands/causal.py` 的step/edge创建同时要求URL thesis所属Case已admit给可信tenant；edge两端必须属于URL thesis，不能因为同租户就跨thesis连接。anonymous401、invalid403、foreign/missing/unadmitted404，拒绝不会新增AIRun、Job、snapshot、assessment、proposal、causal记录或domain event。

新增 `tests/test_engine_causal_tenant_access.py`；现有异步竞争测试明确admit其Case并向direct函数调用传tenant，未放宽生产认证。实现定向173 passed/3 skipped，mechanism另7 passed/1 skipped。独立合规与质量审查未发现本片缺陷，六文件实际回归69 passed/1 skipped；PG专属竞争尚需补测，不能以跳过作通过。


## 全局文档库读取已实施

`/documents` 与 `/documents/{id}` 要求可信tenant。EXISTS附件关联已准入Case在SQL分页前过滤，并避免同文档附属多个自有Case时重复。显式case_id先校验归属；详情同时校验附件，引用只含自有Case（显式Case时进一步收窄）。全局未附属历史文档拒绝访问，不通过读取自动admit。游标必须对应同一读取范围内的文档与时间，外租户游标404。来源展示规则维持原有状态。

新增三组权限/共享/游标回归先红后绿，原有正向fixture明确建立Case归属。广泛定向185 passed/1 skipped；独立审查24 passed；临时PostgreSQL迁移后同24项passed，未发现数据库方言差异。文档抽取/补充写入策略仍是后续项，不能将本片只读接口权限作为写入已保护的证明。


## 文档写入已实施

`POST /documents/{id}/supplements`要求可信tenant、自有已准入Case及该Case原文附件，授权位于freeze/attach与审计写入之前。`POST /documents/{id}/extract`要求至少一个自有已准入Case附件；可选case_id须自有且已附该文档。共享源抽取的效果限定为document-scoped不可变候选生成/复用，不批准或推进Case。未附属版本404，匿名401，外租户或同租户错Case404，拒绝不构造provider且相关账本数量不变。来源合同门禁保留。8项新增边界测试和合并88 passed/1 skipped验证通过，独立审查无阻塞发现。


## task/activity 已实施

公开tasks与activity/evidence-changes认证后从Case admission解析SQL归属，过滤先于分页。task create要求Case及可选资源引用一致，patch先验证原task归属；无归属任务拒绝。活动支持当前实际emitters的proposal/job/evidence_link/document/material聚合：pending/rejected link使用proposal ID，published link使用实际link；共享文档须事件Case引用和持久附件一致。未知或无法证明归属的事件不公开。proposal target校验与review共用，错误/缺失thesis拒绝。

游标与(created_at,id)排序一致，且必须位于同租户、Case、状态/指派人或事件/actor筛选范围。恶意UUID顺序和相同时间戳测试不漏页。142项相关回归及隔离PostgreSQL32项通过，规格与质量独立审查通过。性能大数据查询计划仍待后续评估，不因SQL已LIMIT宣称查询成本恒定。


## 旧审核队列来源展示补强

`/review-queue` legacy rows 在LIMIT前排除禁止展示/未生效/过期合同；tenant公开调用同时校验CaseDocumentVersion目标附件。`/review-proposals`的受限evidence_link提案仍保留审核编号与版本，但payload/target_context置空、display_withheld=true。历史无合同来源保持兼容。验证87项相关SQLite与15项隔离PostgreSQL通过。该批读取保护不替代旧review-link写命令的来源合同核查。
