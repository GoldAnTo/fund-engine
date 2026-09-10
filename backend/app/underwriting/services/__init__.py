"""Application services for the underwriting bounded context."""

from typing import TYPE_CHECKING

from .kernel import UnderwritingKernelService, canonical_hash
from .source_freeze import FrozenSourceManifest, freeze_manifest, freeze_observations
from .source_policy import (
    AuthorizationState,
    DisplayPolicy,
    SourcePolicyDecision,
    evaluate_source_policy,
)

if TYPE_CHECKING:
    from .company_research_critical_input_confirmation import (
        CompanyResearchCriticalInputConfirmationService,
    )
    from .company_research_run import CompanyResearchRun, CompanyResearchRunService

__all__ = [
    "AuthorizationState",
    "CompanyResearchCriticalInputConfirmationService",
    "CompanyResearchRun",
    "CompanyResearchRunService",
    "DisplayPolicy",
    "FrozenSourceManifest",
    "SourcePolicyDecision",
    "UnderwritingKernelService",
    "canonical_hash",
    "evaluate_source_policy",
    "freeze_manifest",
    "freeze_observations",
]


def __getattr__(name: str):
    if name == "CompanyResearchCriticalInputConfirmationService":
        from .company_research_critical_input_confirmation import (
            CompanyResearchCriticalInputConfirmationService,
        )

        return CompanyResearchCriticalInputConfirmationService
    if name in {"CompanyResearchRun", "CompanyResearchRunService"}:
        from .company_research_run import CompanyResearchRun, CompanyResearchRunService

        return {
            "CompanyResearchRun": CompanyResearchRun,
            "CompanyResearchRunService": CompanyResearchRunService,
        }[name]
    raise AttributeError(name)
