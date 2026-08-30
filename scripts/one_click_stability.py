"""Pure validation helpers and CLI for one-click runtime stability checks."""

import argparse
import json
import re
import sys
from pathlib import Path


_ID = re.compile(r"^[0-9a-f]{64}$")


def connection_cap(replicas: int, pool_size: int, max_overflow: int) -> int:
    if replicas < 1 or pool_size < 1 or max_overflow < 0:
        raise ValueError("connection-cap inputs are invalid")
    return (3 + replicas) * (pool_size + max_overflow) + 5


def _mapping(value, message):
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def validate_snapshot(payload, expected_counts):
    if not isinstance(payload, list):
        raise ValueError("snapshot payload must be a list")
    if not isinstance(expected_counts, dict) or any(
        not isinstance(k, str) or not k or not isinstance(v, int) or isinstance(v, bool) or v < 1
        for k, v in expected_counts.items()
    ):
        raise ValueError("expected service counts are malformed")

    grouped = {service: [] for service in expected_counts}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("inspected container is malformed")
        config = item.get("Config")
        if not isinstance(config, dict):
            raise ValueError("container config is malformed")
        labels = config.get("Labels")
        if not isinstance(labels, dict):
            raise ValueError("container labels are malformed")
        service = labels.get("com.docker.compose.service")
        if service not in expected_counts:
            continue
        if not isinstance(item.get("State"), dict):
            raise ValueError("container state is malformed")
        grouped[service].append(item)

    result = {}
    for service, expected in expected_counts.items():
        items = grouped[service]
        if len(items) != expected:
            raise ValueError(f"{service}: expected {expected} containers, got {len(items)}")
        entries = []
        for item in items:
            state = item["State"]
            if state.get("Status") != "running":
                raise ValueError("container is not running")
            health = state.get("Health")
            if not isinstance(health, dict) or health.get("Status") != "healthy":
                raise ValueError("container is not healthy")
            if state.get("OOMKilled") is not False:
                raise ValueError("container reports OOMKilled")
            ident = item.get("Id")
            if not isinstance(ident, str) or not _ID.fullmatch(ident):
                raise ValueError("container identity is malformed")
            restart = item.get("RestartCount")
            if not isinstance(restart, int) or isinstance(restart, bool) or restart < 0:
                raise ValueError("restart count is malformed")
            entries.append({"id": ident, "restart_count": restart})
        result[service] = sorted(entries, key=lambda entry: entry["id"])
    return result


def _stable_map(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} snapshot is malformed")
    checked = {}
    for service, entries in value.items():
        if not isinstance(service, str) or not service or not isinstance(entries, list):
            raise ValueError("stability snapshot is malformed")
        normalized = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "restart_count"}:
                raise ValueError("stability snapshot is malformed")
            ident, restart = entry["id"], entry["restart_count"]
            if not isinstance(ident, str) or not _ID.fullmatch(ident) or not isinstance(restart, int) or isinstance(restart, bool) or restart < 0:
                raise ValueError("stability snapshot is malformed")
            normalized.append((ident, restart))
        checked[service] = sorted(normalized)
    return checked


def stable_snapshot(baseline, current):
    before, after = _stable_map(baseline, "baseline"), _stable_map(current, "current")
    if set(before) != set(after):
        raise ValueError("stability snapshot service structure is malformed")
    for service in before:
        old_ids = [ident for ident, _ in before[service]]
        new_ids = [ident for ident, _ in after[service]]
        if old_ids != new_ids:
            raise ValueError(f"{service}: container identity changed")
        if before[service] != after[service]:
            raise ValueError(f"{service}: restart count changed")


def _parse_expect(values):
    if not values:
        raise ValueError("at least one --expect is required")
    result = {}
    for raw in values:
        if not isinstance(raw, str) or raw.count("=") != 1:
            raise ValueError("--expect value is malformed")
        service, count = raw.split("=")
        if not service or service in result or not count.isdigit() or int(count) < 1 or str(int(count)) != count:
            raise ValueError("--expect value is malformed")
        result[service] = int(count)
    return result


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        raise ValueError("JSON file is unreadable or invalid") from None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="one_click_stability")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--expect", action="append", required=True)
    comp = sub.add_parser("compare")
    comp.add_argument("baseline"); comp.add_argument("current")
    cap = sub.add_parser("connection-cap")
    cap.add_argument("--replicas", type=int, required=True)
    cap.add_argument("--pool-size", type=int, required=True)
    cap.add_argument("--max-overflow", type=int, required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "snapshot":
            expected = _parse_expect(args.expect)
            payload = json.load(sys.stdin)
            output = validate_snapshot(payload, expected)
            sys.stdout.write(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n")
        elif args.command == "compare":
            stable_snapshot(_load_json(args.baseline), _load_json(args.current))
        else:
            print(connection_cap(args.replicas, args.pool_size, args.max_overflow))
        return 0
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
