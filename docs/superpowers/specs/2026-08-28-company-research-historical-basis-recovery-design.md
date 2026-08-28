# Company Research Historical Basis Recovery Design

**Date:** 2026-08-28

**Status:** Proposed

**Scope:** Company-research initialization, model preparation, retry recovery, and the bounded Alphabet fixture adapter

## Summary

Company research currently uses the effective time of an `InvestmentMandate` as the preparation cutoff. That is the wrong authority: a mandate describes investment constraints and lifecycle, while `HistoricalBasis` is the product contract that freezes evidence, parser, definition, and market-data time boundaries.

This mismatch leaves the current Alphabet project blocked at `30% · model_bundle`. Its reviewed evidence is frozen at `2026-08-25T23:59:59Z`, while its mandate was created with an effective time of `2026-08-28T04:08:33.490000Z`. Model completion therefore fails with `company research preparation cutoff does not match reviewed evidence`.

The fix will make `HistoricalBasis.cutoff` the sole preparation cutoff, create and bind a basis during every new initialization, and add a fail-closed, idempotent recovery for legacy projects that have no basis. The existing project and all seven evidence-review decisions will be preserved in place.

## Goals

- Make `HistoricalBasis` the only authority for evidence and market cutoff validation.
- Prevent new company-research projects from being initialized without a bound basis.
- Normalize Alphabet preview and initialization to the exact cutoff supported by its authenticated frozen fixture.
- Recover the current legacy project without replacing the project, draft, evidence artifacts, or review decisions.
- Keep retries safe under duplicate requests and concurrent workers.
- Surface a useful validation message while retaining a stable machine-readable error code.

## Non-goals

- Do not rewrite an existing mandate's effective time.
- Do not rewrite the cutoff or contents of reviewed evidence.
- Do not silently repair a project whose existing basis conflicts with reviewed evidence.
- Do not turn the Alphabet fixture into a live or arbitrary-date data provider.
- Do not change publication, valuation, or judgment-review semantics beyond restoring the preparation pipeline.
- Do not change the public request or response schema; no generated OpenAPI artifact update is required.

## Current Failure

The current flow creates a project, mandate, scope, agenda, and workspace draft. It leaves `WorkspaceDraftContent.historical_basis_id` empty and sets the mandate's `effective_at` to the requested preview cutoff. Evidence compilation independently uses the Alphabet golden fixture cutoff.

At model completion, `CompanyResearchRepository.validate_workspace_market_boundary()` loads the draft's mandate and treats `mandate.effective_at` as the preparation cutoff. When the frontend requested a preview using the current clock, the two clocks diverged:

| Boundary | Current value |
|---|---|
| Reviewed evidence cutoff | `2026-08-25T23:59:59Z` |
| Mandate effective time | `2026-08-28T04:08:33.490000Z` |
| Draft historical basis | missing |

The worker correctly refuses to commit a model bundle with inconsistent inputs, but it reports only the generic code `validation_failed`, so the actionable cause is hidden from the UI.

## Source-of-truth Contract

For company research, the boundary contract is:

1. `WorkspaceDraftContent.historical_basis_id` must reference a product `HistoricalBasis`.
2. `HistoricalBasis.cutoff` must exactly equal the cutoff authenticated by the current reviewed evidence artifact.
3. Every price, FX, capital-structure, and security-rights snapshot bound to the draft must be valid at that same cutoff.
4. `HistoricalBasis.source_manifest_hash` must authenticate the source fixture used to compile the evidence.
5. `HistoricalBasis.definition_bundle_hash` and `parser_bundle_hash` must identify the exact versioned model-definition and parser contracts used by the adapter.
6. `InvestmentMandate.effective_at` remains mandate lifecycle metadata and must never be used as a fallback cutoff.

Missing or conflicting boundary data fails closed. The system only performs automatic legacy recovery when the basis is absent; an existing mismatched basis remains a validation error requiring explicit investigation.

## Alphabet Cutoff Resolution

The bundled Alphabet adapter supports one authenticated snapshot, not arbitrary historical instants. It will expose one cutoff-resolution operation shared by preview, initialization, evidence compilation, governed model inputs, and recovery.

