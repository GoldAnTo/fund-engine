# Real successful research example

## Objective

Run a new, private FundClaw conversation through the real Gateway and workers to a native succeeded research run, with currently authorized, traceable evidence and a generated research draft. Do not seed results, rewrite old runs, relax admission or source authorization, or present mocks as execution.

## Candidate and source

Use 贵州茅台 (600519), with three metric names: 营业收入, 归属于上市公司股东的净利润, 经营活动产生的现金流量净额. The live model preflight returned unsupported `2024-H1` for the initial half-year candidate. Do not coerce this to a year or increase period precision. Use calendar-year 2024 instead, verified against the company report summary at https://www.moutaichina.com/mtgf/articleFileDir/2025-04/08/a8931897311b4d4097b7c0b2bf3207d1.pdf, page 4. Any pasted summary must be labeled as user-provided, source-derived material rather than an independently authenticated company disclosure. The unsubmitted half-year input remains a diagnostic record, not a successful example.

## Execution plan

1. Verify a real source and provider response before creating a case.
2. TDD the extraction-contract mismatch: request grounded structured fields and retain supported year/month/day precision; retain the established period-end ledger projection.
3. TDD the duplicate-retrieval chronology mismatch: recognize only a validated content-duplicate binding and retain all other source, timestamp, cutoff and locator checks.
   - Real-model preflight also reproduced wrong character offsets on verbatim quotes. Resolve only an exact unique occurrence within its supplied frozen span, retaining valid bounded offsets for disambiguation and recording reanchoring in the extraction audit. Never fuzzy-match or rewrite a quote. Prompt version `extract-v4` permits null offsets for deterministic anchoring.
   - Independent review found a previously permissive annual-period check: half-year/quarter values could lose their period qualifier. Add regression checks so the metric value must belong to the claimed period, not a different nearby annual mention.
4. Independently review the changes, run SQLite and isolated PostgreSQL regression tests, then reload only scoped idle local services.
5. Submit a fresh conversation through the normal same-origin Gateway with an idempotency key. Observe real worker stages, outcomes and source admission without changing native state directly.
6. Verify native success, currently authorized evidence and draft, citation provenance, and the rendered case. Report the case URL and limitations honestly.
7. Live verification exposed an additional replay bottleneck: every candidate reparses its entire PDF. TDD a bounded deterministic parser-result reuse for the default pypdf replay only. Cache identity must include actual raw content and complete parser/config identity; preserve per-candidate source authorization, raw/span/quote/context hashes and exact locator replay. Custom parsers, mutable results, parser changes and parse errors must not share stale cache entries. Review and regression-test before reloading idle services after the current run ends; do not alter active leases or frozen provenance.

## Safety boundaries

- Work only in the existing codex/fundclaw-gateway-p0-plan worktree; preserve unrelated dirty files.
- Live database: fundclaw_gateway_live_c4b71b84e5b2. Never run tests or truncation against it.
- PostgreSQL verification database: fundclaw_gateway_verify_a6f9b6eee84b; one test process at a time.
- No browser token entry, credentials in artifacts, fake role workers, source-contract bypass, new commercial subscription, or investment/trading action.
- Old Google cases and immutable evidence/audit history remain untouched.

## Verification record

Native execution is now verified complete. Independent content-quality review did **not** pass: this is a real execution-success example, not a trusted financial-verification result. The additional PDF replay performance repair has also passed its separate verification below.

Preflight records:

- Official company 2024 annual-report summary, page 4: revenue 170899152276.34 CNY, attributable net profit 86228146421.62 CNY, operating net cash flow 92463692168.43 CNY. Historical facts, not current quotes or recommendations.
- Existing Gildata `AnnouncementData` returned real 2024 annual-report entries and rounded revenue/profit figures. No external evidence has yet been admitted to a new case.
- SQLite new quote-alignment RED: 5 failed / 5 passed; GREEN and related extraction/runner regressions: 92 passed. The null-offset prompt follow-up RED: 1 failed / 11 passed; GREEN with extraction/AI regressions: 68 passed.
- Initial broader isolated PostgreSQL run: 410 passed, 21 failed. All failures were existing SQLite-only raw SQL tampering/CAS tests being run against PostgreSQL (SQLite `?` placeholders or SQLite-specific fencing expectations). No live database was involved, and those failures are not presented as a passing full suite.
- Subsequent isolated PostgreSQL targeted run excluding those 21 explicitly SQLite-only cases: 259 passed / 21 deselected, before the final metric-period clause-binding refinement. SQLite runs retain those tests.
- Latest final metric-position/period-binding refinement: PostgreSQL period regressions 67 passed / 192 deselected; independent implementation SQLite four-file run 306 passed. The prior broader targeted PG run was 271 passed / 21 SQLite-only deselected.
- Final simplified, explicitly user-supplied annual input passed live provider preflight: three exact source quotes, three correct metric names, `2024` periods, correct finite values and CNY units. These preflight calls used no seeded/live case records and are not the final successful-run proof.

