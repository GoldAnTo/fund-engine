# Immutable Research Revisions and Diffs — Design

## Decision

Research is an append-only sequence of named conclusions, not an editable
document. A reader must be able to select any historical revision and answer:

1. what was believed at that time;
2. which evidence, mechanism, model input, or reviewer decision changed it;
3. whether the new revision superseded, challenged, retired, or merely added
   to the previous conclusion; and
4. which downstream conclusions are now stale.

The system must never overwrite a published research conclusion, source
interpretation, mechanism, model input, or reviewer decision. Corrections and
updates append a new revision that points to the exact predecessor(s).

This is a research-governance design only. It does not calculate price,
valuation, target price, expected return, or an investment action.

## Why a separate revision model is needed

The existing append-only domain rows preserve individual source, observation,
mechanism, industry-state, and earnings-engine versions. That is necessary but
not sufficient for an investor: it does not yet make a *research conclusion*
and its material changes legible as one revision chain.

Making a mutable "latest research" record would lose the distinction between
new evidence, a changed interpretation of the same evidence, and a correction
of an earlier error. Making each underlying row a separate UI event would bury
the actual thesis change in implementation detail. The revision layer therefore
groups frozen domain parents into an immutable, reviewable research snapshot,
while preserving every parent identity.

## Alternatives considered

### A. Mutable research document with an audit log

This is simple to render, but the current text can diverge from the historical
inputs and an audit diff cannot reliably reconstruct the prior model. Reject.

### B. Recompute differences from all current domain rows at read time

This avoids a new table, but produces unstable output when schemas evolve and
cannot distinguish an intentional conclusion revision from incidental source
refreshes. Reject.

### C. Immutable research revision plus material change-set (recommended)

Each publication freezes an ordered parent set and a canonical claim payload.
When it supersedes a prior revision, the command computes and stores a typed
change-set against that exact predecessor. The stored change-set makes the
investor-facing explanation stable; a verifier can recompute it from both
immutable revision payloads. Adopt.

## Core model

### ResearchRevision

A `ResearchRevision` is a single published or draft research view of one
`research_object_id`, one `research_family_key`, and one historical basis.
It contains:

- immutable `id`, `created_at`, `created_by`, `sequence`, and `content_hash`;
- `status`: `draft`, `published`, `superseded`, or `retired`;
- `as_of_cutoff` and `source_manifest_hash` copied from its historical basis;
- `revision_kind`: `initial`, `evidence_update`, `interpretation_update`,
  `model_update`, `correction`, or `retirement`;
- canonical `claim_payload`, containing only sourceable research statements,
  uncertainty, open questions, and explicitly named key factors;
- ordered immutable `parent_refs` to the exact source manifests, observations,
  mechanisms, industry states, scenarios, company exposures, earnings engines,
  falsifiers, and review decisions used by the revision;
- `supersedes_id` for the prior effective revision in the same family; and
- optional `reason_for_revision`, which describes the change without claiming
  unsupported causality.

`published` is immutable. A later revision may mark it no longer effective in
the family, but must not alter its own payload or state row. `retired` is a new
revision whose claim payload explains the withdrawal and remains linked to its
predecessor.

### RevisionChangeSet

One `RevisionChangeSet` belongs to a revision that has `supersedes_id`. It is
not free-form audit prose. It contains canonical, typed entries:

- `evidence_added`, `evidence_removed`, `evidence_replaced`;
- `observation_changed` (old and new frozen observation ID/value/unit/period);
- `mechanism_status_changed` or `mechanism_mapping_changed`;
- `assumption_changed`, `model_input_changed`, or `model_output_changed`;
- `claim_added`, `claim_removed`, `claim_strength_changed`, and
  `uncertainty_changed`;
- `falsifier_triggered`, `falsifier_cleared`, and `open_question_changed`; and
- `correction`, with a mandatory error category and replacement parent.

Every entry carries `before_ref` and/or `after_ref`; a claim-only entry carries
a canonical before/after claim fragment. Numeric values include unit and
period. The design intentionally does not offer a generic "changed" label:
the reader must know whether a conclusion changed because evidence changed,
because a mechanism was reinterpreted, or because a data/model error was
corrected.

## Invariants and write rules

1. A revision belongs to exactly one research object and family. Its predecessor
   belongs to that same object and family and has sequence one lower.
2. `sequence=1` has no predecessor and `revision_kind=initial`. Every later
   sequence has exactly one predecessor and one authenticated change-set.
3. All parents are present in the declared historical basis, have
   `available_at <= as_of_cutoff`, and are immutable version identities—not
   natural keys or "latest" aliases.