- If the requested cutoff is equal to or later than the fixture cutoff, the effective preview cutoff is normalized to the fixture cutoff.
- If the requested cutoff is earlier than the fixture cutoff, preview fails validation. Serving the fixture would otherwise introduce information that was not available at the requested time.
- The normalized cutoff participates in the preview payload and input hash. Initialization recomputes the same normalized preview and still requires the caller's preview hash to match.
- Evidence compilation and governed model inputs continue to require the exact effective fixture cutoff.

This preserves the current frontend flow: it may request `new Date().toISOString()`, but initialization receives the authenticated cutoff returned by preview.

## Basis Fingerprints

Basis construction will be centralized in an adapter-owned boundary descriptor so initialization and legacy recovery cannot compute different contracts.

- `source_manifest_hash`: the verified `AlphabetGoldenCaseFixture.content_hash`.
- `definition_bundle_hash`: `canonical_hash` of a payload with schema `company-research.definition-bundle.v1` and the complete typed `CompanyResearchModelTemplate` returned by the adapter, including its `template_version`, identities, modules, classifications, driver bindings, baseline requirements, and scenario mechanisms.
- `parser_bundle_hash`: `canonical_hash` of a payload with schema `company-research.parser-bundle.v1`, parser strategy `alphabet-golden-case-parser.v1`, and the ordered supported fixture schema versions for manifest, business map, source facts, market inputs, and strategy assumptions.

The descriptor returns a `ProductHistoricalBasisInput`. Both hashes are deterministic lower-case SHA-256 values and change whenever the governed definition or parser contract changes. Fixture data changes affect `source_manifest_hash`, not the parser fingerprint.

## New-project Initialization

Initialization remains caller-transactional and keeps its existing same-company serialization and idempotency behavior. Inside the existing nested transaction it will:

1. Recompute the normalized preview and validate `preview_hash`.
2. Create the research project.
3. Create a product HistoricalBasis from the adapter boundary descriptor.
4. Create the mandate, scope, and agenda.
5. Create the workspace draft with `historical_basis_id` set to the new basis ID.
6. Create the preparation, job, and initialized event.

The mandate effective time will be the initialization clock, not an evidence cutoff. No downstream boundary check may depend on it. Existing project mandates are left unchanged.

Idempotent reads of a previously initialized result will also require and return a valid bound basis; an incomplete historical result is handled by the legacy recovery path rather than being treated as a healthy new initialization.

## Model-boundary Validation

`validate_workspace_market_boundary()` will load the basis identified by the draft instead of the mandate:

1. Lock or read the current draft using the existing draft identity and lock-version checks.
2. Require `historical_basis_id`.
3. Load the basis through the product repository and require it to belong to the product HistoricalBasis schema.
4. Compare `basis.cutoff` exactly with the reviewed evidence cutoff.
5. Validate draft market references and model-bundle snapshot bindings against the basis cutoff.
6. Return the basis cutoff to model completion.

There is no compatibility fallback to `mandate.effective_at`. Tests that protect against substituting a newer mandate for a basis remain valid.

## Legacy Recovery on Retry

The existing retry endpoint will transparently run a recovery guard before requeueing a blocked `model_bundle` preparation. Recovery is permitted only when all of the following are true:

- The preparation belongs to a supported Alphabet company project.
- The preparation is a recoverable `model_bundle` failure with error code `validation_failed`.
- The current workspace draft exists and has no `historical_basis_id`.
- The current evidence index and research-gaps artifacts exist and are the versions referenced by the preparation.
- Every evidence fact has a terminal review decision (`confirmed` or `rejected`).
- The evidence cutoff exactly equals the authenticated Alphabet fixture cutoff.
- The evidence source manifest and project identities match the adapter boundary descriptor.
- The draft still contains its existing mandate, scope, agenda, and market snapshot references.

Within the retry transaction, the service will lock the preparation and draft, re-check every condition, create or resolve the exact HistoricalBasis, and compare-and-swap only `historical_basis_id` using the current draft lock version. All other draft fields are preserved. It then appends a `historical_basis_recovered` lifecycle event containing the basis ID, cutoff, source manifest hash, and prior draft lock version before using the existing retry transition.

