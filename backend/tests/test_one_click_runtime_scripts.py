import shutil
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_runtime_control_script_keeps_credentials_local_and_switches_only_app_services() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()

    assert "umask 077" in script
    assert "openssl rand -hex 32" in script
    assert "RESEARCH_TENANT_TOKENS=" in script
    assert "local-one-click" in script
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata" in script
    assert 'LEGACY_PROJECT="fund-engine-event"' in script
    assert "label=com.docker.compose.project=" in script
    for service in ("api", "frontend", "research-worker", "acquisition-worker", "scheduler"):
        assert service in script
    assert "postgres" not in script[script.index("stop_legacy_application_services"): script.index("start_one_click_runtime")]
    assert "--scale acquisition-worker=3" in script
    assert " down" in script
    assert "--volumes" not in script


def test_runtime_verifier_checks_new_stack_and_legacy_database_revision() -> None:
    script = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()

    assert "config -q" in script
    assert "http://127.0.0.1:8000/health" in script
    assert "http://127.0.0.1:8080/health" in script
    for service in ("postgres", "api", "research-worker", "acquisition-worker", "frontend"):
        assert service in script
    assert "0059" in script
    assert "0062" in script
    assert "fund-engine-event" in script


def test_init_generates_private_local_credentials_without_echoing_them(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    created = subprocess.run([script, "init"], check=True, capture_output=True, text=True)
    runtime_env = tmp_path / ".env.one-click.local"
    contents = runtime_env.read_text()

    assert created.stdout == "Created local one-click runtime environment.\n"
    assert stat.S_IMODE(runtime_env.stat().st_mode) == 0o600
    assert "RESEARCH_TENANT_TOKENS={\"" in contents
    assert "\":\"local-one-click\"}" in contents
    assert "ONE_CLICK_POSTGRES_PASSWORD=" in contents
    assert "RESEARCH_BEARER_TOKEN=" in contents
    assert "ONE_CLICK_POSTGRES_PASSWORD=" not in created.stdout
    assert "RESEARCH_BEARER_TOKEN=" not in created.stdout

    existing = subprocess.run([script, "init"], check=True, capture_output=True, text=True)
    assert existing.stdout == "One-click runtime environment already exists.\n"