4. Parent references, claim payload, revision metadata, and change-set hash are
   part of the revision content hash. Reordering equivalent parents must not
   change the hash; replacing a parent must change it.
5. The change-set is derived from the predecessor and candidate revision, then
   canonicalized and hashed. The server rejects a supplied change-set that does
   not recompute exactly.
6. A correction states what was wrong, the observed scope, and the replacement
   evidence/model parent. It may not delete or conceal the erroneous revision.
7. A research claim cannot cite a source as a causal driver merely because the
   source arrived near a price or business movement. The claim must reference a
   governed mechanism or be labelled descriptive/unknown.
8. Optimistic concurrency applies at family head: publishing against a stale
   predecessor returns a conflict and creates no revision or change-set.
9. A historical replay reads the selected revision and its stored parents only;
   it never fills a missing parent from newer data.
10. Revisions do not contain valuation, price targets, recommendations,
    position sizing, or action labels.

## Lifecycle and dependency effects

Publishing a successor does not erase the predecessor. It gives the successor
the new effective head and marks only the *family relationship* as superseded.
Other families that cited the older revision remain historically valid, but
their dependency view becomes `needs_review` if an upstream change-set contains
an evidence replacement, correction, mechanism/falsifier change, or a changed
key-factor claim.

The dependency graph is directional:

```text
source / observation / mechanism / model revision
                 │
                 ▼
          ResearchRevision A ──superseded by──► ResearchRevision B
                 │                                      │
                 └────────── RevisionChangeSet ◄────────┘
                                                        │
                                                        ▼
                                      downstream research marked needs_review
```

`needs_review` is a workflow signal, not a truth judgment. It must identify the
specific upstream revision and change-entry IDs that created the signal.

## Read model and presentation

The default company/industry research view shows the effective revision and a
small, evidence-led delta summary: "what changed", "why it changed", "which
claims changed", and "what remains unknown." It must show source/observation
locators and the historical cutoff for every material item.

The revision history view is a chronological chain. Selecting two revisions
renders the stored typed diff grouped in this order: evidence, mechanism,
business/industry inputs, earnings/model outputs, claims and uncertainty,
falsifiers, corrections. It never merges a later source into an older selected
revision. A reader can open either side's immutable parent references.

No visual change magnitude is shown without a unit, period, and parent source.
No "better/worse" color is assigned to a change without a governed claim that
defines the relevant direction; neutral source and uncertainty changes remain
neutral.

## APIs and service boundary

The write service accepts a candidate immutable payload plus
`expected_predecessor_id`. It resolves all parents at the requested basis,
validates the invariants above, computes the canonical change-set, atomically
appends revision + entries, and returns both hashes.

Read operations are:

- `effective_revision(research_object_id, family_key, cutoff)`;
- `revision(revision_id)`;
- `revision_history(research_object_id, family_key)`;
- `revision_diff(from_revision_id, to_revision_id)` for ancestor/descendant
  pairs only; and
- `downstream_review_impact(revision_id)`.

Cross-family arbitrary diffs may be added later, but must not be presented as a
successor explanation because they have no common revision contract.

## Acceptance criteria

1. Publishing an initial revision stores its basis, immutable parents, canonical
   payload, and deterministic content hash.
2. Publishing a successor preserves the first revision byte-for-byte, creates
   exactly one successor link and one recomputable typed change-set.
3. A source/observation/mechanism parent from another object, another basis, or
   after the cutoff is rejected before any write.
4. Identical candidate input ordering produces identical content/change-set
   hashes; changing a material parent or claim fragment changes the hash.
5. A stale expected predecessor returns a conflict and leaves no partial rows.
6. A correction remains visible in history and identifies the replaced evidence
   or model input; the original cannot be edited or deleted.
7. Historical replay of an old revision returns only its old claims and parents,
   even after later revisions exist.
8. A mechanism status/falsifier/evidence replacement marks each dependent
   effective revision `needs_review` with a precise causal link; adding an
   unrelated source does not.
9. The diff UI/API always includes cutoff, source locators, units, periods, and
   explicit Unknown/open-question changes where relevant.
10. Schema validation rejects PE, PB, DCF, price, target-price, buy/sell,
    stop-loss, position-size, and equivalent valuation/action fields from this
    revision subsystem.

## Out of scope

- live-data polling, automatic refresh, or AI completion of gaps;
- valuation, market price, recommendation, or portfolio action;
- mutable collaborative document editing;
- replacing the existing source-freeze, mechanism, industry, or earnings
  contracts; this design composes their immutable outputs.
