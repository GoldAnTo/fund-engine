"""Alphabet-specific business reading modules, without research data or decisions."""

from __future__ import annotations

from app.underwriting.domain.company_research import (
    CompanyResearchModule,
    CompanyResearchValidationError,
)


ALPHABET_COMPANY_EXTERNAL_KEY = "US:ALPHABET:COMPANY"
ALPHABET_BUSINESS_MODULES = (
    "search_and_other_ads",
    "youtube_ads_and_subscriptions",
    "google_cloud",
    "other_google_services",
    "other_bets",
    "corporate_capital_allocation",
)
_EXPECTED_ALPHABET_BUSINESS_MODULES = ALPHABET_BUSINESS_MODULES
_ALPHABET_MODULE_LABELS = {
    "search_and_other_ads": "Search and Other Ads",
    "youtube_ads_and_subscriptions": "YouTube Ads and Subscriptions",
    "google_cloud": "Google Cloud",
    "other_google_services": "Other Google Services",
    "other_bets": "Other Bets",
    "corporate_capital_allocation": "Corporate Capital Allocation",
}


class AlphabetCompanyResearchAdapter:
    """Own Alphabet's business vocabulary and recognize only its company key."""

    def supports(self, company_external_key: str) -> bool:
        return company_external_key == ALPHABET_COMPANY_EXTERNAL_KEY

    def business_modules(
        self, company_external_key: str
    ) -> tuple[CompanyResearchModule, ...]:
        if not self.supports(company_external_key):
            raise CompanyResearchValidationError(
                "Alphabet adapter does not support company external key"
            )
        if ALPHABET_BUSINESS_MODULES != _EXPECTED_ALPHABET_BUSINESS_MODULES:
            raise CompanyResearchValidationError(
                "Alphabet business modules have been altered"
            )
        if len(set(ALPHABET_BUSINESS_MODULES)) != len(ALPHABET_BUSINESS_MODULES):
            raise CompanyResearchValidationError(
                "Alphabet business modules must be unique"
            )
        return tuple(
            CompanyResearchModule(key=key, label=_ALPHABET_MODULE_LABELS[key])
            for key in ALPHABET_BUSINESS_MODULES
        )

    def validate_source_modules(
        self,
        company_external_key: str,
        modules: tuple[CompanyResearchModule, ...],
    ) -> tuple[CompanyResearchModule, ...]:
        """Reject a bundled source fixture whose business vocabulary drifted."""
        expected = self.business_modules(company_external_key)
        if modules != expected:
            raise CompanyResearchValidationError(
                "Alphabet source fixture business modules do not match the adapter"
            )
        return modules
