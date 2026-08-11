"""Append the approved source-bound daily market window to the local demo Case."""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.datasources.gildata.client import GildataMCPClient, GildataMCPError
from app.db_migrations import upgrade_database_to_head
from app.env import load_local_env
from app.services.industrial_foxconn_forecast_case import load_industrial_foxconn_sources
from app.services.live_industrial_foxconn_case import append_live_industrial_foxconn_market_window


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    load_local_env()
    upgrade_database_to_head(args.database_url)
    engine = create_engine(args.database_url)
    session = sessionmaker(bind=engine)()
    try:
        record = append_live_industrial_foxconn_market_window(
            session,
            bundle=load_industrial_foxconn_sources(GildataMCPClient.from_env()),
        )
    except (GildataMCPError, ValueError) as exc:
        session.rollback()
        print(f"market window append failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
        engine.dispose()
    print(f"market_observation_id={record.id}")
    print(f"window={record.window_label}")
    print(f"relative_return={record.relative_return}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
