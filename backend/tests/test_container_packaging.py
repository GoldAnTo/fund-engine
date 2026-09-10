from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_runs_as_non_root_and_contains_runtime_entrypoints() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert '"uvicorn", "app.main:app"' in dockerfile
    assert "COPY alembic" in dockerfile
    assert "COPY app" in dockerfile
