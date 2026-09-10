from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_live_topology_declares_every_runtime_and_migration_gate() -> None:
    base = _compose("docker-compose.yml")
    live = _compose("docker-compose.live.yml")
    services = set(base["services"]) | set(live["services"])

    assert {
        "postgres",
        "keycloak-db",
        "keycloak",
        "migrate",
        "api",
        "research-worker",
        "acquisition-worker",
        "scheduler",
    } <= services
    for service in ("api", "research-worker", "acquisition-worker", "scheduler"):
        dependency = live["services"][service]["depends_on"]["migrate"]
        assert dependency["condition"] == "service_completed_successfully"
        assert live["services"][service]["restart"] == "unless-stopped"
        assert live["services"][service]["healthcheck"]["test"]
    for service in ("postgres", "keycloak-db", "keycloak"):
        assert base["services"][service]["restart"] == "unless-stopped"


def test_live_acquisition_lease_covers_blocking_provider_and_llm_work() -> None:
    command = _compose("docker-compose.live.yml")["services"]["acquisition-worker"][
        "command"
    ]
    lease_flag = command.index("--lease-seconds")

    assert float(command[lease_flag + 1]) >= 300


def test_live_research_worker_exposes_a_real_crash_observation_window() -> None:
    command = _compose("docker-compose.live.yml")["services"]["research-worker"][
        "command"
    ]
    hold_flag = command.index("--claim-observation-seconds")

    assert float(command[hold_flag + 1]) >= 15


def test_runtime_healthcheck_budget_covers_real_probe_startup_cost() -> None:
    healthcheck = _compose("docker-compose.live.yml")["x-runtime-healthcheck"]

    assert healthcheck["timeout"] == "20s"
    assert healthcheck["interval"] == "30s"


def test_scaled_acquisition_workers_derive_unique_container_identities() -> None:
    environment = _compose("docker-compose.live.yml")["services"][
        "acquisition-worker"
    ]["environment"]

    assert "RUNTIME_INSTANCE_ID" not in environment
    assert "ACQUISITION_WORKER_INSTANCE" not in environment


def test_topology_uses_runtime_environment_and_contains_no_provider_secret() -> None:
    rendered = (ROOT / "docker-compose.live.yml").read_text(encoding="utf-8")

    assert "LLM_API_KEY: ${LLM_API_KEY" in rendered
    assert "GILDATA_TOKEN: ${GILDATA_TOKEN" in rendered
    assert "RESEARCH_TENANT_TOKENS" not in rendered
    assert "VITE_RESEARCH_BEARER_TOKEN" not in rendered
    assert "live-ui-verifier-token" not in rendered
    assert "FRONTEND_PORT" not in rendered
    assert "KEYCLOAK_PORT" not in rendered

    live = _compose("docker-compose.live.yml")["services"]
    assert "LLM_API_KEY" not in live["migrate"]["environment"]
    assert "GILDATA_TOKEN" not in live["migrate"]["environment"]
    assert "GILDATA_TOKEN" not in live["research-worker"]["environment"]
    assert "OIDC_ISSUER" not in live["research-worker"]["environment"]
    assert "OIDC_ISSUER" not in live["scheduler"]["environment"]
    assert "ACQUISITION_ENABLED_ADAPTERS" not in live["api"]["environment"]
    assert live["api"]["environment"]["LIVE_ACCEPTANCE_FAST_SCHEDULER"] == "true"
    assert live["scheduler"]["environment"]["LIVE_ACCEPTANCE_FAST_SCHEDULER"] == "true"
    assert "LIVE_ACCEPTANCE_FAST_SCHEDULER" not in live["migrate"]["environment"]


def test_operator_script_has_safe_lifecycle_commands() -> None:
    script = (ROOT / "scripts" / "research-stack.sh").read_text(encoding="utf-8")

    for command in (
        "up",
        "status",
        "logs",
        "restart-api",
        "restart-research-worker",
        "restart-acquisition-worker",
        "restart-scheduler",
        "down",
    ):
        assert command in script
    assert "down --volumes" in script
    assert "LIVE_KEYCLOAK_PASSWORD" in script
    assert "wait_for_service" in script
    assert 'docker start "${container_ids[@]}"' in script
    assert "service_completed_successfully" in (
        ROOT / "docker-compose.live.yml"
    ).read_text(encoding="utf-8")


def test_local_realm_uses_pkce_audience_tenant_claim_and_repeatable_users() -> None:
    realm = json.loads(
        (ROOT / "infra" / "keycloak" / "realm-export.json").read_text(
            encoding="utf-8"
        )
    )
    clients = {client["clientId"]: client for client in realm["clients"]}
    web = clients["fund-engine-web"]

    assert web["publicClient"] is True
    assert web["standardFlowEnabled"] is True
    assert web["directAccessGrantsEnabled"] is False
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert any(
        mapper["protocolMapper"] == "oidc-audience-mapper"
        and mapper["config"]["included.client.audience"] == "research-api"
        for mapper in web["protocolMappers"]
    )
    assert any(
        mapper["protocolMapper"] == "oidc-usermodel-attribute-mapper"
        and mapper["config"]["claim.name"] == "tenant_id"
        for mapper in web["protocolMappers"]
    )
    users = {user["username"]: user for user in realm["users"]}
    for username in ("alice", "bob"):
        assert users[username]["enabled"] is True
        assert users[username]["emailVerified"] is True
        assert users[username]["attributes"]["tenant_id"] == ["local-acceptance"]
        assert users[username]["credentials"][0]["temporary"] is False
