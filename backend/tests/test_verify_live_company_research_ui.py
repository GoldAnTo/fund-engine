from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).parents[2]
FRONTEND = ROOT / "frontend"
PASS_LINE = (
    "PASS: default frontend completed live Alphabet company research through "
    "reviewed evidence, frozen revision replay, and verified Markdown export"
)
SENSITIVE_OUTPUT_MARKERS = (
    "ALL_PROXY",
    "Authorization",
    "BASH_ENV",
    "Bearer",
    "DATABASE_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "PYTHONPATH",
    "RESEARCH_BEARER_TOKEN",
    "RESEARCH_TENANT_TOKENS",
    "VITE_RESEARCH_CLIENT",
    "company-research.sqlite",
    "live-company-research-verifier-token",
)


def test_package_exposes_the_closed_live_company_research_command() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["verify:live-company-research"] == (
        "node scripts/with-project-node.mjs scripts/verify-live-company-research-ui.mjs"
    )
    assert package["scripts"]["test:live-company-research-support"] == (
        "node scripts/with-project-node.mjs --test "
        "scripts/live-company-research-support.test.mjs"
    )


def test_live_company_research_browser_closes_the_full_public_workflow() -> None:
    node = shutil.which("node")
    npm = shutil.which("npm")
    assert node, "Node.js is required for the live frontend verifier"
    assert npm, "npm is required for the live frontend verifier"

    tool_directories = dict.fromkeys(
        [str(Path(node).parent), str(Path(npm).parent), "/usr/bin", "/bin"]
    )
    with tempfile.TemporaryDirectory(prefix="fund-engine-live-npm-") as npm_runtime:
        env = {
            "PATH": os.pathsep.join(tool_directories),
            "PYTHON": sys.executable,
            "NPM_CONFIG_USERCONFIG": str(Path(npm_runtime) / "user-config"),
            "NPM_CONFIG_GLOBALCONFIG": str(Path(npm_runtime) / "global-config"),
            "NPM_CONFIG_CACHE": str(Path(npm_runtime) / "cache"),
        }
        for key in ("PW_BROWSER_CHANNEL", "SystemRoot", "WINDIR"):
            if key in os.environ:
                env[key] = os.environ[key]

        result = subprocess.run(
            [npm, "run", "--silent", "verify:live-company-research"],
            cwd=FRONTEND,
            env=env,
            text=True,
            capture_output=True,
            timeout=210,
            check=False,
        )

    combined_output = result.stdout + result.stderr
    if any(marker in combined_output for marker in SENSITIVE_OUTPUT_MARKERS):
        raise AssertionError("live verifier emitted sensitive output")
    if result.returncode != 0:
        raise AssertionError(
            "live company research public command failed "
            f"with exit code {result.returncode}; output redacted"
        )
    if result.stdout != f"{PASS_LINE}\n":
        raise AssertionError(
            "live verifier stdout did not contain exactly one PASS line; output redacted"
        )
    if result.stderr != "":
        raise AssertionError("live verifier stderr was not empty; output redacted")
