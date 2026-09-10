# Professional Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the persisted four-role task DAG with authorized evidence, isolated network work, durable attempts, expiring leases and stale-result fencing.

**Architecture:** `ProfessionalWorker(session_factory, client_factory=LLMClient.from_env, lease_seconds=240, clock=utcnow).run_once() -> bool` claims the newest eligible task under a short team transaction. It loads a frozen authorized input snapshot, commits, generates through the strict LLM client while a separate heartbeat renews the lease, then starts a new transaction to reauthorize all original inputs and conditionally persist an immutable result. No worker path creates a human review.

**Tech Stack:** SQLAlchemy, PostgreSQL row locks / SQLite BEGIN IMMEDIATE, Python heartbeat thread, existing ResearchGatewayContent and strict generate_role, pytest with real SDK models and offline provider transport.

---

The user approved complete production implementation. Root task owns models, migrations, team API and commands; this worker uses the existing eight-table model and does not redefine it.

## Files

- Create `backend/app/services/research_team_worker.py`: claim, lease renewal, authorization snapshot and conditional completion.
- Create `backend/app/scripts/run_professional_worker.py`: finite validated CLI options, one-shot and supervised loop execution, safe operational logging.
- Create `backend/tests/test_research_team_worker.py`: real native admission fixture, isolated DB sessions and SDK-shaped completions.

## Task 1: Claim independent roles and enforce the DAG

- [ ] Add a failing worker test based on `complete_authorized_evidence`:

```python
worker = ProfessionalWorker(factory, client_factory=lambda: offline_client)
assert all(worker.run_once() for _ in range(4))
assert {task.role for task in tasks if task.status == "succeeded"} == {
    "industry", "finance", "strategy", "quality",
}
assert len(outputs) == 4
assert len({output.task_id for output in outputs}) == 4
```

- [ ] Run `env -u LLM_API_KEY -u TEST_DATABASE_URL -u NEO4J_URL APP_ENV=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_research_team_worker.py -q` in backend and confirm missing-worker failures.
- [ ] Select newest task per role, lock its active team (`FOR UPDATE SKIP LOCKED` on PostgreSQL), validate native state and dependencies, and claim with a UUID lease token and increasing attempt counter. SQLite reserves the writer before reading. Do not run blocked tasks until evidence/dependencies are actually available.
- [ ] Load full authorized evidence details with `ResearchGatewayContent`; reject truncated projections and wait on missing evidence. Include immutable dependency output IDs/content, frozen scope/cutoff/source policy, role instruction and operation IDs in a canonical hashed input. Never omit sources silently to fit budget.
- [ ] Re-run the DAG and eligibility tests to green.

## Task 2: Network isolation and fencing

- [ ] Add tests for separate workers entering independent providers concurrently, no open caller DB transaction during provider work, stale-lease recovery, pause/cancel during generation, source revocation and fabricated citations.

```python
def provider_reply():
    assert not any(session.in_transaction() for session in worker_sessions)
    cancel_from_another_session()
    return valid_sdk_completion()

worker.run_once()
assert not persisted_outputs()
assert persisted_attempts()  # Paid/observed work remains accounted for.
```

- [ ] Run the tests and confirm the expected concurrency/authorization failures before implementing guards.
- [ ] Commit claim before calling `client_factory` / `generate_role`. Capture immutable LLMAttempt records in memory. Renew lease in a separate short transaction every `min(15, lease_seconds / 3)` seconds while generating. On DB heartbeat failure, use fail-stop process termination as the existing worker heartbeat publisher does; injectable termination supports offline tests.
- [ ] On return, reacquire the team lock, persist safe attempt metadata, then require current lease token, unexpired lease, active team, newest task and nonfailed/noncancelled native run. Re-read each original evidence ID and exact quote/content hashes and dependency output versions before publishing. Reject all late results and emit a safe event.
- [ ] Re-run concurrency and rejection tests to green.

## Task 3: Recovery and operational entrypoint

- [ ] Add tests for expired lease, dependency failure, newest-role-only selection, safe provider error reason, nullable usage, heartbeat renewal and CLI one-shot behavior.
- [ ] Run red tests; implement stable status/reason codes and per-team event sequence increments under lock. A changed blocked state counts as progress once; unchanged blocked polling does not generate events.
- [ ] Add `python -m app.scripts.run_professional_worker --once` and `--loop`, bounded polling and lease configuration. Log fixed operational failures without provider exception messages or tracebacks.
- [ ] Run worker/generation/LLM regression and owned-file Ruff/diff checks, and record exact results below.

## Boundaries

The ledger fence provides one accepted output per task. A crashed process or explicit cancellation cannot prove that a remote provider did not consume tokens; metering records only responses/errors observed before process exit. The heartbeat prevents a healthy worker's long synchronous request from becoming a reclaimable stale lease. Pause/cancel invalidates the token and rejects late output; it does not claim a remote API cancellation guarantee. Postgres migrations and command permissions remain root-owned.
