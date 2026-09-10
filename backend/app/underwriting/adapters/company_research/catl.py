"""CATL-specific business vocabulary and governed candidate assumptions."""

from __future__ import annotations

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchModule,
    CompanyResearchValidationError,
    DriverInput,
    ModelInputState,
    ScenarioDriverOverride,
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
    SensitivityAssumption,
    StrategyAssumptionSet,
    StrategyAssumptionValue,
)
from app.underwriting.fixtures.catl_answerable_case import (
    CatlStrategyAssumptionBundle,
)

CATL_COMPANY_EXTERNAL_KEY = "CN:300750:COMPANY"
CATL_SECURITY_EXTERNAL_KEY = "SZSE:300750"
CATL_BUSINESS_MODULES = (
    "power_battery",
    "energy_storage",
    "battery_materials_and_recycling",
    "mineral_resources",
    "corporate_capital_allocation",
)
_MODULE_LABELS = {
    "power_battery": "Power Battery Systems",
    "energy_storage": "Energy Storage Systems",
    "battery_materials_and_recycling": "Battery Materials and Recycling",
    "mineral_resources": "Mineral Resources",
    "corporate_capital_allocation": "Corporate Capital Allocation",
}
_OPERATING_DRIVERS = (
    ("power_battery_revenue", "power_battery", "revenue"),
    ("power_battery_volume", "power_battery", "volume_gwh"),
    ("energy_storage_revenue", "energy_storage", "revenue"),
    ("energy_storage_volume", "energy_storage", "volume_gwh"),
    (
        "materials_recycling_revenue",
        "battery_materials_and_recycling",
        "revenue",
    ),
    ("mineral_resources_revenue", "mineral_resources", "revenue"),
    ("consolidated_revenue", "corporate_capital_allocation", "revenue"),
    (
        "consolidated_operating_profit",
        "corporate_capital_allocation",
        "operating_profit",
    ),
    (
        "historical_depreciation",
        "corporate_capital_allocation",
        "depreciation",
    ),
    ("historical_capex", "corporate_capital_allocation", "capex"),
    (
        "historical_working_capital_change",
        "corporate_capital_allocation",
        "working_capital_change",
    ),
)


