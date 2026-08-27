"""Compatibility exports for the persistence-neutral artifact codec seam."""

from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
    MODEL_ARTIFACT_KINDS,
)

__all__ = ["CompanyResearchArtifactCodec", "MODEL_ARTIFACT_KINDS"]
