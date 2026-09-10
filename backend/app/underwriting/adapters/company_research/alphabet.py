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
    StrategyAssumptionValue,
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
_OPERATING_DRIVER_SPECS = (
    ("search_query_intensity", "search_and_other_ads", "query_intensity"),
    ("search_ad_monetization", "search_and_other_ads", "ad_monetization"),
    ("traffic_acquisition_cost", "search_and_other_ads", "traffic_acquisition_cost"),
    ("youtube_usage", "youtube_ads_and_subscriptions", "youtube_usage"),
    (
        "youtube_ad_monetization",
        "youtube_ads_and_subscriptions",
        "youtube_ad_monetization",
    ),
    (
        "youtube_subscription_growth",
        "youtube_ads_and_subscriptions",
        "youtube_subscription_growth",
    ),
    ("cloud_workload", "google_cloud", "cloud_workload"),
    ("cloud_revenue_growth", "google_cloud", "cloud_revenue_growth"),
    ("cloud_operating_margin", "google_cloud", "cloud_operating_margin"),
    (
        "ai_data_center_capex",
        "corporate_capital_allocation",
        "capital_expenditures",
    ),
    (
        "infrastructure_depreciation",
        "corporate_capital_allocation",
        "depreciation",
    ),
    (
        "infrastructure_opex",
        "corporate_capital_allocation",
        "infrastructure_opex",
    ),
    ("free_cash_flow", "corporate_capital_allocation", "free_cash_flow"),
    (
        "stock_based_compensation",
        "corporate_capital_allocation",
        "stock_based_compensation",
    ),
    ("share_repurchases", "corporate_capital_allocation", "share_repurchases"),
    ("dilution", "corporate_capital_allocation", "diluted_shares"),
)


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
        modules = (
            CompanyResearchModelModule(
                "search_and_other_ads",
                ("Search query volume, commercial-query mix, and ad yield",),
                ("Traffic acquisition costs and AI inference cost per query",),
                ("Serving compute required for Search and generative answers",),
            ),
            CompanyResearchModelModule(
                "youtube_ads_and_subscriptions",
                ("Watch time, ad load and yield, and paid subscription growth",),
                ("Creator revenue share, content delivery, and sales costs",),
                ("Video delivery, recommendation, and generative media compute",),
            ),
            CompanyResearchModelModule(
                "google_cloud",
                ("Cloud workload consumption, contracted backlog, and price mix",),
                ("Compute capacity utilization and Cloud operating expenses",),
                ("Accelerator, server, network, and data-center capacity",),
            ),
            CompanyResearchModelModule(
                "other_google_services",
                ("Play, devices, and consumer-subscription units and pricing",),
                ("Hardware bill of materials, distribution, and content costs",),
                ("Device inventory and consumer-service platform investment",),
            ),
            CompanyResearchModelModule(
                "other_bets",
                ("Commercial deployment and external customer revenue",),
                ("Pre-scale engineering and operating-loss intensity",),
                ("Fleet, infrastructure, and long-duration technology investment",),
            ),
            CompanyResearchModelModule(
                "corporate_capital_allocation",
                ("Consolidated revenue and cross-business cash generation",),
                ("Depreciation, infrastructure opex, SBC, and corporate costs",),
                ("AI data-center capex, repurchases, and fully diluted share count",),
            ),
        )
        revenue_metrics = tuple(
            CompanyResearchMetricClassification(key, "revenue", "revenue")
            for key in ALPHABET_BUSINESS_MODULES
        )
        operating_bindings = tuple(
            CompanyResearchOperatingDriverBinding(
                driver_key,
                module_key,
                metric_key,
                ModelInputState.REPORTED,
                None,
            )
            for driver_key, module_key, metric_key in _OPERATING_DRIVER_SPECS
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
                CompanyResearchMetricClassification(
                    "search_and_other_ads", "query_intensity", "revenue"
                ),
                CompanyResearchMetricClassification(
                    "search_and_other_ads", "ad_monetization", "revenue"
                ),
                CompanyResearchMetricClassification(
                    "search_and_other_ads", "traffic_acquisition_cost", "cost"
                ),
                CompanyResearchMetricClassification(
                    "youtube_ads_and_subscriptions", "youtube_usage", "revenue"
                ),
                CompanyResearchMetricClassification(
                    "youtube_ads_and_subscriptions",
                    "youtube_ad_monetization",
                    "revenue",
                ),
                CompanyResearchMetricClassification(
                    "youtube_ads_and_subscriptions",
                    "youtube_subscription_growth",
                    "revenue",
                ),
                CompanyResearchMetricClassification(
                    "google_cloud", "cloud_workload", "revenue"
                ),
                CompanyResearchMetricClassification(
                    "google_cloud", "cloud_revenue_growth", "revenue"
                ),
                CompanyResearchMetricClassification(
                    "google_cloud", "cloud_operating_margin", "cost"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "depreciation", "cost"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "infrastructure_opex", "cost"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "free_cash_flow", "capital"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "stock_based_compensation", "cost"
                ),
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", "share_repurchases", "capital"
                ),
            ),
            operating_driver_bindings=operating_bindings,
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
            operating_baseline_requirements=tuple(
                CompanyResearchOperatingBaselineRequirement(
                    item.driver_key, item.module_key, item.metric_key
                )
                for item in operating_bindings
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
                assumption_rationale=item.rationale,
                assumption_equation=item.equation,
            )
            for item in value.driver_paths
        )
        scenario_overrides = tuple(
            ScenarioAssumption(
                scenario_id=item.scenario_id,
                mechanism_id=item.mechanism_id,
                driver_overrides=tuple(
                    ScenarioDriverOverride(
                        driver_key=driver_key,
                        value=number.value,
                        state=ModelInputState.ASSUMPTION,
                        assumption_key=number.assumption_key,
                        rationale=number.rationale,
                        equation=number.equation,
                    )
                    for driver_key, number in item.driver_overrides
                ),
            )
            for item in value.scenario_overrides
        )
        terminal_growth = StrategyAssumptionValue(
            value=value.terminal_growth.value,
            state=ModelInputState.ASSUMPTION,
            assumption_key=value.terminal_growth.assumption_key,
            rationale=value.terminal_growth.rationale,
            equation=value.terminal_growth.equation,
        )
        return StrategyAssumptionSet.from_legacy_alphabet(
            strategy_version=value.strategy_version,
            content_hash=value.content_hash,
            first_fiscal_year=value.first_fiscal_year,
            driver_paths=driver_paths,
            scenario_overrides=scenario_overrides,
            terminal_growth=terminal_growth,
        )
