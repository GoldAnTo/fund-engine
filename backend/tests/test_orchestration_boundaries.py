"""Executable import boundaries for the event-research orchestration mainline."""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"

ORCHESTRATION_MODULES = (
    BACKEND_ROOT / "app/services/research_orchestration.py",
    BACKEND_ROOT / "app/services/research_acquisition.py",
    BACKEND_ROOT / "app/services/acquisition_coverage.py",
    BACKEND_ROOT / "app/repositories/research_orchestration.py",
    BACKEND_ROOT / "app/queries/research_workflow.py",
)
FORBIDDEN_ORCHESTRATION_IMPORTS = (
    "app.acquisition.sources",
    "app.datasources.gildata",
    "app.datasources.exchanges",
    "app.repositories.acquisition",
)
FRONTEND_WORKFLOW_MODULES = (
    FRONTEND_ROOT / "src/domain/eventWorkflow.ts",
    FRONTEND_ROOT / "src/features/case/EventWorkflowOverview.tsx",
    FRONTEND_ROOT / "src/data/httpResearchAdapter.ts",
)
IMPORT_SPECIFIER = re.compile(r"(?:\bfrom\s*|\bimport\s*\(\s*)[\"']([^\"']+)[\"']")


def _python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            imports.add(node.args[0].value)
    return imports


def test_orchestration_modules_do_not_import_source_or_acquisition_repository() -> None:
    violations: list[str] = []
    for path in ORCHESTRATION_MODULES:
        assert path.is_file(), f"missing guarded orchestration module: {path}"
        for imported in sorted(_python_imports(path)):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN_ORCHESTRATION_IMPORTS
            ):
                violations.append(
                    f"{path.relative_to(REPOSITORY_ROOT)} imports {imported}"
                )
    assert violations == []


def test_frontend_workflow_modules_do_not_import_mock_adapters() -> None:
    violations: list[str] = []
    for path in FRONTEND_WORKFLOW_MODULES:
        assert path.is_file(), f"missing guarded frontend workflow module: {path}"
        source = path.read_text(encoding="utf-8")
        for specifier in IMPORT_SPECIFIER.findall(source):
            normalized = specifier.replace("\\", "/").casefold()
            if "mock" in normalized:
                violations.append(
                    f"{path.relative_to(REPOSITORY_ROOT)} imports {specifier}"
                )
    assert violations == []
