"""Portable setup for 0067 market rows that share business identity/time."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa


TABLES = (
    "uw_price_snapshots",
    "uw_fx_snapshots",
    "uw_capital_structure_snapshots",
    "uw_security_rights_versions",
)


def seed_0067_market_conflicts(connection: sa.Connection) -> dict[str, tuple[str, str]]:
    company_id = str(uuid4())
    security_id = str(uuid4())
    market_at = datetime(2026, 8, 25, 20, tzinfo=UTC)
    available_at = datetime(2026, 8, 25, 23, tzinfo=UTC)
    report_start = datetime(2026, 1, 1, tzinfo=UTC)
    report_end = datetime(2026, 6, 30, tzinfo=UTC)
    ids = {table: (str(uuid4()), str(uuid4())) for table in TABLES}
    connection.execute(
        sa.text(
            "INSERT INTO uw_research_objects "
            "(id, kind, external_key, canonical_name, created_at) VALUES "
            "(:company, 'company', 'legacy:company', 'Legacy Company', :now), "
            "(:security, 'security', 'legacy:security', 'Legacy Security', :now)"
        ),
        {"company": company_id, "security": security_id, "now": available_at},
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_price_snapshots ("
            "id,security_identity_id,price,currency,price_type,adjustment_basis,"
            "market_at,available_at,source_id,raw_hash,content_hash,created_at) "
            "VALUES (:id,:security,:value,'USD','official_close','unadjusted',"
            ":market,:available,:source,:raw,:content,:available)"
        ),
        [
            {
                "id": row_id,
                "security": security_id,
                "value": 100 + offset,
                "market": market_at,
                "available": available_at,
                "source": f"legacy-price-{offset}",
                "raw": str(offset + 1) * 64,
                "content": str(offset + 3) * 64,
            }
            for offset, row_id in enumerate(ids["uw_price_snapshots"])
        ],
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_fx_snapshots ("
            "id,base_currency,quote_currency,rate,quote_direction,market_at,"
            "available_at,source_id,raw_hash,content_hash,created_at) VALUES "
            "(:id,'USD','CNY',:value,'quote_per_base',:market,:available,"
            ":source,:raw,:content,:available)"
        ),
        [
            {
                "id": row_id,
                "value": 7.1 + offset / 10,
                "market": market_at,
                "available": available_at,
                "source": f"legacy-fx-{offset}",
                "raw": str(offset + 1) * 64,
                "content": str(offset + 3) * 64,
            }
            for offset, row_id in enumerate(ids["uw_fx_snapshots"])
        ],
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_capital_structure_snapshots ("
            "id,company_id,currency,cash,debt,minority_interest,investments,"
            "pension_liabilities,other_adjustments,basic_shares,diluted_shares,"
            "potential_dilution_descriptors,report_period_start,report_period_end,"
            "market_at,available_at,source_id,raw_hash,content_hash,created_at) "
            "VALUES (:id,:company,'USD',:value,20,1,5,0,0,100,110,'[]',"
            ":report_start,:report_end,:market,:available,:source,:raw,:content,"
            ":available)"
        ),
        [
            {
                "id": row_id,
                "company": company_id,
                "value": 10 + offset,
                "report_start": report_start,
                "report_end": report_end,
                "market": market_at,
                "available": available_at,
                "source": f"legacy-capital-{offset}",
                "raw": str(offset + 1) * 64,
                "content": str(offset + 3) * 64,
            }
            for offset, row_id in enumerate(ids["uw_capital_structure_snapshots"])
        ],
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_security_rights_versions ("
            "id,security_identity_id,version,economic_units,votes_per_unit,"
            "conversion_ratio,adr_ratio,dividend_rights_per_unit,effective_from,"
            "effective_to,source_id,raw_hash,supersedes_id,content_hash,created_at) "
            "VALUES (:id,:security,:version,:value,1,1,1,1,:market,NULL,:source,"
            ":raw,NULL,:content,:available)"
        ),
        [
            {
                "id": row_id,
                "security": security_id,
                "version": offset + 1,
                "value": 10 + offset,
                "market": market_at,
                "available": available_at,
                "source": f"legacy-rights-{offset}",
                "raw": str(offset + 1) * 64,
                "content": str(offset + 3) * 64,
            }
            for offset, row_id in enumerate(ids["uw_security_rights_versions"])
        ],
    )
    return ids


def clone_conflicting_insert_sql(
    table: str,
    *,
    fresh_business_key: bool = False,
) -> str:
    expressions = {
        "uw_price_snapshots": (
            "id,security_identity_id,price,currency,price_type,adjustment_basis,"
            "market_at,available_at,source_id,raw_hash,content_hash,created_at,"
            "legacy_business_conflict",
            "security_identity_id,price,currency,price_type,adjustment_basis,"
            "market_at,available_at,'new-price',:raw,:content,created_at,:legacy",
            "security_identity_id,price,currency,'fresh_close',adjustment_basis,"
            "market_at,available_at,'new-price',:raw,:content,created_at,:legacy",
        ),
        "uw_fx_snapshots": (
            "id,base_currency,quote_currency,rate,quote_direction,market_at,"
            "available_at,source_id,raw_hash,content_hash,created_at,"
            "legacy_business_conflict",
            "base_currency,quote_currency,rate,quote_direction,market_at,"
            "available_at,'new-fx',:raw,:content,created_at,:legacy",
            "base_currency,'JPY',rate,quote_direction,market_at,available_at,"
            "'new-fx',:raw,:content,created_at,:legacy",
        ),
        "uw_capital_structure_snapshots": (
            "id,company_id,currency,cash,debt,minority_interest,investments,"
            "pension_liabilities,other_adjustments,basic_shares,diluted_shares,"
            "potential_dilution_descriptors,report_period_start,report_period_end,"
            "market_at,available_at,source_id,raw_hash,content_hash,created_at,"
            "legacy_business_conflict",
            "company_id,currency,cash,debt,minority_interest,investments,"
            "pension_liabilities,other_adjustments,basic_shares,diluted_shares,"
            "potential_dilution_descriptors,report_period_start,report_period_end,"
            "market_at,available_at,'new-capital',:raw,:content,created_at,:legacy",
            "company_id,currency,cash,debt,minority_interest,investments,"
            "pension_liabilities,other_adjustments,basic_shares,diluted_shares,"
            "potential_dilution_descriptors,report_period_start,report_period_end,"
            ":fresh_at,available_at,'new-capital',:raw,:content,created_at,:legacy",
        ),
        "uw_security_rights_versions": (
            "id,security_identity_id,version,economic_units,votes_per_unit,"
            "conversion_ratio,adr_ratio,dividend_rights_per_unit,effective_from,"
            "effective_to,source_id,raw_hash,supersedes_id,content_hash,created_at,"
            "legacy_business_conflict",
            "security_identity_id,99,economic_units,votes_per_unit,conversion_ratio,"
            "adr_ratio,dividend_rights_per_unit,effective_from,effective_to,"
            "'new-rights',:raw,NULL,:content,created_at,:legacy",
            "security_identity_id,99,economic_units,votes_per_unit,conversion_ratio,"
            "adr_ratio,dividend_rights_per_unit,:fresh_at,effective_to,"
            "'new-rights',:raw,NULL,:content,created_at,:legacy",
        ),
    }
    columns, conflicting, fresh = expressions[table]
    selected = fresh if fresh_business_key else conflicting
    return (
        f"INSERT INTO {table} ({columns}) SELECT :id,{selected} "
        f"FROM {table} LIMIT 1"
    )