Recovery is idempotent:

- If a duplicate retry observes the exact basis already bound, it proceeds without creating or rebinding anything.
- An exact existing product basis may be reused by content hash; otherwise one is created once while the locked draft is still missing a basis.
- If another request binds a different basis or advances the draft, compare-and-swap or the re-check fails; the service does not overwrite it.

The recovery changes only the draft version and lifecycle events. It does not create successor evidence artifacts, mutate fact payloads, reset review decisions, replace the research-gaps artifact, or create a new project/preparation.

## Error Reporting

Worker failure persistence will retain the stable `validation_failed` code and store a bounded, non-sensitive validation message in the job error payload. Status projection may show that message as diagnostic detail while continuing to drive behavior from the code and persisted preparation state.

Boundary failures use distinct messages for missing basis, invalid basis, basis/evidence cutoff mismatch, and stale draft. These messages aid diagnosis but are not new public enum values.

## Transaction and Concurrency Rules

- Initialization basis creation and draft binding are atomic with the rest of initialization.
- Retry recovery occurs in the same caller-owned transaction as requeueing.
- Preparation and draft rows are locked before recovery eligibility is finalized.
- Draft updates use the existing optimistic lock version; no direct JSON mutation is allowed.
- Worker model completion retains its existing claim-token, request-hash, strategy-version, artifact-version, and draft-lock checks.
- A failed recovery leaves the preparation blocked and preserves all reviewed artifacts.

## Verification Strategy

Tests will be written before implementation and will cover:

### Cutoff resolution

- A preview requested after the fixture cutoff returns the fixture cutoff and a stable input hash.
- A preview requested exactly at the fixture cutoff is unchanged.
- A preview requested before the fixture cutoff is rejected.
- Initialization recomputation accepts only the normalized preview hash and cutoff.

### Initialization

- A new project creates a HistoricalBasis and binds it in the initial draft.
- The basis contains the expected source, definition, parser, schema, and content hashes.
- The mandate effective time may differ from the basis cutoff without breaking evidence or model preparation.
- Initialization rollback leaves no orphan basis.
- Idempotent initialization returns the same bound basis.

### Boundary validation

- Model completion reads the cutoff from HistoricalBasis.
- Missing basis, unknown basis, mismatched basis cutoff, and mismatched market bindings fail closed.
- A newer or older mandate cannot substitute for the basis.

### Legacy recovery

- A fixture matching the current production shape—blocked at `model_bundle`, missing basis, seven reviewed facts with six confirmed and one rejected—recovers in place.
- Project ID, preparation ID, evidence/research-gaps artifact IDs and content hashes, fact order, and all seven review decisions remain unchanged.
- The draft retains mandate, scope, agenda, user focus, and all market snapshot references; only basis ID and lock version change.
- Duplicate retry is idempotent and concurrent retry cannot create a conflicting binding.
- Recovery rejects unreviewed evidence, the wrong cutoff, wrong identities, wrong source manifest, a stale draft, and an already-bound mismatched basis.

### End-to-end state

- After recovery, the current model job advances from blocked `30% · model_bundle` through model construction to `awaiting_judgment_review` at `85%`.
- The workbench shows the judgment-review state and still shows the same seven evidence decisions.
- Focused backend, frontend API/view/workbench tests, type checking, and production build pass.

## Rollout and Current-project Restoration

After implementation verification:

1. Integrate the fix into the active workspace and restart the local service.
2. Snapshot the current project, preparation, draft, artifact IDs/hashes, and seven decisions.
3. Invoke the existing retry action once for project `19a046e8-2f48-4e1e-949d-cdd84a66bb5e`.
4. Verify that a basis is bound and the snapshot invariants are unchanged except for draft lock version and recovery/retry lifecycle events.
5. Let the existing worker finish the model bundle.
6. Verify status reaches `awaiting_judgment_review` at `85%` and the UI remains interactive.

If any recovery precondition fails, stop without rewriting data and report the specific mismatch. The existing blocked project remains recoverable because no diagnostic action taken so far has committed database changes.
