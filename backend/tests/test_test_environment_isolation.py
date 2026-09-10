"""Regression coverage for pytest's provider-environment isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_conftest_forces_offline_provider_environment_before_collection() -> None:
    """Ambient live credentials must not survive loading the test bootstrap."""
    provider_names = (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TEMPERATURE",
        "LLM_SEED",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_ATTEMPTS",
        "LLM_MAX_OUTPUT_TOKENS",
        "GILDATA_MAX_ATTEMPTS",
        "GILDATA_TOKEN",
        "GILDATA_ALLOW_AI_PROCESSING",
        "GILDATA_ALLOW_DISPLAY",
    )
    test_database_url = "postgresql://test-isolation.example.invalid/test"
    neo4j_url = "bolt://test-isolation.example.invalid:7687"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "LLM_API_KEY": "sentinel-llm-key",
            "LLM_BASE_URL": "https://live-llm.example.invalid/v1",
            "LLM_MODEL": "live-model",
            "LLM_TEMPERATURE": "0.9",
            "LLM_SEED": "999",
            "LLM_TIMEOUT_SECONDS": "123",
            "LLM_MAX_ATTEMPTS": "9",
            "LLM_MAX_OUTPUT_TOKENS": "999",
            "GILDATA_MAX_ATTEMPTS": "9",
            "GILDATA_TOKEN": "sentinel-gildata-token",
            "GILDATA_ALLOW_AI_PROCESSING": "true",
            "GILDATA_ALLOW_DISPLAY": "true",
            "TEST_DATABASE_URL": test_database_url,
            "NEO4J_URL": neo4j_url,
        }
    )
    conftest_path = Path(__file__).with_name("conftest.py")
    probe = """
import json
import os
import runpy
import sys

runpy.run_path(sys.argv[1])
print(json.dumps({
    "APP_ENV": os.environ.get("APP_ENV"),
    "provider_values": {
        name: os.environ.get(name)
        for name in sys.argv[2:]
    },
    "TEST_DATABASE_URL": os.environ.get("TEST_DATABASE_URL"),
    "NEO4J_URL": os.environ.get("NEO4J_URL"),
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe, str(conftest_path), *provider_names],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    observed = json.loads(completed.stdout)

    assert observed["APP_ENV"] == "test"
    assert observed["provider_values"] == {
        name: None for name in provider_names
    }
    assert observed["TEST_DATABASE_URL"] == test_database_url
    assert observed["NEO4J_URL"] == neo4j_url
