import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.one_click_stability import connection_cap, stable_snapshot, validate_snapshot


SERVICES = ["postgres", "api", "research-worker", "acquisition-worker", "company-research-worker", "frontend"]


def inspect_item(service, suffix="a", *, status="running", health="healthy", oom=False, restart=0):
    ident = hashlib.sha256(f"{service}-{suffix}".encode()).hexdigest()
    return {
        "Id": ident, "Name": f"/{service}-{suffix}",
        "Config": {"Labels": {"com.docker.compose.service": service, "com.docker.compose.project": "fund-engine", "com.docker.compose.version": "2.24"}},
        "State": {"Status": status, "Health": {"Status": health}, "OOMKilled": oom},
        "RestartCount": restart,
    }


def snapshot_payload():
    return [inspect_item(service) for service in SERVICES] + [
        inspect_item("migrate"), inspect_item("file-store-init")
    ]


def test_validate_exact_healthy_six_service_snapshot():
    expected = {service: 1 for service in SERVICES} | {"acquisition-worker": 2}
    payload = snapshot_payload() + [inspect_item("acquisition-worker", "b")]
    actual = validate_snapshot(payload, expected)
    assert set(actual) == set(SERVICES)
    assert all(len(actual[s]) == (2 if s == "acquisition-worker" else 1) for s in SERVICES)
    assert all(set(item) == {"id", "restart_count"} for items in actual.values() for item in items)


def test_validate_acquisition_missing_and_extra_replicas():
    expected = {service: 1 for service in SERVICES} | {"acquisition-worker": 2}
    payload = snapshot_payload() + [inspect_item("acquisition-worker", "b")]
    with pytest.raises(ValueError, match="acquisition-worker: expected 3 containers, got 2"):
        validate_snapshot(payload, expected | {"acquisition-worker": 3})
    with pytest.raises(ValueError, match="acquisition-worker: expected 1 containers, got 2"):
        validate_snapshot(payload, {service: 1 for service in SERVICES})


@pytest.mark.parametrize("change, message", [
    (lambda p: p.pop(), "api: expected 2 containers, got 1"),
    (lambda p: p.append(inspect_item("api", "b")), "api: expected 1 containers, got 2"),
])
def test_validate_replica_counts(change, message):
    payload = snapshot_payload(); change(payload)
    with pytest.raises(ValueError, match=message):
        validate_snapshot(payload, {service: 1 for service in SERVICES} | ({"api": 2} if "2 containers" in message else {}))


@pytest.mark.parametrize("field, value, message", [
    ("status", "exited", "container is not running"),
    ("health", "unhealthy", "container is not healthy"),
    ("oom", True, "container reports OOMKilled"),
    ("health", None, "container is not healthy"),
    ("oom", None, "container reports OOMKilled"),
])
def test_validate_runtime_state(field, value, message):
    item = inspect_item("api")
    if field == "status": item["State"]["Status"] = value
    elif field == "health": item["State"]["Health"]["Status"] = value
    else: item["State"]["OOMKilled"] = value
    with pytest.raises(ValueError, match=message): validate_snapshot([item], {"api": 1})


def test_validate_identity_and_restart_count():
    item = inspect_item("api"); item["Id"] = "ABC"
    with pytest.raises(ValueError, match="container identity is malformed"): validate_snapshot([item], {"api": 1})
    item = inspect_item("api"); item["RestartCount"] = "0"
    with pytest.raises(ValueError, match="restart count is malformed"): validate_snapshot([item], {"api": 1})


@pytest.mark.parametrize("payload", [None, {}, [1], [{"Config": 1}], [{"Config": {"Labels": 1}}]])
def test_validate_malformed_shapes_are_bounded(payload):
    with pytest.raises(ValueError) as exc: validate_snapshot(payload, {"api": 1})
    assert "Traceback" not in str(exc.value) and (payload is None or "secret" not in str(exc.value))


@pytest.mark.parametrize("label", [None, [], {}, ""])
def test_validate_bad_service_labels_are_bounded(label):
    item = inspect_item("api"); item["Config"]["Labels"]["com.docker.compose.service"] = label
    with pytest.raises(ValueError): validate_snapshot([item], {"api": 1})


@pytest.mark.parametrize("restart", [None, "0", True, -1])
def test_validate_bad_restart_count(restart):
    item = inspect_item("api"); item["RestartCount"] = restart
    with pytest.raises(ValueError, match="restart count is malformed"): validate_snapshot([item], {"api": 1})


def test_stable_snapshot_success_is_silent():
    assert stable_snapshot({"api": [{"id": "a" * 64, "restart_count": 1}]}, {"api": [{"id": "a" * 64, "restart_count": 1}]}) is None


@pytest.mark.parametrize("bad", [None, [], {"api": {}}, {"api": [1]}, {"api": [{"id": "x"}]}, {"api": [{"id": "a" * 64, "restart_count": "0"}]}])
def test_stable_snapshot_malformed_is_bounded(bad):
    with pytest.raises(ValueError): stable_snapshot(bad, {})


def test_stable_snapshot_rejects_identity_and_restart_changes():
    first, second = "a" * 64, "b" * 64
    baseline = {"api": [{"id": first, "restart_count": 0}]}
    with pytest.raises(ValueError, match="api: container identity changed"): stable_snapshot(baseline, {"api": [{"id": second, "restart_count": 0}]})
    with pytest.raises(ValueError, match="api: restart count changed"): stable_snapshot(baseline, {"api": [{"id": first, "restart_count": 1}]})


def test_connection_cap_examples_and_invalid_inputs():
    assert connection_cap(1, 2, 2) == 21
    assert connection_cap(4, 2, 2) == 33
    assert connection_cap(1, 4, 1) == 25
    for values in [(0, 1, 0), (1, 0, 0), (1, 1, -1), (True, 1, 0), (1.0, 1, 0), ("1", 1, 0)]:
        with pytest.raises(ValueError, match="connection-cap inputs are invalid"): connection_cap(*values)


def test_cli_snapshot_round_trip_and_errors(tmp_path):
    root = Path(__file__).parents[2]; script = root / "scripts" / "one_click_stability.py"
    payload = json.dumps([inspect_item("api")])
    args = [sys.executable, str(script), "snapshot", "--expect", "api=1"]
    result = subprocess.run(args, input=payload, text=True, capture_output=True)
    assert result.returncode == 0; assert json.loads(result.stdout)["api"][0]["restart_count"] == 0
    base = tmp_path / "base.json"; base.write_text(result.stdout)
    cur = tmp_path / "cur.json"; cur.write_text(result.stdout)
    compared = subprocess.run([sys.executable, str(script), "compare", str(base), str(cur)], capture_output=True, text=True)
    assert compared.returncode == 0 and compared.stdout == ""
    bad = subprocess.run([sys.executable, str(script), "connection-cap", "--replicas", "0", "--pool-size", "1", "--max-overflow", "0"], capture_output=True, text=True)
    assert bad.returncode == 2 and "Traceback" not in bad.stderr
