"""Frozen 0076 guard contracts, independent from mutable ORM metadata."""

from __future__ import annotations

import hashlib

from sqlalchemy import text

COMPANY_STUDY_TABLES = frozenset(
    {
        "company_study_revisions",
        "company_study_activities",
        "company_study_monitors",
        "company_studies",
        "company_study_requests",
    }
)
SQLITE_GUARDS = {
    "cs_frozen_company_studies": (
        "company_studies",
        "eefafb47d29700de21f183460dd2f5723694598572e17d4b1104feb235247bf6",
    ),
    "cs_frozen_company_study_activities": (
        "company_study_activities",
        "bc0b8d360e632352f123afc5146851e0a084f9ba333f457483e1dce934bece42",
    ),
    "cs_no_delete_company_studies": (
        "company_studies",
        "a1e7885c7f3ddd7cf44b18b28fa84c666cbc822a13e79374540aa9a05339446c",
    ),
    "cs_no_delete_company_study_activities": (
        "company_study_activities",
        "8735f35998f17564dc4ffb2ee6621bd95858d5eecead00466023410c455b5da0",
    ),
    "cs_no_delete_company_study_monitors": (
        "company_study_monitors",
        "df8cff43db321b961c407ddc8b157e1f11a7401f5838ef25956e42500e16ddeb",
    ),
    "cs_no_delete_company_study_requests": (
        "company_study_requests",
        "fa512f3f370781f6f887b1c84850f71e36304dfe4ac707d207d4291a2c9b81a3",
    ),
    "cs_no_delete_company_study_revisions": (
        "company_study_revisions",
        "60432f14a9fb59ffe8ae3e660acd4faf1d2cdadd894b6173d6870d1eae46acea",
    ),
    "cs_no_replace_company_studies": (
        "company_studies",
        "150a00674c498c70caa897688d54da91928a7b24d46397b1feba2407fc3cc2b4",
    ),
    "cs_no_replace_company_study_activities": (
        "company_study_activities",
        "9ad5f71afc285f802d4917e5112254f106db06054aa32ef8f1fd28e7609ddf60",
    ),
    "cs_no_replace_company_study_monitors": (
        "company_study_monitors",
        "2063a7a62e4844b466a426307ba2f48693872d1a6d3141ac48add262f945975a",
    ),
    "cs_no_replace_company_study_requests": (
        "company_study_requests",
        "2d4c6516042113986c4041762cd6e64135f2d05d8d1ae0cf744a4dedfde92caf",
    ),
    "cs_no_replace_company_study_revisions": (
        "company_study_revisions",
        "7751a9bd7c3edc1b97efafff6a2f959e8dcd7a45963c0fa7ac04aefa39f3edb6",
    ),
    "cs_no_update_company_study_monitors": (
        "company_study_monitors",
        "f2d19ebe6fcdf732fe18c8c18dbceea1eed2c6a34e0f517a8efc763fbaba9faf",
    ),
    "cs_no_update_company_study_requests": (
        "company_study_requests",
        "6471365884bc7ddc55f1d73a1469bc6ee2b630ed3f1fc23c52edd805b2637295",
    ),
    "cs_no_update_company_study_revisions": (
        "company_study_revisions",
        "7a9256fd2cddb9f02598043672c7e2c79838ff3c882183fb7f24e9dfdcd4d3b7",
    ),
}
POSTGRESQL_GUARDS = {
    ("company_studies", "cs_frozen_company_studies"): (
        19,
        "cs_frozen_company_studies",
        "0b1e56a5990d2cf85d53b587f84789c60b22ccc2690c684ce891ec353cc8108c",
    ),
    ("company_studies", "cs_no_delete_company_studies"): (
        11,
        "cs_no_delete_company_studies",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_activities", "cs_frozen_company_study_activities"): (
        19,
        "cs_frozen_company_study_activities",
        "6faeb8dc9962d77fcd0e85d2614e30328fb5204174b556876dc68bc7f756ffb8",
    ),
    ("company_study_activities", "cs_no_delete_company_study_activities"): (
        11,
        "cs_no_delete_company_study_activities",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_monitors", "cs_no_delete_company_study_monitors"): (
        11,
        "cs_no_delete_company_study_monitors",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_monitors", "cs_no_update_company_study_monitors"): (
        19,
        "cs_no_update_company_study_monitors",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_requests", "cs_no_delete_company_study_requests"): (
        11,
        "cs_no_delete_company_study_requests",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_requests", "cs_no_update_company_study_requests"): (
        19,
        "cs_no_update_company_study_requests",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_revisions", "cs_no_delete_company_study_revisions"): (
        11,
        "cs_no_delete_company_study_revisions",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
    ("company_study_revisions", "cs_no_update_company_study_revisions"): (
        19,
        "cs_no_update_company_study_revisions",
        "f06cdb207ed2145fc15b3e2d26ee693837623c5302a34aed589464e994a9931b",
    ),
}


def require_company_study_guards(engine):
    from app.db_migrations import (
        _POSTGRESQL_PROFESSIONAL_TEAM_TRIGGER_STATE_SQL,
        UnmanagedDatabaseSchemaError,
        _normalized_sqlite_ddl,
        _postgresql_gateway_trigger_state_is_canonical,
        _postgresql_trusted_schema,
    )

    def reject():
        raise UnmanagedDatabaseSchemaError(
            "database lacks a canonical company study guard"
        )

    if engine.dialect.name == "sqlite":
        parameters = {
            f"table_{i}": table for i, table in enumerate(sorted(COMPANY_STUDY_TABLES))
        }
        slots = ", ".join(f":{key}" for key in parameters)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        f"SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name IN ({slots})"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
        actual = {
            row["name"]: (
                row["tbl_name"],
                hashlib.sha256(_normalized_sqlite_ddl(row["sql"]).encode()).hexdigest(),
            )
            for row in rows
        }
        if actual != SQLITE_GUARDS:
            reject()
        return
    if engine.dialect.name != "postgresql":
        reject()
    schema = _postgresql_trusted_schema(engine)
    # Table identifiers are frozen release literals, never user input.
    relations = ", ".join(
        f"to_regclass(format('%I.%I', CAST(:schema_name AS text), '{table}'))"
        for table in sorted(COMPANY_STUDY_TABLES)
    )
    query = (
        _POSTGRESQL_PROFESSIONAL_TEAM_TRIGGER_STATE_SQL.split(
            "  AND trigger_row.tgrelid IN ("
        )[0]
        + f" AND trigger_row.tgrelid IN ({relations})"
    )
    with engine.connect() as connection:
        if connection.scalar(text("SELECT current_schema()")) != schema:
            reject()
        rows = connection.execute(text(query), {"schema_name": schema}).mappings().all()
    states = {(row["table_name"], row["trigger_name"]): row for row in rows}
    if set(states) != set(POSTGRESQL_GUARDS):
        reject()
    for (table, name), (
        trigger_type,
        function_name,
        digest,
    ) in POSTGRESQL_GUARDS.items():
        if not _postgresql_gateway_trigger_state_is_canonical(
            states[(table, name)],
            table_name=table,
            trigger_name=name,
            trigger_type=trigger_type,
            function_name=function_name,
            function_source_sha256=digest,
            schema_name=schema,
        ):
            reject()
