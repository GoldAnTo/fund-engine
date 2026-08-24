# Underwriting research revision lineage

## Purpose and boundary

The underwriting revision reader makes a previously published research basis
inspectable after later work exists. It is a **read model**, not an automated
research author, valuation engine, or trading system. A response describes
what a selected version actually froze; it never supplements that version from
the current database state.

This makes the reader useful for investment review: an analyst can distinguish
the evidence and unresolved gaps that supported an earlier conclusion from
what was learned later, without treating a newly published narrative as if it
had been known at the earlier cutoff.

## Stored model to reader concepts

| Persisted model | Reader-facing concept | Historical rule |
| --- | --- | --- |
| `uw_research_versions` | a revision, grouped by `object_id + version_kind` | `sequence` and `supersedes_id` are the sole lineage chain; `parent_ids` is its frozen parent set. |
| `uw_historical_bases` | the cutoff and source-manifest boundary | every resolved parent must use the revision's `basis_id`. |
| source, metric, mechanism, industry/model, ledger and answerability version rows | a `parent_ref` | the specific UUID is resolved, verified and described; no natural-key or latest-row lookup is allowed. |
| `semantic_snapshot:<sha256>` plus CATL's parent-set ledger seal | a governed fixture snapshot | the stored seal and content hash must exactly match the stored parent set. |

The generic revision seal binds object, basis, version family and the sorted
parent IDs. Older generic versions are checked against their historical
snapshot reconstructed only from ledger IDs frozen on that revision; a reader
does not query later effective ledger state to make them readable.

## Legacy hash compatibility boundary

The reader preserves each historical hash family without attempting to upgrade
one family into another:

- Pre-parent-set generic rows hash their frozen legacy ledger snapshot
  (object, basis cutoff, source-manifest hash, and selected ledger IDs/content
  hashes), then bind that snapshot hash to the version kind and sorted parent
  IDs. They do not include `price_as_of` in that historical preimage.
- CATL parent-set seal v1/v2 rows hash the stored semantic-snapshot token,
  CATL version kind, and semantic hash of their canonical sealed parent refs.
  V2 additionally seals the CATL answerability parent creation time; v1
  remains readable under its original, less specific seal and cannot be
  upgraded by the reader.
- Generic parent-set v3 rows hash their schema tag, object/basis IDs, stored
  basis cutoff, `price_as_of`, source-manifest hash, version kind, canonical
  parent IDs, and canonical parent descriptors.

New product revisions will dispatch through the separate
`underwriting.research-revision-manifest.v1` contract. That dispatch neither
reinterprets nor backfills a legacy hash: each legacy row is verified only
against its stored basis fields and original seal semantics.

## Archive identity and runtime isolation

A historical family identifies itself from the persisted `uw_research_objects`
row selected by that same immutable family. The history response returns its
controlled object kind, canonical name, and external key only after that row
has been checked; it never substitutes a current security, event, market, or
fixture record.

The browser archive lives under a small archive-only route shell rather than
the event workbench shell. It does not import event polling, worker controls,
fund-disclosure data, mocks, or a current-data client. A deep link therefore
uses the URL only to address the frozen family; all displayed identity is the
checked history response.

## Parent resolution and presentation

Each selected UUID must resolve to exactly one allowed immutable row and match
the revision basis. Object-scoped rows must also match the revision object.
Missing, malformed, duplicate, ambiguous, cross-basis, cross-object or
unverifiable parents are not omitted.

| Parent category | Descriptor fields shown | Presentation rule |
| --- | --- | --- |
| source manifest | identity, content hash, source locators, frozen status | locators come from the stored manifest. |
| metric definition / observation | identity, content hash, locator, unit, period, availability, status | observation identity includes metric, definition version, period, dimensions and source. |
| mechanism | identity, content hash, source locators, status | it is evidence of a candidate/formal mechanism record, not proof of a forecast. |
| industry/model rows | identity, content hash, status | unsealable advanced rows fail closed until a sealed write model exists. |
| ledger entry / unknown gap | identity, content hash, source locator or boundary, unit, effective period, availability, entry type | a recorded `unknown_evidence_gap` remains visible as an unknown; readers do not complete it automatically. |
| answerability | identity, content hash, status | its stored research state is shown, not converted into a trading instruction. |

No arbitrary raw payload is returned. Source locators are traceability anchors,
not a claim that every source was independently revalidated at read time.

## History and diff semantics

The reader provides three GET-only resources:

- a family history by object and `version_kind`;
- one selected revision;
- a diff from a strict ancestor to a strict descendant in the same family.

