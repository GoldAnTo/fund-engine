"""Schema contracts for trusted external users and explicit Case access."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

import app.models  # noqa: F401
from app.models.ledger import Base


def _unique_column_sets(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_identity_tables_publish_stable_uniqueness_and_role_contracts() -> None:
    assert {"research_users", "case_access_grants"} <= set(Base.metadata.tables)

    users = Base.metadata.tables["research_users"]
    grants = Base.metadata.tables["case_access_grants"]
    assert {("issuer", "subject"), ("tenant_id", "normalized_email")} <= (
        _unique_column_sets(users)
    )
    assert {("research_case_id", "user_id")} <= _unique_column_sets(grants)
    assert {
        str(constraint.sqltext)
        for constraint in grants.constraints
        if isinstance(constraint, sa.CheckConstraint)
    } == {"role IN ('owner', 'editor', 'reviewer', 'viewer')"}


def test_case_grant_role_is_enforced_by_the_database(engine) -> None:
    now = datetime.now(UTC)
    users = Base.metadata.tables["research_users"]
    cases = Base.metadata.tables["research_cases"]
    grants = Base.metadata.tables["case_access_grants"]
    user_id = uuid.uuid4()
    case_id = uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(
            users.insert().values(
                id=user_id,
                issuer="https://issuer.example/realms/research",
                subject="subject-a",
                tenant_id="tenant-a",
                display_name="Alice",
                normalized_email="alice@example.com",
                active=True,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            cases.insert().values(
                id=case_id,
                title="Identity contract",
                industry_topic="identity",
                created_at=now,
                created_by=f"user:{user_id}",
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                grants.insert().values(
                    id=uuid.uuid4(),
                    research_case_id=case_id,
                    user_id=user_id,
                    role="administrator",
                    granted_by_principal_id=f"user:{user_id}",
                    reason="invalid fixture",
                    created_at=now,
                    updated_at=now,
                )
            )


def test_oidc_identity_pair_is_immutable_while_profile_fields_remain_mutable(
    engine,
) -> None:
    created_at = datetime.now(UTC)
    last_seen_at = datetime.now(UTC)
    users = Base.metadata.tables["research_users"]
    user_id = uuid.uuid4()
    subject = f"subject-{user_id.hex}"
    normalized_email = f"alice-{user_id.hex}@example.com"

    with engine.begin() as connection:
        connection.execute(
            users.insert().values(
                id=user_id,
                issuer="https://issuer.example/realms/research",
                subject=subject,
                tenant_id="tenant-a",
                display_name="Alice",
                normalized_email=normalized_email,
                active=True,
                last_seen_at=created_at,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        connection.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                display_name="Alice Updated",
                active=False,
                last_seen_at=last_seen_at,
                updated_at=last_seen_at,
            )
        )

    with engine.connect() as connection:
        profile = connection.execute(
            sa.select(
                users.c.display_name,
                users.c.active,
                users.c.last_seen_at,
            ).where(users.c.id == user_id)
        ).one()
        assert profile.display_name == "Alice Updated"
        assert profile.active is False
        assert profile.last_seen_at == last_seen_at.replace(tzinfo=None)

    for column_name, replacement in (
        ("issuer", "https://other-issuer.example/realms/research"),
        ("subject", "subject-b"),
    ):
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    users.update()
                    .where(users.c.id == user_id)
                    .values(**{column_name: replacement})
                )

    with engine.connect() as connection:
        identity = connection.execute(
            sa.select(users.c.issuer, users.c.subject).where(users.c.id == user_id)
        ).one()
        assert identity == (
            "https://issuer.example/realms/research",
            subject,
        )
