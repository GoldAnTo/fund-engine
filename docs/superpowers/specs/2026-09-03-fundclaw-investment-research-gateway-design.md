# FundClaw Investment Research Gateway Design

**Date:** 2026-09-03
**Status:** Validated design
**Scope:** A web-native, conversation-first investment-research Agent Gateway for Fund Engine. FundClaw is the working product name used here.

## 1. Product decision

FundClaw is Fund Engine's investment-research equivalent of an OpenClaw-style Agent Gateway. It is not a generic chat assistant, an agent marketplace, or a replacement evidence system.

The primary surface is a research conversation where a researcher can start, observe, steer, pause, resume, retry, and extend a research run. A fixed team of AI research roles executes only approved domain capabilities. The existing Fund Engine ledger remains the sole authority for source material, evidence, claims, reviews, historical replay, and publication.

| Decision | Chosen behaviour |
| --- | --- |
| Entry channel | Fund Engine web application only |
| Start policy | Sending a request starts a run immediately, with no pre-start confirmation gate |
| Visibility | The user sees each role's verified actions, evidence references, blockers, and next step in real time |
| Role model | Four fixed investment-research roles; no dynamic agent creation |
| OpenClaw strategy | Borrow Gateway, skill, task, and observability patterns; preserve a future optional OpenClaw runtime adapter behind a Fund Engine interface |
| Product hierarchy | Conversation and agent operations are the primary shell; evidence workbenches are the authoritative artifact views |

## 2. Goals and exclusions

### Goals

1. An authenticated researcher can issue a natural-language request in the web UI and immediately create a durable, auditable research run.
2. The fixed research team is visible without exposing chain-of-thought, hidden prompts, raw sub-agent conversations, secrets, or untrusted tool output.
3. The Gateway reuses automatic-research intake, acquisition, workers, evidence, review, and historical replay rather than creating a second ledger.
4. Tenant, case, actor, source-policy, and historical-cutoff enforcement applies to every conversation, command, event, and artifact read.
5. A stable seam exists for a future sandboxed OpenClaw runtime adapter without making OpenClaw a source of authorization or research facts.

### Exclusions for the first release

- External IM channels, voice, mobile nodes, and messaging-platform pairing.
- General-purpose plugins, arbitrary MCP servers, arbitrary browser access, shell access, user-installed skills, and model-created tools.
- Shared global memory, cross-tenant memory, autonomous long-term memories, dynamic agents, or free-form agent group chat.
- Automatic investment recommendations, orders, valuation publication, or research publication.
- Replacing the existing company-research model engine before its tenant-scoped CompanyResearchRun application layer exists.

## 3. Architecture and interface

~~~text
Web Agent Gateway
  -> authenticated ResearchGateway
  -> Conversation / Intent / immutable RunSpec
  -> fixed Role Orchestrator
  -> existing automatic intake, workers, acquisition, and ledger
  -> append-only role and domain events
  -> tenant-safe SSE projection and workbench deep links
~~~

ResearchGateway is a deep module. Its small external interface hides identity resolution, intent parsing, scope defaulting, idempotency, model invocation, role capability selection, durable job routing, event projection, recovery, and redaction.

~~~text
createConversation() -> ConversationRef
sendMessage(conversation, text, idempotencyKey) -> IntentRef + optional RunRef
readConversation(conversation, afterSequence?) -> ConversationProjection
streamEvents(conversation, afterSequence?) -> ordered safe events
issueCommand(run, command, idempotencyKey) -> CommandReceipt
~~~

The first release supports four message intents:

- Start a new evidence-led research request.
- Ask a grounded question about frozen material already authorized in the current thread.
- Issue a control command: pause, resume, retry, cancel, or request a counter-evidence pass.
- Change scope. This produces a successor intent and run, never a mutation of the original run.

An uncertain company or security identity still creates the conversation and intent immediately. The Scope and Identity role enters a visible blocked state; acquisition cannot begin until the identity is deterministically resolved.

## 4. Authority, data model, and memory

| Record | Responsibility | Core invariant |
| --- | --- | --- |
| research_conversations | One persistent web research thread | Tenant-scoped, owned by a stable human subject, private by default, never an authorization substitute |
| research_messages | Append-only user/system/role-visible messages | A message is control input or explanation, never evidence or a published conclusion |
| research_intents | Structured interpretation of a submitted message | Has parent intent where needed, input hash, idempotency key, and explicit state |
| research_run_specs | Immutable execution contract for a launched intent | Freezes tenant, subject, case/project reference, scope, historical basis/cutoff, source policy, role graph, capability/model manifests, budget, input artifacts, and correlation ID |
| role_runs | Durable state for one fixed role in one run | Uses attempts and worker claim fences; role state is not evidence state |
| role_events | Append-only safe-to-display lifecycle events | Monotonic per run, no raw reasoning or secret material; links artifacts instead of copying content |
| gateway_commands | User-issued control and steering | Has authenticated actor, idempotency key, target hash/version when needed, receipt, and auditable transition |

Existing ResearchRunEvent, JobEvent, acquisition events, evidence records, review records, publication versions, and DomainEvent remain authoritative in their existing domains. Gateway records refer to them and do not overwrite them.

