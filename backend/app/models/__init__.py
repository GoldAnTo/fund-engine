"""Model package: import every model module so ``Base.metadata`` aggregates
all tables (ledger + operational + events + versions + proposals).

Importing this package is enough to make ``create_all`` / Alembic autogenerate
see every table.  Keep the imports side-effect-free (no engine creation).
"""
from app.models import events  # noqa: F401
from app.models import operational  # noqa: F401
from app.models import proposals  # noqa: F401
from app.models import versions  # noqa: F401
from app.models.ledger import Base  # noqa: F401

__all__ = ["Base", "events", "operational", "proposals", "versions", "ledger"]
