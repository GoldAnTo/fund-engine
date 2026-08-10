"""Create the isolated, live Industrial Foxconn forecast-verdict demonstration.

Usage (from ``backend``)::

    .venv/bin/python -m app.scripts.run_industrial_foxconn_forecast_case \
      --database-url sqlite:///./.local/industrial-foxconn-case.db

The command reads GILDATA_TOKEN from ``backend/.env``.  It intentionally
prints no provider URL or credentials.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.datasources.gildata.client import GildataMCPClient, GildataMCPError
from app.env import load_local_env
from app.models.ledger import Base
from app.services.industrial_foxconn_forecast_case import load_industrial_foxconn_sources
from app.services.live_industrial_foxconn_case import materialize_live_industrial_foxconn_case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", default="local-demo")
    args = parser.parse_args()

    load_local_env()
    engine = create_engine(args.database_url)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        bundle = load_industrial_foxconn_sources(GildataMCPClient.from_env())
        result = materialize_live_industrial_foxconn_case(
            session, bundle=bundle, tenant_id=args.tenant_id
        )
    except (GildataMCPError, ValueError) as exc:
        session.rollback()
        print(f"case run failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
        engine.dispose()

    print("Industrial Foxconn live case materialized")
    print(f"case_id={result.case_id}")
    print(f"verdict_id={result.verdict_id}")
    print(f"baseline_cny={result.baseline_value}")
    print(f"expected_cny={result.expected_value}")
    print(f"actual_cny={result.actual_value}")
    print(f"outcome={result.outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
