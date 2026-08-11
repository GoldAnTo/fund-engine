# Empty-review run terminal state

## Context

In the industrial-foxconn live case, a monitor run processed its frozen scope
without creating an atomic-claim candidate or relationship candidate.  The
runner then recorded `waiting_for_review`.  The review queue was empty, so the
human-facing next action was impossible to perform and the run remained in the
active-run list.

## Options considered

1. Keep `waiting_for_review` and add an empty-state review page.  This preserves
   the status but misrepresents the required human action.
2. Add a new terminal `no_change` status.  This is precise, but expands API,
   schema, and presentation contracts for a condition already represented by a
   stop reason.
3. **Recommended: use `succeeded` when no reviewable candidates exist.**  Keep
   `no_new_evidence` or `max_rounds_reached` as the immutable stop reason.
   This accurately represents a completed run, needs no schema migration, and
   leaves `waiting_for_review` exclusively for runs that require human review.

## Design

At each successful terminal branch in `AutoResearchService.execute`, determine
whether the run produced reviewable candidate output.  If it did, retain the
existing `waiting_for_review` handoff.  If it did not, persist `succeeded` with
the existing terminal reason.  Failed and cancelled branches remain unchanged.

The active-run API will continue to return only queued, running, and genuinely
waiting-for-review runs.  Its `next_action` stays review-specific only for the
latter.  The existing completed-run presentation is used for `succeeded`.

The completion event must state that no new review material was produced for a
successful no-change terminal state, rather than claiming a pending human
action.  Frozen scope, event history, budget use, and stop reason remain
unchanged.

## Tests and acceptance

1. A run with no new evidence and no candidates ends as `succeeded`, retains
   `no_new_evidence`, and has no review handoff.
2. A run that reaches the last round with no candidates ends as `succeeded`,
   retains `max_rounds_reached`, and has no review handoff.
3. A run with reviewable candidates still ends as `waiting_for_review`.
4. The active-runs endpoint excludes a successful no-change run and the
   frontend labels it completed.
5. Re-run the Industrial Foxconn monitor manually: the execution exits without
   a phantom review action and its audit timeline remains readable.

## Non-goals

This change does not alter source admission, extraction quality, monitor-scope
selection, candidate review rules, or the audit record of an earlier run.
