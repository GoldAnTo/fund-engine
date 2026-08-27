"""Alphabet-specific business reading modules, without research data or decisions."""

from __future__ import annotations

from app.underwriting.domain.company_research import (
    CompanyResearchModule,
    CompanyResearchValidationError,
    DriverInput,
    ModelInputState,
    ScenarioDriverOverride,
)
from app.models.ledger import ValidationError
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetStrategyAssumptionBundle,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchDriverBinding,
    CompanyResearchMetricClassification,
    CompanyResearchModelModule,
    CompanyResearchModelTemplate,
    CompanyResearchOperatingBaselineRequirement,
    CompanyResearchOperatingDriverBinding,
    CompanyResearchScenarioMechanism,
    ScenarioAssumption,
    StrategyAssumptionSet,
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

    def model_template(self) -> CompanyResearchModelTemplate:
        """Return Alphabet vocabulary and routing rules, never forecast values."""
        modules = tuple(
            CompanyResearchModelModule(
                module_key=key,
                revenue_sources=(f"Reviewed {label} revenue evidence",),
                cost_structure=(f"Reviewed {label} cost evidence or explicit gap",),
                capital_needs=(f"Reviewed {label} capital evidence or explicit gap",),
            )
            for key, label in (
                (key, _ALPHABET_MODULE_LABELS[key])
                for key in ALPHABET_BUSINESS_MODULES
            )
        )
        revenue_metrics = tuple(
            CompanyResearchMetricClassification(key, "revenue", "revenue")
            for key in ALPHABET_BUSINESS_MODULES
        )
        return CompanyResearchModelTemplate(
            template_version="alphabet-company-model.v1",
            company_external_key=ALPHABET_COMPANY_EXTERNAL_KEY,
            security_external_keys=("NASDAQ:GOOG", "NASDAQ:GOOGL"),
            modules=modules,
            metric_classifications=(
                *revenue_metrics,
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "operating_expense", "cost"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "capital_expenditures", "capital"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "diluted_shares", "capital"
                ),
            ),
            operating_driver_bindings=(
                CompanyResearchOperatingDriverBinding(
                    "search_monetization", "search_and_other_ads", "revenue",
                    ModelInputState.REPORTED, None,
                ),
                CompanyResearchOperatingDriverBinding(
                    "youtube_monetization", "youtube_ads_and_subscriptions", "revenue",
                    ModelInputState.REPORTED, None,
                ),
                CompanyResearchOperatingDriverBinding(
                    "cloud_workload_monetization", "google_cloud", "revenue",
                    ModelInputState.REPORTED, None,
                ),
                CompanyResearchOperatingDriverBinding(
                    "capital_intensity", "corporate_capital_allocation",
                    "capital_expenditures", ModelInputState.REPORTED, None,
                ),
                CompanyResearchOperatingDriverBinding(
                    "dilution", "corporate_capital_allocation", "diluted_shares",
                    ModelInputState.REPORTED, None,
                ),
            ),
            financial_driver_ownership=(
                CompanyResearchDriverBinding("revenue", "search_and_other_ads"),
                CompanyResearchDriverBinding("operating_margin", "google_cloud"),
                CompanyResearchDriverBinding(
                    "cash_tax_rate", "corporate_capital_allocation"
                ),
                CompanyResearchDriverBinding(
                    "depreciation", "corporate_capital_allocation"
                ),
                CompanyResearchDriverBinding("capex", "corporate_capital_allocation"),
                CompanyResearchDriverBinding(
                    "working_capital_change", "other_google_services"
                ),
            ),
            operating_baseline_requirements=(
                CompanyResearchOperatingBaselineRequirement(
                    "consolidated_revenue",
                    "corporate_capital_allocation",
                    "revenue",
                ),
            ),
            scenario_mechanisms=(
                CompanyResearchScenarioMechanism("base", "search_cloud_resilience"),
                CompanyResearchScenarioMechanism(
                    "bull", "ai_monetization_and_utilization"
                ),
                CompanyResearchScenarioMechanism(
                    "bear", "search_disruption_and_capital_drag"
                ),
            ),
        )

    def strategy_assumptions(
        self,
        value: AlphabetStrategyAssumptionBundle | None,
    ) -> StrategyAssumptionSet:
        """Translate one authenticated, fully explicit candidate or fail closed."""
        if type(value) is not AlphabetStrategyAssumptionBundle:
            raise ValidationError("Alphabet strategy assumptions are missing")
        driver_paths = tuple(
            DriverInput(
                driver_key=item.driver_key,
                state=ModelInputState.ASSUMPTION,
                values=item.values,
                source_refs=(),
                assumption_key=item.assumption_key,
                equation_id=None,
            )
            for item in value.driver_paths
        )
        scenario_overrides = tuple(
            ScenarioAssumption(
                scenario_id=item.scenario_id,
                mechanism_id=item.mechanism_id,
                driver_overrides=tuple(
                    ScenarioDriverOverride(driver_key, number.value)
                    for driver_key, number in item.driver_overrides
                ),
            )
            for item in value.scenario_overrides
        )
        content_hash = StrategyAssumptionSet.calculate_content_hash(
            strategy_version=value.strategy_version,
            first_fiscal_year=value.first_fiscal_year,
            driver_paths=driver_paths,
            scenario_overrides=scenario_overrides,
            terminal_growth=value.terminal_growth.value,
        )
        return StrategyAssumptionSet(
            strategy_version=value.strategy_version,
            content_hash=content_hash,
            first_fiscal_year=value.first_fiscal_year,
            driver_paths=driver_paths,
            scenario_overrides=scenario_overrides,
            terminal_growth=value.terminal_growth.value,
        )
