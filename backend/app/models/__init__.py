"""Model package: import every model module so ``Base.metadata`` aggregates
all tables (ledger + operational + events + versions + proposals).

Importing this package is enough to make ``create_all`` / Alembic autogenerate
see every table.  Keep the imports side-effect-free (no engine creation).
"""
from app.models import (
    acquisition,  # noqa: F401
    company_study,  # noqa: F401
    event_research,  # noqa: F401
    events,  # noqa: F401
    fund_disclosure_sync,  # noqa: F401
    operational,  # noqa: F401
    proposals,  # noqa: F401
    research_expression,  # noqa: F401
    research_gateway,  # noqa: F401
    research_monitor,  # noqa: F401
    research_preparation,  # noqa: F401
    research_protocol,  # noqa: F401
    research_team,  # noqa: F401
    source_governance,  # noqa: F401
    versions,  # noqa: F401
)
from app.models.ledger import Base  # noqa: F401
from app.underwriting.persistence import models as underwriting  # noqa: F401
from app.underwriting.persistence import (
    research_models as underwriting_research,  # noqa: F401
)

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
    "research_preparation",
    "research_expression",
    "research_gateway",
    "source_governance",
    "underwriting",
    "underwriting_research",
    "versions",
    "ledger",
]
