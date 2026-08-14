"""Model package: import every model module so ``Base.metadata`` aggregates
all tables (ledger + operational + events + versions + proposals).

Importing this package is enough to make ``create_all`` / Alembic autogenerate
see every table.  Keep the imports side-effect-free (no engine creation).
"""
from app.models import acquisition  # noqa: F401
from app.models import events  # noqa: F401
from app.models import event_research  # noqa: F401
from app.models import fund_disclosure_sync  # noqa: F401
from app.models import operational  # noqa: F401
from app.models import proposals  # noqa: F401
from app.models import research_monitor  # noqa: F401
from app.models import research_protocol  # noqa: F401
from app.models import research_expression  # noqa: F401
from app.models import source_governance  # noqa: F401
from app.models import versions  # noqa: F401
from app.models.ledger import Base  # noqa: F401

__all__ = [
    "Base",
    "acquisition",
    "events",
    "event_research",
    "fund_disclosure_sync",
    "operational",
    "proposals",
    "research_monitor",
    "research_protocol",
    "research_expression",
    "source_governance",
    "versions",
    "ledger",
]
