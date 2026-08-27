"""Stable persistence exports without application-service dependencies.

The company-research repository is intentionally imported from its concrete
module: eagerly loading it here would make ORM model registration depend on
the application-service graph while ``app.models`` is still initializing.
"""

from typing import TYPE_CHECKING

from app.underwriting.persistence.company_research_models import (
    COMPANY_RESEARCH_ARTIFACT_KINDS,
    COMPANY_RESEARCH_PREPARATION_STATUSES,
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingHistoricalBasis,
    UnderwritingLedgerEntry,
    UnderwritingMandateVersion,
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingObjectIdentityVersion,
    UnderwritingPriceSnapshot,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchAssessmentVersion,
    UnderwritingResearchObjectAlias,
    UnderwritingResearchObjectSearchTerm,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingRevisionBoundary,
    UnderwritingRevisionManifest,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.repository import (
    StaleParentError,
    UnderwritingRepository,
)
from app.underwriting.persistence.research_models import (
    UnderwritingCompanyExposureVersion,
    UnderwritingEarningsEngineVersion,
    UnderwritingEvidenceCandidateDossierVersion,
    UnderwritingEvidenceCandidateReviewVersion,
    UnderwritingFalsifierVersion,
    UnderwritingForecastInputVersion,
    UnderwritingIndustryScenarioVersion,
    UnderwritingIndustryStateVersion,
    UnderwritingMechanismPackVersion,
    UnderwritingMetricDefinitionVersion,
    UnderwritingMetricObservation,
    UnderwritingSourceManifestVersion,
)
from app.underwriting.persistence.research_repository import (
    UnderwritingResearchRepository,
)

if TYPE_CHECKING:
    from app.underwriting.persistence.company_research_repository import (
        CompanyResearchIntegrityError,
        CompanyResearchRepository,
    )


def __getattr__(name: str) -> object:
    """Load compatibility repository exports only when explicitly requested."""
    if name not in {"CompanyResearchIntegrityError", "CompanyResearchRepository"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from app.underwriting.persistence.company_research_repository import (
        CompanyResearchIntegrityError,
        CompanyResearchRepository,
    )

    exports = {
        "CompanyResearchIntegrityError": CompanyResearchIntegrityError,
        "CompanyResearchRepository": CompanyResearchRepository,
    }
    globals().update(exports)
    return exports[name]

__all__ = [
    "UnderwritingAnswerabilityEvaluation",
    "UnderwritingHistoricalBasis",
    "UnderwritingLedgerEntry",
    "UnderwritingMandateVersion",
    "UnderwritingObjectRelation",
    "UnderwritingResearchObject",
    "UnderwritingResearchVersion",
    "UnderwritingObjectIdentityVersion",
    "UnderwritingResearchObjectAlias",
    "UnderwritingResearchObjectSearchTerm",
    "UnderwritingResearchProject",
    "UnderwritingResearchProjectSecurity",
    "UnderwritingResearchScopeVersion",
    "UnderwritingResearchAgendaVersion",
    "UnderwritingPriceSnapshot",
    "UnderwritingFXSnapshot",
    "UnderwritingCapitalStructureSnapshot",
    "UnderwritingSecurityRightsVersion",
    "UnderwritingResearchAssessmentVersion",
    "UnderwritingWorkspaceDraft",
    "UnderwritingRevisionBoundary",
    "UnderwritingRevisionManifest",
    "UnderwritingSourceManifestVersion",
    "UnderwritingMetricDefinitionVersion",
    "UnderwritingMetricObservation",
    "UnderwritingMechanismPackVersion",
    "UnderwritingIndustryStateVersion",
    "UnderwritingIndustryScenarioVersion",
    "UnderwritingCompanyExposureVersion",
    "UnderwritingEarningsEngineVersion",
    "UnderwritingEvidenceCandidateDossierVersion",
    "UnderwritingEvidenceCandidateReviewVersion",
    "UnderwritingForecastInputVersion",
    "UnderwritingFalsifierVersion",
    "StaleParentError",
    "UnderwritingRepository",
    "UnderwritingResearchRepository",
    "COMPANY_RESEARCH_ARTIFACT_KINDS",
    "COMPANY_RESEARCH_PREPARATION_STATUSES",
    "CompanyResearchArtifactVersion",
    "CompanyResearchEvent",
    "CompanyResearchPreparation",
    "CompanyResearchIntegrityError",
    "CompanyResearchRepository",
]