The Gateway compiles a bounded ContextPack for each role turn from only the immutable run specification, permitted messages and compact conversation summary, authorized frozen artifacts available by the run cutoff, and the role's versioned instruction/capability manifest.

The ContextPack manifest and artifact IDs are persisted with the role event. No role receives an unbounded transcript, a shared main session, or data from another tenant, case, or future cutoff. A model cannot write its own persistent memory. Any fact must pass normal source, freeze, locator, admission, and review paths.

## 5. Fixed roles and capability policy

The orchestrator is manager-style. It owns user communication and treats four roles as constrained work units. Roles cannot choose arbitrary peers or tools.

| Role | Purpose | Allowed output | Prohibited action |
| --- | --- | --- | --- |
| Scope and identity | Parse request, resolve company/security/case, identify ambiguity | Identity decision, scope proposal, blocked decision request | Guess identity, expand scope, write evidence or conclusions |
| Sources and evidence | Request approved acquisition, freeze and extract source candidates | Source/evidence candidates, locator references, rejected-source reasons | Direct web/shell outside approved adapters; unauthorized admission |
| Analysis and counter-evidence | Consume authorized material to identify support, alternatives, counter-evidence, and gaps | Candidate claims/relationships, gaps, counter-evidence tasks | Treat candidate as fact, change policy, publish |
| Compilation and checks | Assemble provisional draft and perform lineage/Unknown checks | Draft artifact, validation results, decision requests | Conceal gaps, perform human review, publish |

Each role sees a server-selected versioned capability manifest. Narrow domain commands include request_acquisition, read_frozen_evidence, propose_candidate_claim, propose_counter_evidence_task, and compile_provisional_draft. The server validates tenant, case, role, policy, cutoff, and schema before any command reaches existing services.

## 6. OpenClaw-inspired strategy

| Pattern | FundClaw adaptation |
| --- | --- |
| Gateway and routing | Web-only Gateway first. Authentication fixes tenant, human subject, and accessible case scope before model routing. |
| Session and sub-agent isolation | One conversation/run-local context. Roles receive bounded context packs and return artifacts to the parent orchestrator. |
| Skills, tools, and plugins | Versioned research skill manifests and narrow domain tools. A future plugin is a governed adapter with explicit policy, not a marketplace install. |
| Tasks, cron, and heartbeat | Persistent task/run history now. Case monitoring, announcement triggers, evidence expiry, and reminders are later work. |
| Control UI | Thread rail, activity rail, capability status, decision cards, task history, and safe operational inspection. |

FundClaw rejects shared main sessions, global memory, host execution, arbitrary browser tools, runtime skill installation, generic tenant trust within one Gateway, and raw tool/prompt/reasoning display.

A later OpenClawRuntimeAdapter operates behind the AgentRuntime interface in a single-run sandbox with a short-lived task grant. It may return only structured candidate artifacts and safe events. Fund Engine policy and ledgers remain the authority.

The related repository assessment is [2026-09-03-github-conversational-research-gateway-references.md](../../research/2026-09-03-github-conversational-research-gateway-references.md).

## 7. Conversation-first web experience

### Navigation and routes

~~~text
FundClaw
|- New research
|- Research threads                 (default home)
|- Needs your attention             (decisions, reviews, blocked work)
|- Monitoring                       (later: announcements, expiry, heartbeat)
|- Research archive
|  |- Cases
|  |- Evidence library
|  |- Companies, topics, funds
|  |- Relationship graph and versions
|- Settings
~~~

| Route | Purpose |
| --- | --- |
| / | Agent Gateway default, with the most recent accessible research thread selected |
| /research/:threadId | Persistent conversation and current safe role-event projection |
| /research/:threadId/runs/:runId | One immutable run within a thread |
| /events/new | Compatibility route that opens the new-research composer |
| /automatic-research/:caseId | Compatibility deep link that resolves to the matching thread/run projection |

Review becomes the primary destination for user-required decisions while retaining the frozen-document and human-decision workbench. Existing case, library, graph, company, topic, version, and fund views become deep artifact surfaces reached from the inspector and global search.

### Page anatomy

Desktop uses a stable three-column workspace:

~~~text
left:   New research, threads, needs attention, monitoring, archive
center: Immutable scope card, safe conversation and role-event timeline, fixed composer
right:  Fixed team status, current evidence, blockers/decision cards, artifact deep links
~~~

The center is not a generic chat feed. A role card shows its responsibility, allowed data scope, completed verified action, evidence count and locator links, rejected/missing material with reason, blocker, next step, and budget status. It never shows internal prompts, raw chain-of-thought, private sub-agent messages, secrets, or unredacted tool arguments.

At 768px to 1279px, the thread list and inspector become labelled keyboard-accessible drawers while the timeline remains central. Below 768px, conversation is full screen and Threads, Team, and Evidence are separate panels. Evidence, graph, review, and version workbenches remain full-screen authoritative pages on small screens.

The visual setting stays a quiet warm document-oriented research desk for daytime professional reading. Existing semantic support, counter-evidence, AI-draft, and human-reviewed language remains visible through labels and shapes as well as color. The product does not become a dark neon agent dashboard.