class CatlCompanyResearchAdapter:
    """Own CATL's vocabulary and accept only its exact company identity."""

    def supports(self, company_external_key: str) -> bool:
        return company_external_key == CATL_COMPANY_EXTERNAL_KEY

    def business_modules(
        self, company_external_key: str
    ) -> tuple[CompanyResearchModule, ...]:
        if not self.supports(company_external_key):
            raise CompanyResearchValidationError(
                "CATL adapter does not support company external key"
            )
        return tuple(
            CompanyResearchModule(key=key, label=_MODULE_LABELS[key])
            for key in CATL_BUSINESS_MODULES
        )

    def validate_source_modules(
        self,
        company_external_key: str,
        modules: tuple[CompanyResearchModule, ...],
    ) -> tuple[CompanyResearchModule, ...]:
        if modules != self.business_modules(company_external_key):
            raise CompanyResearchValidationError(
                "CATL source fixture business modules do not match the adapter"
            )
        return modules

    def model_template(self) -> CompanyResearchModelTemplate:
        modules = (
            CompanyResearchModelModule(
                "power_battery",
                ("Battery-system shipment volume and realized revenue per GWh",),
                ("Cell materials, manufacturing yield, warranty, and logistics",),
                ("Cell and pack capacity, overseas localization, and R&D",),
            ),
            CompanyResearchModelModule(
                "energy_storage",
                ("Storage-system shipment volume, mix, and realized pricing",),
                ("Cell input, integration, warranty, and delivery costs",),
                ("Dedicated storage products, integration, and service capacity",),
            ),
            CompanyResearchModelModule(
                "battery_materials_and_recycling",
                ("Recovered-material volume and battery-material realizations",),
                ("Feedstock acquisition, processing, and recovery yield",),
                ("Recycling throughput and material recovery capacity",),
            ),
            CompanyResearchModelModule(
                "mineral_resources",
                ("Mineral output, price, and attributable economic interest",),
                ("Extraction, processing, transport, and development costs",),
                ("Mine development and long-duration resource investment",),
            ),
            CompanyResearchModelModule(
                "corporate_capital_allocation",
                ("Consolidated revenue and cross-business cash generation",),
                ("Operating expenses, tax, depreciation, and working capital",),
                ("Cash capex, balance-sheet liquidity, debt, and share count",),
            ),
        )
        metrics = (
            CompanyResearchMetricClassification("power_battery", "revenue", "revenue"),
            CompanyResearchMetricClassification(
                "power_battery", "volume_gwh", "revenue"
            ),
            CompanyResearchMetricClassification("energy_storage", "revenue", "revenue"),
            CompanyResearchMetricClassification(
                "energy_storage", "volume_gwh", "revenue"
            ),
            CompanyResearchMetricClassification(
                "battery_materials_and_recycling", "revenue", "revenue"
            ),
            CompanyResearchMetricClassification(
                "mineral_resources", "revenue", "revenue"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "revenue", "revenue"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "revenue_yoy_change", "revenue"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "operating_profit", "cost"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "depreciation", "cost"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "capex", "capital"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "working_capital_change", "capital"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "profit_before_tax", "cost"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "income_tax_expense", "cost"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "cash_tax_rate", "cost"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation",
                "inventory_cash_flow_effect",
                "capital",
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation",
                "operating_receivables_cash_flow_effect",
                "capital",
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation",
                "operating_payables_cash_flow_effect",
                "capital",
            ),
            *(
                CompanyResearchMetricClassification(
                    "corporate_capital_allocation", metric, "capital"
                )
                for metric in (
                    "cash",
                    "short_term_borrowings",
                    "current_portion_long_term_debt",
                    "long_term_borrowings",
                    "bonds_payable",
                    "lease_liabilities",
                    "debt",
                    "basic_shares",
                )
            ),
        )
        operating = tuple(
            CompanyResearchOperatingDriverBinding(
                driver_key,
                module_key,
                metric_key,
                ModelInputState.REPORTED,
                None,
            )
            for driver_key, module_key, metric_key in _OPERATING_DRIVERS
        )
        return CompanyResearchModelTemplate(
            template_version="catl-company-model.v1",
            company_external_key=CATL_COMPANY_EXTERNAL_KEY,
            security_external_keys=(CATL_SECURITY_EXTERNAL_KEY,),
            modules=modules,
            metric_classifications=metrics,
            operating_driver_bindings=operating,
            financial_driver_ownership=(
                CompanyResearchDriverBinding("revenue", "corporate_capital_allocation"),
                CompanyResearchDriverBinding(
                    "operating_margin", "corporate_capital_allocation"
                ),
                CompanyResearchDriverBinding(
                    "cash_tax_rate", "corporate_capital_allocation"
                ),
                CompanyResearchDriverBinding(
                    "depreciation", "corporate_capital_allocation"
                ),
                CompanyResearchDriverBinding("capex", "corporate_capital_allocation"),
                CompanyResearchDriverBinding(
                    "working_capital_change", "corporate_capital_allocation"
                ),
            ),
            operating_baseline_requirements=tuple(
                CompanyResearchOperatingBaselineRequirement(
                    item.driver_key, item.module_key, item.metric_key
                )
                for item in operating
            ),
            scenario_mechanisms=(
                CompanyResearchScenarioMechanism(
                    "base", "volume_growth_and_margin_normalization"
                ),
                CompanyResearchScenarioMechanism(
                    "bull", "faster_volume_growth_and_utilization"
                ),
                CompanyResearchScenarioMechanism(
                    "bear", "price_pressure_and_lower_utilization"
                ),
            ),
        )

    def strategy_assumptions(
        self,
        value: CatlStrategyAssumptionBundle | None,
    ) -> StrategyAssumptionSet:
        if type(value) is not CatlStrategyAssumptionBundle:
            raise ValidationError("CATL strategy assumptions are missing")
        driver_paths = tuple(
            DriverInput(
                driver_key=item.driver_key,
                state=ModelInputState.ASSUMPTION,
                values=item.values,
                source_refs=(),
                assumption_key=item.assumption_key,
                assumption_rationale=item.rationale,
                assumption_equation=item.equation,
            )
            for item in value.driver_paths
        )
        scenarios = tuple(
            ScenarioAssumption(
                scenario_id=item.scenario_id,
                mechanism_id=item.mechanism_id,
                driver_overrides=tuple(
                    ScenarioDriverOverride(
                        driver_key=key,
                        value=number.value,
                        state=ModelInputState.ASSUMPTION,
                        assumption_key=number.assumption_key,
                        rationale=number.rationale,
                        equation=number.equation,
                    )
                    for key, number in item.driver_overrides
                ),
            )
            for item in value.scenario_overrides
        )
        terminal = StrategyAssumptionValue(
            value=value.terminal_growth.value,
            state=ModelInputState.ASSUMPTION,
            assumption_key=value.terminal_growth.assumption_key,
            rationale=value.terminal_growth.rationale,
            equation=value.terminal_growth.equation,
        )
        sensitivity = tuple(
            SensitivityAssumption(
                variable_key=item.variable_key,
                low=StrategyAssumptionValue(
                    value=item.low.value,
                    state=ModelInputState.ASSUMPTION,
                    assumption_key=item.low.assumption_key,
                    rationale=item.low.rationale,
                    equation=item.low.equation,
                ),
                high=StrategyAssumptionValue(
                    value=item.high.value,
                    state=ModelInputState.ASSUMPTION,
                    assumption_key=item.high.assumption_key,
                    rationale=item.high.rationale,
                    equation=item.high.equation,
                ),
            )
            for item in value.sensitivity_assumptions
        )
        return StrategyAssumptionSet(
            strategy_version=value.strategy_version,
            content_hash=value.content_hash,
            first_fiscal_year=value.first_fiscal_year,
            driver_paths=driver_paths,
            scenario_overrides=scenarios,
            terminal_growth=terminal,
            sensitivity_assumptions=sensitivity,
        )


__all__ = [
    "CATL_BUSINESS_MODULES",
    "CATL_COMPANY_EXTERNAL_KEY",
    "CATL_SECURITY_EXTERNAL_KEY",
    "CatlCompanyResearchAdapter",
]
