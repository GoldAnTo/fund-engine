"""Pure validation helpers and CLI for one-click runtime stability checks."""

from __future__ import annotations

import argparse
import json
import re
import sys


_ID = re.compile(r"^[0-9a-f]{64}$")
MONITORED_SERVICES = frozenset(
    {
        "postgres",
        "api",
        "research-worker",
        "acquisition-worker",
        "company-research-worker",
        "frontend",
    }
)


def connection_cap(replicas: int, pool_size: int, max_overflow: int) -> int:
    if (
        not isinstance(replicas, int)
        or isinstance(replicas, bool)
        or not isinstance(pool_size, int)
        or isinstance(pool_size, bool)
        or not isinstance(max_overflow, int)
        or isinstance(max_overflow, bool)
        or replicas < 1
        or pool_size < 1
        or max_overflow < 0
    ):
        raise ValueError("connection-cap inputs are invalid")
    return (3 + replicas) * (pool_size + max_overflow) + 5


def validate_snapshot(
    payload: object, expected_counts: object
) -> dict[str, list[dict[str, int | str]]]:
    if not isinstance(payload, list):
        raise ValueError("snapshot payload must be a list")
    if (
        not isinstance(expected_counts, dict)
        or not expected_counts
        or any(
            not isinstance(k, str)
            or k not in MONITORED_SERVICES
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v < 1
            for k, v in expected_counts.items()
        )
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
        if not isinstance(service, str) or not service:
            raise ValueError("container service label is malformed")
        if service not in expected_counts:
            continue
        if not isinstance(item.get("State"), dict):
            raise ValueError("container state is malformed")
        grouped[service].append(item)

    result = {}
    all_ids: set[str] = set()
    for service, expected in expected_counts.items():
        items = grouped[service]
        if len(items) != expected:
            raise ValueError(
                f"{service}: expected {expected} containers, got {len(items)}"
            )
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
            if ident in all_ids:
                raise ValueError("duplicate container identity")
            all_ids.add(ident)
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
    if not value:
        raise ValueError("stability snapshot is malformed")
    all_ids: set[str] = set()
    for service, entries in value.items():
        if (
            not isinstance(service, str)
            or service not in MONITORED_SERVICES
            or not isinstance(entries, list)
            or not entries
        ):
            raise ValueError("stability snapshot is malformed")
        normalized = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "restart_count"}:
                raise ValueError("stability snapshot is malformed")
            ident, restart = entry["id"], entry["restart_count"]
            if (
                not isinstance(ident, str)
                or not _ID.fullmatch(ident)
                or not isinstance(restart, int)
                or isinstance(restart, bool)
                or restart < 0
            ):
                raise ValueError("stability snapshot is malformed")
            if ident in all_ids:
                raise ValueError("stability snapshot is malformed")
            all_ids.add(ident)
            normalized.append((ident, restart))
        checked[service] = sorted(normalized)
    return checked


def stable_snapshot(baseline: object, current: object) -> None:
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
        if (
            not service
            or not re.fullmatch(r"[1-9][0-9]*", count)
            or service in result
            or len(count) > 10
            or (len(count) == 10 and count > "2147483647")
        ):
            raise ValueError("--expect value is malformed")
        result[service] = int(count)
    return result


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("JSON file is unreadable or invalid") from None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="one_click_stability")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--expect", action="append", required=True)
    comp = sub.add_parser("compare")
    comp.add_argument("baseline")
    comp.add_argument("current")
    cap = sub.add_parser("connection-cap")
    cap.add_argument("--replicas", required=True)
    cap.add_argument("--pool-size", required=True)
    cap.add_argument("--max-overflow", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "snapshot":
            expected = _parse_expect(args.expect)
            try:
                payload = json.load(sys.stdin)
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError("JSON input is invalid") from None
            output = validate_snapshot(payload, expected)
            sys.stdout.write(
                json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n"
            )
        elif args.command == "compare":
            stable_snapshot(_load_json(args.baseline), _load_json(args.current))
        else:
            values = []
            for raw in (args.replicas, args.pool_size, args.max_overflow):
                if not isinstance(raw, str) or not re.fullmatch(
                    r"(?:0|[1-9][0-9]*|-([1-9][0-9]*))", raw
                ):
                    raise ValueError("connection-cap inputs are invalid")
                digits = raw[1:] if raw.startswith("-") else raw
                if len(digits) > 10 or (len(digits) == 10 and digits > "2147483647"):
                    raise ValueError("connection-cap inputs are invalid")
                try:
                    values.append(int(raw))
                except (ValueError, OverflowError):
                    raise ValueError("connection-cap inputs are invalid") from None
            print(connection_cap(*values))
        return 0
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
