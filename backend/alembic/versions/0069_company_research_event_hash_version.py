"""Version company research event hashes without downgrade inference.

Revision ID: 0069
Revises: 0068
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Union
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_EVENTS = "uw_company_research_events"
_CHECK = "ck_uw_company_research_event_hash_version"


def _canonical_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _canonical_uuid(value: object) -> str:
    return str(value if isinstance(value, UUID) else UUID(str(value)))


def _canonical_created_at(value: object) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise RuntimeError("cannot authenticate company research event history")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _event_hash(
    row: sa.RowMapping, *, version: int
) -> str:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    value: dict[str, object] = {
        "schema_version": f"company-research-event.v{version}",
        "preparation_id": _canonical_uuid(row["preparation_id"]),
        "sequence": row["sequence"],
        "previous_event_hash": row["previous_event_hash"],
        "event_type": row["event_type"],
        "payload": payload,
    }
    if version == 2:
        value["created_at"] = _canonical_created_at(row["created_at"])
    return _canonical_hash(value)


def _authenticated_hash_versions() -> tuple[tuple[object, int], ...]:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT id, preparation_id, sequence, previous_event_hash, "
            f"event_type, payload, content_hash, created_at FROM {_EVENTS} "
            "ORDER BY preparation_id, sequence"
        )
    ).mappings()
    authenticated: list[tuple[object, int]] = []
    active_preparation: str | None = None
    expected_sequence = 1
    expected_previous_hash: str | None = None
    v2_started = False
    try:
        for row in rows:
            preparation_id = _canonical_uuid(row["preparation_id"])
            if preparation_id != active_preparation:
                active_preparation = preparation_id
                expected_sequence = 1
                expected_previous_hash = None
                v2_started = False
            matches = tuple(
                version
                for version in (1, 2)
                if row["content_hash"] == _event_hash(row, version=version)
            )
            if (
                len(matches) != 1
                or row["sequence"] != expected_sequence
                or row["previous_event_hash"] != expected_previous_hash
                or (matches[0] == 1 and v2_started)
            ):
                raise RuntimeError(
                    "cannot authenticate company research event history"
                )
            version = matches[0]
            authenticated.append((row["id"], version))
            expected_sequence += 1
            expected_previous_hash = row["content_hash"]
            v2_started = v2_started or version == 2
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "cannot authenticate company research event history"
        ) from exc
    return tuple(authenticated)


def _drop_immutable_triggers(dialect_name: str) -> None:
    suffix = "" if dialect_name == "sqlite" else f" ON {_EVENTS}"
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{_EVENTS}{suffix}")
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{_EVENTS}{suffix}")


def _install_immutable_triggers(dialect_name: str) -> None:
    if dialect_name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER no_update_{_EVENTS}
            BEFORE UPDATE ON {_EVENTS}
            BEGIN
                SELECT RAISE(ABORT, 'immutable company research table is append-only');
            END;
        """)
        op.execute(f"""
            CREATE TRIGGER no_delete_{_EVENTS}
            BEFORE DELETE ON {_EVENTS}
            BEGIN
                SELECT RAISE(ABORT, 'immutable company research table is append-only');
            END;
        """)
        return
    op.execute(
        f"CREATE TRIGGER no_update_{_EVENTS} BEFORE UPDATE ON {_EVENTS} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{_EVENTS} BEFORE DELETE ON {_EVENTS} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    authenticated = _authenticated_hash_versions()
    _drop_immutable_triggers(dialect_name)
    op.add_column(
        _EVENTS,
        sa.Column("hash_version", sa.Integer(), nullable=True),
    )
    for event_id, version in authenticated:
        op.execute(
            sa.text(
                f"UPDATE {_EVENTS} SET hash_version = :version WHERE id = :event_id"
            ).bindparams(version=version, event_id=event_id)
        )
    if dialect_name == "sqlite":
        with op.batch_alter_table(_EVENTS, recreate="always") as batch:
            batch.alter_column("hash_version", nullable=False)
            batch.create_check_constraint(_CHECK, "hash_version IN (1, 2)")
    else:
        op.alter_column(_EVENTS, "hash_version", nullable=False)
        op.create_check_constraint(_CHECK, _EVENTS, "hash_version IN (1, 2)")
    _install_immutable_triggers(dialect_name)


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _drop_immutable_triggers(dialect_name)
    if dialect_name == "sqlite":
        with op.batch_alter_table(_EVENTS, recreate="always") as batch:
            batch.drop_constraint(_CHECK, type_="check")
            batch.drop_column("hash_version")
    else:
        op.drop_constraint(_CHECK, _EVENTS, type_="check")
        op.drop_column(_EVENTS, "hash_version")
    _install_immutable_triggers(dialect_name)
