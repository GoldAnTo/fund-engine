"""Database engine and unit-of-work dependency.

The evidence ledger is the only write-model truth and lives in PostgreSQL.
The engine is created lazily; importing this module does not open a connection.
"""
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://evidence:evidence@localhost:5432/evidence",
)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