### Interaction rules

- Sending a request immediately produces a visible receipt stating frozen defaults actually used. The receipt does not delay launch.
- A grounded question returns only authorized frozen references, or explicitly says it remains unverified.
- A scope change creates a successor intent and run.
- Pause, resume, retry, cancel, and counter-evidence actions create gateway_commands, not untracked text effects.
- Identity ambiguity, scope expansion, non-standard source use, critical model input, export, and publication use typed decision cards bound to case/version/artifact/policy hashes. Ordinary evidence acquisition does not interrupt the user item by item.

## 8. Authorization, streaming, and recovery

The authenticated bearer remains the only source of tenant identity. The Gateway extends the current actor model with a stable human subject identifier to audit who created a conversation, confirmed identity, or issued a control command. Request bodies, client thread IDs, model output, and session keys cannot establish tenant/case access.

Every read, stream, command, artifact link, and cancellation is tenant- and case-scoped. Conversations are private by default. Collaboration is a future explicit sharing policy.

Role events and permitted domain-event projections receive a monotonically increasing sequence. The browser uses authenticated SSE with after_sequence or Last-Event-ID replay. If a retained window cannot provide contiguous replay, the client fetches a tenant-safe snapshot and resumes from its returned sequence.

The projection is a safe derived read model. It reports role state, artifact references, counts, policy-safe failure categories, and lifecycle events. It does not expose inaccessible source content, secrets, model reasoning, or raw system exceptions.

| Situation | Required behaviour |
| --- | --- |
| Duplicate send/command | Same idempotency key returns original receipt without duplicate work |
| API/worker restart | Existing claim-token and lease recovery continues durable jobs; role projection reconstructs from persisted events |
| Model/transient tool failure | Bounded policy retry; show role, classified failure, retained artifacts, and permitted next action |
| Unsupported/unsafe request | Persist rejected intent with explanation; invoke no unapproved capability |
| Prompt injection in source/message | Treat as untrusted content; it cannot change policy, identity, tools, permissions, or publication state |
| Pause/cancel | Stop pending work through durable commands; preserve prior events, artifacts, and audit records |
| Scope change | Create successor intent/run and keep prior history replayable |

## 9. Delivery plan

### Phase 1: Web Agent Gateway over existing automatic research

1. Add a stable human subject to the actor model, Gateway schema, migrations, repositories, tenant/case authorization, and audit correlation.
2. Implement ResearchGateway commands, safe projection, idempotent SSE replay, fixed role capability manifests, and an AgentRuntime interface.
3. Implement the native role adapter using the current model provider and existing automatic-research intake, acquisition, and research workers. Preserve source and evidence gates.
4. Replace the automatic-research entry path with the Gateway composer. Add thread routes, role-event timeline, team/evidence inspector, and decision cards while preserving deep links.
5. Fix affected accessibility behaviour: labelled compact navigation, keyboard-accessible inspector drawers, focus restoration, and mobile search overflow.

### Phase 2: company research and monitoring

1. Build the tenant-scoped CompanyResearchRun application layer described by the existing company-research workbench design, then route suitable company research intents through it.
2. Add governed monitor, announcement, expiry, and reminder schedules with durable history, source policy, retry, deduplication, and event delivery.

### Phase 3: optional runtime and channels

1. Add a sandboxed OpenClawRuntimeAdapter only after proving least-privilege grants, data-egress controls, and structured output enforcement in security review.
2. Evaluate external channels only after explicit identity mapping, pairing, replay protection, delivery audit, and tenant-isolation design and tests.

## 10. Acceptance criteria

The first release is accepted only if focused tests and a real browser/API/worker run prove all of the following:

1. An authenticated user sends a request and gets one durable thread, intent, frozen run-spec receipt, and queued role timeline.
2. Replaying the same idempotency key, refreshing, reconnecting SSE, and restarting API/workers do not duplicate a run or lose permitted role progress.
3. A real pipeline run emits visible state for all four roles, including a locator-backed evidence reference and a correctly classified blocker or completion state.
4. A displayed evidence link is readable only when tenant, case, and cutoff authorize it. Unauthorized jobs, activity, and artifacts cannot leak through the Gateway.
5. A user scope change produces a successor run and leaves original scope, events, and historical view unchanged.
6. Source text, uploads, and model output cannot bypass source policy, freeze, available-at/cutoff, admission, review, or publication gates.
7. The UI never displays chain-of-thought, internal prompts, secrets, unredacted tool arguments, or unrestricted traces. It remains keyboard-operable at desktop, tablet, and mobile breakpoints.
8. Human review/publish and every other material decision remain explicit hash-bound operations with an auditable actor record. The Gateway never labels an AI draft as human-reviewed or published.

## 11. Related designs

- [GitHub project assessment](../../research/2026-09-03-github-conversational-research-gateway-references.md)
- [Evidence-first company research workbench design](2026-09-03-evidence-first-company-research-workbench-design.md)
- [Governed acquisition/runtime identity design](2026-08-12-governed-acquisition-runtime-identity-design.md)
