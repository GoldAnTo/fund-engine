"""Case collaborator management contracts with server-owned acting identity."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model


CaseAccessRoleDTO = Literal["owner", "editor", "reviewer", "viewer"]


class GrantCaseAccessRequest(V1Model):
    target_user_id: uuid.UUID
    role: CaseAccessRoleDTO
    reason: str = Field(min_length=1, max_length=2_000)


class ChangeCaseAccessRoleRequest(V1Model):
    role: CaseAccessRoleDTO
    reason: str = Field(min_length=1, max_length=2_000)


class RevokeCaseAccessRequest(V1Model):
    reason: str = Field(min_length=1, max_length=2_000)


class CaseAccessGrantDTO(V1Model):
    user_id: uuid.UUID
    display_name: str
    normalized_email: str | None
    role: CaseAccessRoleDTO
    reason: str | None


class CaseAccessGrantListDTO(V1Model):
    items: list[CaseAccessGrantDTO]