A diff indexes descriptors by `(artifact_type, semantic identity)`. It emits
only `added`, `removed`, or `replaced` records. Ledger identity is its
ledger-kind/family rather than its row version, so an updated ledger fact is a
replacement rather than a misleading remove/add pair. Entries are sorted by
group (`evidence`, `mechanism`, `industry_model`, `answerability`), then type,
identity, change type and references.

`diff_hash` is the canonical SHA-256 hash of both revision IDs, both stored
revision content hashes and the fully ordered typed change payload. There is
no clock, current head, or unordered query result in the recipe.

## Archive directory and version selection

`GET /api/underwriting/v1/research-archives` is the entry point for a
researcher who knows a company or industry name/key but not an internal UUID.
It lists persisted revision families in stable
`(canonical_name, external_key, object_id, version_kind)` order. `query`
case-fold matches only the stored name/key; `kind` is an exact research-object
kind; `limit` is bounded; and `cursor` is an opaque, versioned continuation
token. A cursor only continues that stable directory ordering. It does not
select a newer interpretation or recover a missing version.

Each directory family is checked using the same frozen revision reader as
detail/history. A `readable` row can show its latest persisted revision ID,
sequence, cutoff and source-manifest hash. An `unreadable` row is deliberately
limited to the research-object identity, family, version count and state: it
does **not** expose an unverified cutoff, source, parent or conclusion.

The archive detail fetches the selected family history and defaults to the
last readable stored revision. A researcher can select any listed sequence;
the selected detail is resolved against that exact ID, not against a current
head. When a selected version has a predecessor, the only comparison rendered
is `predecessor → selected`. The reader never compares it to an arbitrary
newer revision or turns a directory head into a retrospective restatement.

The displayed locator, unit, period, availability and status are the frozen
descriptor fields returned for that version. `unknown_evidence_gap` is shown
as **Unknown evidence gap**, while `candidate` remains a candidate; neither is
silently upgraded into a fact. `not_answerable` and
`wait_for_validation` are displayed as unresolved research boundaries rather
than conclusions.

## Frozen research-boundary semantics

`GET /api/underwriting/v1/research-versions/{revision_id}/boundary` returns
only boundary parents explicitly sealed into that selected revision. Its
identity tuple (`revision_id`, object, basis, version family, content hash,
cutoff and source-manifest hash) is bound to the selected revision before the
browser renders it. The browser rejects a response with an unknown field,
malformed nested record, or any identity mismatch; it does not fall back to a
status label, current ledger row, relation, fixture, or economic-model read.

The optional answerability record states only the recorded answerability,
blockers, research-debt keys, mandate flag and resolution requirements.
Explicit `unknown_evidence_gaps` retain their source locator, unit, observed
period, effective/available times and dimensions. If the selected frozen
parent graph contains no displayable gap, the UI says exactly that. It does
not turn the absence of such a parent into a claim that an industry gap was
resolved. This is important for the CATL evidence-only version: its returned
`not_answerable` record and empty selected gap list do not authorise a
cross-object industry lookup or an inference about later research.

## Reviewed industry evidence candidates

An `industry_evidence_candidate` is a sealed record of bounded evidence, not
a formal mechanism or a model input. Its dossier binds one object, one
historical basis and one frozen source manifest. Each selected source and
locator is checked against that manifest, its availability must be at or before
the basis cutoff, and the recorded authorization/retention boundary remains
part of the source freeze. The selected dossier, source manifest and reviews
are all immutable parents of the candidate revision.

Publication requires two distinct recorded, auditable canonical reviewer
identities in separate roles. The `provenance` review checks source identity, authorization,
retention, locator, availability time and byte/version warnings. The
`methodology` review checks scope, statistical denominator, chart
transcription uncertainty, assumption/Unknown labels and the
anti-splicing declaration. A rejection or request for changes blocks
publication; an amended dossier must receive new reviews.

Approval does not formalize the material. Candidate publication creates only a
`not_answerable` boundary and research debt; it does not create a formal
mechanism, IndustryState, scenario, exposure, earnings engine or forecast.
It therefore makes no statement about actual utilisation, a price mechanism,
valuation, or an investment action. A future formal IndustryState must be
created through a separate governed write path with independently frozen and
reconciled metric definitions/observations, adequate source and denominator
coverage, and causal review of direction, alternative explanations and
falsifiers. No candidate dossier, review, source locator, or approval can be
silently promoted to satisfy those inputs.

## Failure behaviour

The API returns the normal underwriting `422` validation envelope when an
otherwise existing revision has corrupt or unresolvable lineage. It fails
closed instead of returning a plausible partial history. Missing revisions or
families use the normal `404` envelope. A `422` means the historical claim is
not safe to read until the persisted lineage is repaired through a governed
data correction; it does not mean the reader may infer a substitute fact.

