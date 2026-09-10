# Professional Team History Review - 2026-09-09

Scope: bounded backend review of the current `fundclaw-gateway-p0-plan` worktree, focused on `backend/app/services/research_team.py`, the professional worker/read/generation surfaces, and the new research-team plus CLI shutdown regressions. I did not inspect local `.env` files, Docker, provider-backed paths, network state, or live databases.

## Findings

No actionable issues found.

## Evidence Reviewed

- `ResearchTeamService._successors` computes affected successors, creates a new revision, rewires normal upstream parents through the newest affected tasks, and then appends at most the carried same-role history edge for directed/retry/downstream cases (`backend/app/services/research_team.py:92`).
- Directed targets bind to the previous successful same-role task only when that previous task succeeded, preserving the exact immutable answer rather than a failed or in-flight task (`backend/app/services/research_team.py:120`).
- Retry is limited to current latest failed/blocked tasks; a stale `task_id` or a non-current task target produces `no_retryable_current_task` (`backend/app/services/research_team.py:200`).
- Retry carries same-role history from the failed retry target rather than adding the failed task as a dependency (`backend/app/services/research_team.py:127` and `backend/app/services/research_team.py:249`).
- Worker payload loading rejects invalid dependency graphs with more than one same-role history parent or same-role parents from the current/future revision, and verifies all parents are in the same run and succeeded before payload publication (`backend/app/services/research_team_worker.py:231`).
- Worker finish re-loads and hashes the payload before publishing, so changes to authorization or inputs fence stale results (`backend/app/services/research_team_worker.py:296`).
- CLI loop shutdown preserves return code `1` when marking loop failure itself fails, while logging only the fixed operational message (`backend/app/scripts/run_professional_worker.py:100`).

## Regression Coverage Checked

- `test_retry_preserves_directed_same_role_history_without_failed_task_edge` checks retry of a failed directed quality task keeps the old successful quality output and excludes the failed task (`backend/tests/test_research_team.py:162`).
- `test_upstream_refresh_preserves_downstream_directed_history_edge` checks an upstream refresh regenerates downstream quality while preserving the old successful quality history edge and instruction (`backend/tests/test_research_team.py:185`).
- `test_directed_followup_uses_immutable_prior_same_role_output` checks the worker payload includes the prior same-role output for a directed follow-up (`backend/tests/test_research_team_worker.py:296`).
- `test_retried_directed_quality_payload_keeps_old_successful_output` checks retried directed quality payloads retain the original successful output and exclude the failed task (`backend/tests/test_research_team_worker.py:377`).
- `test_loop_failure_does_not_publish_database_exception_on_shutdown` checks the CLI returns `1` without leaking worker or database sentinel text when shutdown failure marking raises (`backend/tests/test_professional_worker_cli.py:4`).

## Verification

Command run from `backend`:

```bash
env -u LLM_API_KEY -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test .venv/bin/python -m pytest tests/test_research_team.py tests/test_research_team_api.py tests/test_research_team_generation.py tests/test_research_team_read.py tests/test_research_team_stream.py tests/test_research_team_worker.py tests/test_professional_worker_cli.py
```

Result: `62 passed, 2 warnings in 26.63s`.

Warnings:

- `starlette.testclient` deprecation warning for `anyio.abc.BlockingPortal`.
- Pydantic warning that `SourceLocatorV1.schema` shadows a parent attribute.
