from pathlib import Path


UNDERWRITING_ROOT = Path(__file__).resolve().parents[2] / "app" / "underwriting"


def test_underwriting_is_a_separate_bounded_context() -> None:
    assert UNDERWRITING_ROOT.is_dir()

    forbidden_imports = (
        "app.domain.event_research",
        "app.models.event_research",
        "app.queries.event_research",
        "app.services.auto_research",
        "app.services.automatic_research",
        "app.services.fund_disclosure_sync",
    )
    forbidden_terms = (
        "ResearchCase",
        "EventResearch",
        "ThemeRole",
        "FundDisclosure",
        "AutomaticResearch",
    )

    for source_file in UNDERWRITING_ROOT.rglob("*.py"):
        source = source_file.read_text(encoding="utf-8")
        assert not any(import_path in source for import_path in forbidden_imports), source_file
        assert not any(term in source for term in forbidden_terms), source_file