The archive UI presents a `404` as a missing frozen archive/version and a
`422` as a condition that cannot safely be read. In both cases it withholds
the detail rather than substituting current data, fixture data, a plausible
parent, or a generated narrative. Transport failures receive the same
withhold-not-replace treatment. Directory rows marked `unreadable` are not
deep links to a partial detail.

## Current limitation and follow-on work

This reader can only compare persisted, typed parent artefacts. It cannot yet
diff free-form research claims, analyst rationale, approval/review status, or
downstream review decisions because those have no corresponding immutable
write-model records. Adding them requires a separate governed write-model
project with explicit schemas, evidence links, change sets and reviewer
workflow—not a reader-side inference.

## Explicit non-goals

This component does not calculate or expose:

- price, return, target price, valuation multiple, or DCF;
- buy, sell, stop-loss, recommendation, position size, portfolio action or
  automatic entry/exit decision;
- automatic completion of missing data, unsupported mechanism inference, or
  conversion of an unknown evidence gap into a fact;
- a revision write endpoint or any mutation through the history/diff API;
- a valuation or action workspace disguised as an archive, including any
  recommendation generated from a directory state, version delta, candidate,
  or unresolved boundary.

## Increment A foundation release evidence (2026-08-25)

The independent-investment-research foundation is released at Alembic head
`0065`. The migration remains additive for legacy rows and enforces a strict
SecurityRightsVersion interval (`effective_to` must be later than
`effective_from`, not equal). The one-click migrate job then runs the explicit,
idempotent bundled fixture loader; ordinary application imports never write
fixture state and the runner owns the commit.

The bundled CATL/Alphabet identity foundation contains two companies, three
securities, three exact `company_has_security` relations and three distinct
rights rows. Its canonical content hash is
`bcf4fe19ec98a28ea249bcb6a8bfbdbb3ac77af9d1d0cca8fb26e8b5d71aca55`.
Both the CATL company and 300750 Security carry the explicit `CATL` alias, so a
fresh `/api/underwriting/v1/product/objects?query=CATL` request returns those
two persisted identities rather than a mock or a current-data fallback.

The fresh Increment A gate produced these results:

- backend: 331 passed, 2 skipped, 1 existing Pydantic field-shadow warning;
  the two skips are the PostgreSQL concurrency variants guarded by
  `TEST_DATABASE_URL` in the workspace-draft and publisher tests;
- frontend: 75 passed across the product shell, setup flow, strict API reader,
  accessibility contract and deterministic foundation helpers;
- TypeScript typecheck, Vite production build and Python `compileall`: passed;
- full repository regression: backend 3,073 passed / 33 skipped / 3 warnings;
  frontend 537 passed across 25 files;
- legacy compatibility: 5 passed; the stored CATL evidence-only response and
  hash replayed deterministically before and after product rows were present.

The restore verifier is schema-first and scans every persisted
`independent_research` revision in deterministic order, failing on the first
schema, lineage, manifest or replay-hash mismatch. A concrete synthetic CATL
foundation publication used by the release run produced revision
`62e2f220-4f98-4616-83f1-fed182458371` and manifest hash
`c71f4bb0919a289e0b253cc23f30e84368032eb8fa64f66e7c1ae8e0cdb43e8c`;
the verifier replayed exactly one of one revision. These two identifiers are
evidence from that test run, not global golden constants: product UUIDs are
part of the canonical manifest, while the automated assertion always compares
the stored hash with the same revision's independently replayed hash.

Docker runtime verification used the disposable Compose project
`codex-task11-20260825`, ports 18000/18080, unique image tags and the isolated
`codex-task11-20260825-db` / `codex-task11-20260825-files` volumes. PostgreSQL,
API, frontend, one research worker and three acquisition workers all became
healthy at revision `0065` with `LLM_API_KEY` and `GILDATA_TOKEN` empty. The
unconfigured acquisition AI boundary permits an idle worker to start but fails
any attempted model call as a provider error; it never supplies mock output.
The existing legacy PostgreSQL container was inspected read-only and remained
at `0062`.

The same disposable runtime completed a custom-format PostgreSQL backup, a
secret-excluding research-file tarball and exact SHA-256 manifest. Restore
validated the three-artifact allowlist, checksums and safe tar members before
Docker mutation, required application services to be stopped, restored into a
random staging database and volume, reran migration plus fixture initialization
and revision replay, and only then activated the restored state. After restart,
all services were healthy, the CATL query still returned two identities and a
file written through the non-root API user matched its pre-backup contents.
