import os
from pathlib import Path
import subprocess
import sys


def test_cli_known_provider_failure_does_not_render_sensitive_cause():
    backend_root = Path(__file__).resolve().parents[1]
    code = """
import sys
from app.scripts import run_ai_engine
from app.ai.client import LLMProviderError
def fail():
    try:
        raise ValueError('https://provider.invalid?token=sentinel-secret')
    except ValueError as exc:
        raise LLMProviderError('unsafe-wrapper-detail') from exc
run_ai_engine.main = fail
sys.exit(run_ai_engine.cli_main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=backend_root,
        env={**os.environ, "APP_ENV": "test", "DATABASE_URL": "sqlite://", "PYTHONPATH": str(backend_root)},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 1
    assert "LLM provider request failed" in result.stderr
    output = result.stdout + result.stderr
    assert "sentinel-secret" not in output
    assert "unsafe-wrapper-detail" not in output
    assert "Traceback" not in output
