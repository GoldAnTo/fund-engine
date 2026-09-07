"""Regression coverage for operational conflict handling and input types."""
from decimal import Decimal
from typing import get_type_hints

import pytest
from sqlalchemy import delete

from app.errors import ConflictError
from app.models.operational import IdempotencyKey, Job
from app.repositories.operational import IdempotencyRepository, JobRepository
from app.services.market_expression import MarketObservationInput


def test_missing_idempotency_winner_returns_conflict_without_losing_outer_work(
    cmd_session, monkeypatch
):
    repo = IdempotencyRepository(cmd_session)
    repo.acquire(key="contested-command", request_fingerprint="winner")
    cmd_session.commit()
    pending_job = JobRepository(cmd_session).add_job(kind="propose")
    pending_job_id = pending_job.id
    original_scalar = cmd_session.scalar
    reloads = []

    def remove_winner_before_reload(statement, *args, **kwargs):
        # acquire() reaches this read only after a real duplicate-key INSERT
        # fails inside its savepoint. Model a winner removed before reloading
        # without mocking either the unique constraint or the query result.
        cmd_session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.key == "contested-command")
        )
        reloads.append(statement)
        return original_scalar(statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(cmd_session, "scalar", remove_winner_before_reload)
        with pytest.raises(ConflictError, match="idempotency_conflict"):
            repo.acquire(key="contested-command", request_fingerprint="loser")

    assert len(reloads) == 1
    assert cmd_session.is_active
    cmd_session.commit()
    cmd_session.expire_all()
    assert cmd_session.get(Job, pending_job_id) is not None
    assert repo.get("contested-command") is None


def test_market_observation_input_decimal_annotation_resolves():
    assert get_type_hints(MarketObservationInput)["relative_return"] == Decimal | None
