"""Stable, non-secret identities for observable runtime processes."""
from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping


_SAFE_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def runtime_heartbeat_id(
    service: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return the heartbeat key shared by writers and health probes."""
    env = environment if environment is not None else os.environ
    instance = env.get("RUNTIME_INSTANCE_ID", socket.gethostname()).strip()
    if not instance:
        raise RuntimeError("RUNTIME_INSTANCE_ID must not be empty")
    if _SAFE_PART.fullmatch(instance) is None:
        raise RuntimeError(
            "RUNTIME_INSTANCE_ID must use letters, digits, dot, dash or underscore"
        )
    value = f"{service}:{instance}"
    if len(value) > 128:
        raise RuntimeError("runtime heartbeat identity exceeds 128 characters")
    return value