## Live conversation

- Normal same-origin POST returned HTTP 201, with idempotency key `fundclaw-real-moutai-2024-20260905-a1`.
- Conversation: `4001171b-f697-4085-b111-2a49935dee93`.
- Run spec: `f0681328-8416-416c-bb90-ab153553bdb3`.
- Native case: `4779d164-0144-489b-899d-bd4d51b5a11e`.
- Native run: `a8267a1d-488c-4247-a8b0-76036410c9fc`.
- First snapshot: 3 material evidence admissions, external search in progress, authorized execution projection available. This is progress, not yet completion.
- URL: http://localhost:5178/research/4001171b-f697-4085-b111-2a49935dee93
- Independent read-only provenance audit: initial 3 evidence links match all three source-derived 2024 metrics and exact quote slices/hashes. Real AIRun `7f395501-9bf6-4e9b-aacf-c81d55642689` used `MiniMax-M2.7-highspeed / extract-v4`, with no mock model marker. All remain `reported_claim` / `user_supplied`, not external company disclosures; source contracts allow display and AI processing, not export/API.
- Final code regression (SQLite, including the SQLite-specific fencing/tampering cases): six targeted files, 344 passed. Selected changed/new prompt/domain/contract/quote-test files pass Ruff. No claim is made that the entire repository lint/test suite passes.
- Real browser smoke: desktop progress and timestamps update, no page errors, no browser Authorization header or manual token entry. The stream can briefly show reconnecting while it refreshes an authorized snapshot; this is not a statement that all sources have completed.
- Mobile browser smoke at 390 px: no horizontal document overflow, composer remains rendered, no page errors.
- Read-only throughput audit at 14:59:55 CST: 56/96 unique documents successfully extracted; 78 completed extraction operations (70 success, 8 failure), median 21.85 seconds, maximum 60.16 seconds. Failures retain a safe generic error and cannot be diagnosed as a specific upstream error from the database alone. All three original task leases were active; no stuck database lock was observed. The runner processes each document before the job moves from extraction to admission, explaining the long-lived stage label.
- Temporarily increased acquisition processes from the normal one to six for this case's already-planned jobs; request scope, budget, source policies, and admission rules remain unchanged. Extra processes should be gracefully stopped after the case finishes and no other user work is running.
- Read-only audit at 15:25:38 CST: 7,681 external admission decisions were quarantined; all passed temporal and locator gates. 7,323 also failed source authorization because the SSE downloader's strictly derived official `big5.sse.com.cn` mirror is absent from the admission rule. All external candidates independently failed semantic checks; none matched the requested company, 2024 period and three metric names. Fixing the mirror contract alone would not make those candidates valid. No whitelist, frozen history or active run state was changed.
- Snapshot around 15:30 CST: round 1 of at most 3; native external acquisition budget is 100 (not a count of documents). Nine source jobs are partial and the final three are extracting. Three user-supplied evidence links remain admitted. The case is still in progress, not a completed or independently externally verified example.
- First round completed at approximately 15:38 CST. All three actual model assessments succeeded; cash flow was `insufficient_evidence`, revenue and net profit were `supported` with nonempty gaps. Native policy consequently dispatched round 2. The assessment input omits full frozen research context and source-authority metadata, so the model also reports some avoidable scope ambiguity; do not interpret `supported` as independent verification or a human review.
- Independent read-only query audit reproduced nine different planned searches collapsing to one SSE parameter set and one SZSE set: stock code plus a three-year publication window, with no metric keyword/report-type targeting. The target observation year 2024 is not a retrieval constraint. Full-document extraction is also generic; decisions are scoped per job, amplifying counts across repeated materials. Replenishment gaps are written into task text but are not propagated to acquisition requests. These are recorded limitations, not fixed behavior, and no in-flight scope or native status was rewritten.
- At 15:58:18 CST, independent read-only audit found 6 native evidence links, 5 currently mapped/authorized Gateway references. Three additional links were licensed-research reported claims, not primary disclosures. Their exact quote/span/artifact hashes, bindings and all four admission gates passed. One refers to 2024 revenue and agrees with the pasted source-derived value after unit rounding; its `contradicts` role is inherited from the search objective and must not be interpreted as an actual numerical refutation. The other two refer to 2025 and disagree with each other, so neither is proof of the 2024 request. Do not export licensed source bodies or present those conflicts as resolved.
- Fresh frontend regression during the live run: 6 files / 76 tests passed; TypeScript and Vite production build passed. No frontend implementation was changed in this turn.
- Third round uses 9 total acquisition processes for already-planned jobs. The additional eight (instances 2–9) are temporary and must be gracefully stopped after scoped completion.
- Additional read-only performance audit: a pending 96-character quote took 3.53 ms for semantic checks and 1.51 ms for its lineage digest. A profiler around locator replay hit a 12-second diagnostic cutoff while the parser was still processing the 1.64 MB PDF (71 pages reached). Almost all sampled time was in whole-document PDF extraction. This profiling duration includes overhead/CPU contention and is not a precise unprofiled worker latency. No gate evaluation or DB writes were performed by this diagnostic.

