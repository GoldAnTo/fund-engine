"""Explicit, idempotent one-click initialization for bundled product identities."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db import SessionLocal
from app.underwriting.fixtures.product_foundation import (
    load_product_foundation_fixture,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)


def main() -> int:
    fixture = load_product_foundation_fixture()
    with SessionLocal() as session:
        loaded = ProductFoundationFixtureService(
            session, now=lambda: datetime.now(UTC)
        ).load(fixture)
        session.commit()
    print(
        "Installed product identity foundation "
        f"{loaded.content_hash} ({len(loaded.objects)} objects)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
