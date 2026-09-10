"""Write contracts must never accept authoritative acting identities."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.main import app


FORBIDDEN_ACTING_FIELDS = frozenset(
    {
        "actor",
        "reviewer",
        "reviewer_id",
        "created_by",
        "changed_by",
        "tenant_id",
        "admitted_by",
        "approved_by",
        "triggered_by",
        "requested_by",
        "recorded_by",
        "reviewed_by",
        "creator_type",
        "proposed_by",
    }
)
WRITE_METHODS = ("post", "put", "patch", "delete")


def _schema_name(reference: str) -> str:
    return reference.rsplit("/", 1)[-1]


def _walk_schema(
    schema: dict[str, Any],
    *,
    components: dict[str, Any],
    trail: tuple[str, ...] = (),
    visited: frozenset[str] = frozenset(),
) -> Iterator[tuple[str, str]]:
    reference = schema.get("$ref")
    if reference is not None:
        name = _schema_name(reference)
        if name in visited:
            return
        yield from _walk_schema(
            components[name],
            components=components,
            trail=(*trail, name),
            visited=visited | {name},
        )
        return

    for composition in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(composition, []):
            yield from _walk_schema(
                branch,
                components=components,
                trail=trail,
                visited=visited,
            )
    if "items" in schema:
        yield from _walk_schema(
            schema["items"],
            components=components,
            trail=trail,
            visited=visited,
        )
    for field, child in schema.get("properties", {}).items():
        field_path = ".".join((*trail, field))
        if field in FORBIDDEN_ACTING_FIELDS:
            yield field_path, field
        yield from _walk_schema(
            child,
            components=components,
            trail=(*trail, field),
            visited=visited,
        )


def test_write_contracts_do_not_accept_client_authored_identity() -> None:
    specification = app.openapi()
    components = specification["components"]["schemas"]
    violations: list[str] = []

    for path, path_item in specification["paths"].items():
        for method in WRITE_METHODS:
            operation = path_item.get(method)
            if operation is None:
                continue
            operation_name = operation["operationId"]
            for parameter in (*path_item.get("parameters", []), *operation.get("parameters", [])):
                if parameter.get("name") in FORBIDDEN_ACTING_FIELDS:
                    violations.append(
                        f"{method.upper()} {path} ({operation_name}): parameter {parameter['name']}"
                    )
            for media in operation.get("requestBody", {}).get("content", {}).values():
                for field_path, _ in _walk_schema(
                    media["schema"], components=components
                ):
                    violations.append(
                        f"{method.upper()} {path} ({operation_name}): {field_path}"
                    )

    assert violations == [], "Client-authored identity fields remain:\n" + "\n".join(
        sorted(set(violations))
    )