## Verified native completion

- Completed at `2026-09-05T08:18:10.272001Z` (16:18:10 CST), about 91 minutes after creation.
- Native status `succeeded`, stage `complete`, stop reason `automatic_completed`, round 3. No status, budget, scope or immutable evidence was edited to produce this outcome.
- Thirty source jobs all ended `partial`; this is not a claim that all source checks succeeded. Seven evidence links, nine real model assessments and one system-generated draft were recorded.
- Current authorized draft reference: `f9e324cd-e11b-5d7b-9c1e-51a5a1f3279f`. Gateway sequence 4161 exposes seven evidence references and this draft reference; the four engine execution stages are completed, not four separately implemented professional analyst agents.
- Independent read-only final audit verified draft/run/case/scope binding, deterministic reconstruction, all seven currently authorized links, four active source contracts with AI/display permission, and nine matching successful `MiniMax-M2.7-highspeed / assess-v1` audit rows. Export/API permissions on licensed sources were not bypassed.
- Final desktop (1440 px) and mobile (390 px) browser smoke show 30/30 ended tasks, a 91-minute duration, no page errors, no horizontal overflow and no browser Authorization header/manual token input. Screenshots are in `/tmp/fundclaw-real-case.azbZcl/completed-desktop.png` and `completed-mobile.png`. A snapshot can still show the reconnecting indicator; no uninterrupted-connection claim is made.
- Eight temporary acquisition processes (instances 2–9) were identity/cwd checked and gracefully stopped after read-only checks found no active runs or acquisition jobs. Normal API/research/acquisition services remain running.

### Content-quality review: not passed

- The report retains a `contradicts` link whose 2024 revenue amount actually agrees with the pasted number after unit conversion and rounding. Search objective is being mistaken for a substantive evidence relationship.
- The revenue assessment mixes 2025 contextual figures into the explicit 2024 question. The assessment input contains the short metric thesis but omits the frozen research question/year.
- Net-profit and cash-flow assessments each rely only on one user-supplied statement but describe automatic admission as certification/review. Admission is not independent financial verification; the assessment input also omits source-authority metadata.
- Therefore, this example demonstrates real execution and authorized draft generation only. It must not be described as a trusted end-to-end financial research success, independent company-disclosure verification, or a human-reviewed report. The current webpage exposes safe artifact references, not a complete report reader.

## PDF replay performance repair: verified

- Added `backend/app/services/pdf_replay_cache.py`: default-only deterministic complete PDF parse reuse, bounded by 16 entries and 32 MiB serialized payload. Actual raw-byte SHA, declared document SHA, parser implementation identity and exact parser/package/config stamps form the key. Injected/custom/configured parsers bypass reuse. Only immutable serialized parse results are retained; each cache hit returns fresh objects. Source authorization and every existing locator/raw/span/quote/context check still execute.
- TDD RED: 3 failed / 6 passed before implementation, with repeated real parser calls exceeding the expected single full-document replay. Expanded GREEN: 24 passed, including fake declared SHA, warm-cache tampering, parser/config changes, permission withdrawal, exception retry, object mutation, eviction and concurrent cold misses.
- Independent quality/security review: approved, no P1/P2 findings. Root final SQLite seven-file regression: 368 passed. Isolated PostgreSQL six-file regression: 107 passed / 2 explicitly SQLite-only permission-tamper fixture tests skipped. Those skipped tests run in the passing SQLite suite. Existing dependency warnings remain; no claim is made that the entire repository suite or lint passes.
- After the final import-only ordering change, root re-ran the new 24 tests; all passed. New module/test Ruff and scoped `git diff --check` passed.
- Real read-only benchmark on this case's authorized-processing PDF: 1,639,516 raw bytes and 194 parsed spans; fresh cache parse 8.751752 seconds, subsequent cache hit 0.002811 seconds; full parse outputs were equal. No licensed source body was exported. This measures parser reuse, not an end-to-end research latency guarantee.
- After confirming no active native runs or acquisition jobs, the three normal local services were identity/cwd checked, gracefully stopped and relaunched with the same scoped live database, existing provider configuration and same-origin identity. No run was restarted and no immutable result was changed.
- Post-reload same-origin reads returned HTTP 200 in 1.849 and 1.161 seconds, preserving native `succeeded / complete`, Gateway sequence 4161, exactly seven authorized evidence references and the same draft reference. Acquisition service heartbeat is online. These are observed read timings, not an SLA.
