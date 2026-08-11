# Provisional assessment review closure

## Context

The Industrial Foxconn v2 monitor run generated a provisional AI assessment
(`insufficient_evidence`) and a durable `review_assessment` task.  The case
review page only renders evidence-link and atomic-claim queues, so it displayed
zero items although the run was correctly paused for a human decision.  The
existing assessment review command closes the task but leaves the run in
`waiting_for_review`.

## Decision

Keep the human gate.  Do not auto-accept or auto-reject an assessment merely
because its conclusion is `insufficient_evidence`.

Show a run's pending provisional assessment inside its monitor run detail,
where its frozen scope, event log, and stop reason are already visible.  The
reviewer chooses confirm, modify, or reject, enters a reason, and submits the
existing assessment-review command.  The UI reads the result back from the
server before showing a completion notice.

After a submitted assessment decision, the backend closes only the matching
task.  It then checks every reviewable output for each affected run.  A waiting
run becomes `succeeded` only if it has no open proposal, assessment, or atomic
claim review task.  It retains the original stop reason, frozen scope, task
results, and prior events, and receives an append-only completion-after-review
event.  Runs with any remaining review work stay paused.

## Interface and data flow

1. Extend the existing authenticated `GET /research-runs/{run_id}` response
   with typed assessment review data: assessment ID, provisional conclusion,
   rationale, gaps, task status, and the run-local review-task ID.
2. The monitor page loads that detail for the selected run and renders a card
   only for an open `review_assessment` task.
3. The card submits `POST /assessments/{assessment_id}/reviews` with the human
   outcome, optional replacement conclusion for a modification, reason, and
   reviewer.  A blank reason never sends a request.
4. The command appends the immutable review decision, closes its operational
   task, evaluates the affected run's remaining review work, and returns the
   standard response.  The client reloads monitor detail and run events.

## Acceptance criteria

- A pending assessment appears in the run that produced it; an unrelated case
  cannot read it through the tenant-protected run endpoint.
- Confirm, modify, and reject require a non-empty reason and create an
  immutable review decision without mutating the AI assessment.
- Reviewing the final outstanding assessment transitions only that run from
  `waiting_for_review` to `succeeded`, removes it from active monitoring, and
  records an event that the human review completed.
- If a proposal or atomic-claim review remains, the run stays
  `waiting_for_review`.
- In the Industrial Foxconn browser path, the reviewer can see the
  `insufficient_evidence` assessment, decide it, and see the run complete with
  its original frozen scope and stop reason intact.

## Non-goals

This slice does not redesign the global review queue, alter evidence review
rules, hide historical runs, or treat a provisional assessment as a published
research conclusion.
