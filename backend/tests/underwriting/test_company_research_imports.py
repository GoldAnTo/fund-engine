from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


def test_company_research_domain_imports_in_a_clean_python_process() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.underwriting.domain.company_research",
        ],
        cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_company_research_repository_barrel_exports_in_a_clean_python_process() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    script = """
from app.underwriting.persistence import (
    CompanyResearchIntegrityError,
    CompanyResearchRepository,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError as ConcreteIntegrityError,
    CompanyResearchRepository as ConcreteRepository,
)
assert CompanyResearchRepository is ConcreteRepository
assert CompanyResearchIntegrityError is ConcreteIntegrityError
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_company_research_builder_reexports_domain_contracts_in_a_clean_process() -> (
    None
):
    backend_root = Path(__file__).resolve().parents[2]
    script = """
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchModelTemplate as DomainModelTemplate,
    ScenarioAssumption as DomainScenarioAssumption,
    StrategyAssumptionSet as DomainStrategyAssumptionSet,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelTemplate,
    ScenarioAssumption,
    StrategyAssumptionSet,
)
assert CompanyResearchModelTemplate is DomainModelTemplate
assert ScenarioAssumption is DomainScenarioAssumption
assert StrategyAssumptionSet is DomainStrategyAssumptionSet
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env={**os.environ, "PYTHONPATH": str(backend_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_company_research_builder_does_not_redeclare_domain_contracts() -> None:
    builder = (
        Path(__file__).resolve().parents[2]
        / "app/underwriting/services/company_research_model_builder.py"
    )
    contract_names = {
        "CompanyResearchDriverBinding",
        "CompanyResearchMetricClassification",
        "CompanyResearchModelModule",
        "CompanyResearchModelTemplate",
        "CompanyResearchOperatingBaselineRequirement",
        "CompanyResearchOperatingDriverBinding",
        "CompanyResearchScenarioMechanism",
        "ScenarioAssumption",
        "StrategyAssumptionSet",
    }
    declarations = {
        node.name
        for node in ast.parse(builder.read_text()).body
        if isinstance(node, ast.ClassDef)
    }

    assert declarations.isdisjoint(contract_names)
