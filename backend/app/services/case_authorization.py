"""Single authorization boundary for mutable and legacy research Cases.

Only an explicit grant or the trusted ``tenant_administrator`` role makes an
admitted Case visible.  Tenant membership alone is deliberately insufficient.
The service is transaction-neutral: callers own commit/rollback boundaries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from app.models.identity import CaseAccessGrant, CaseAccessRole, ResearchUser
from app.models.ledger import CaseTenantAdmission, ResearchCase
from app.repositories.outbox import emit_event
from app.security.principal import ResearchPrincipal

CasePermission = Literal["view", "edit", "review", "admin"]

_ROLE_PERMISSIONS: dict[CaseAccessRole, frozenset[CasePermission]] = {
    "owner": frozenset({"view", "edit", "review", "admin"}),
    "editor": frozenset({"view", "edit"}),
    "reviewer": frozenset({"view", "review"}),
    "viewer": frozenset({"view"}),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CaseAuthorization:
    """Successful decision returned to dependencies and service callers."""

    case_id: uuid.UUID
    tenant_id: str
    user_id: uuid.UUID
    role: CaseAccessRole | None
    permission: CasePermission


class CaseAuthorizationService:
    """Authorize Case actions and exclusively mutate Case grants."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def require(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        permission: CasePermission,
    ) -> CaseAuthorization:
        permission = self._permission(permission)
        has_any_grant = exists(
            select(CaseAccessGrant.id).where(
                CaseAccessGrant.research_case_id
                == CaseTenantAdmission.research_case_id
            ).correlate(CaseTenantAdmission)
        ).label("has_any_grant")
        row = self._session.execute(
            select(CaseTenantAdmission, CaseAccessGrant, has_any_grant)
            .outerjoin(
                CaseAccessGrant,
                and_(
                    CaseAccessGrant.research_case_id
                    == CaseTenantAdmission.research_case_id,
                    CaseAccessGrant.user_id == principal.user_id,
                ),
            )
            .where(
                CaseTenantAdmission.research_case_id == case_id,
                CaseTenantAdmission.tenant_id == principal.tenant_id,
            )
        ).one_or_none()
        if row is None:
            self._hidden()
        admission, grant, has_any_grant = row
        if (
            "tenant_administrator" in principal.roles
            and grant is None
            and not has_any_grant
            and permission in {"view", "admin"}
        ):
            return CaseAuthorization(
                case_id=case_id,
                tenant_id=admission.tenant_id,
                user_id=principal.user_id,
                role=None,
                permission=permission,
            )
        if grant is None:
            self._hidden()
        if permission not in _ROLE_PERMISSIONS[grant.role]:
            raise PermissionDeniedError(
                f"case {permission} permission is required"
            )
        return CaseAuthorization(
            case_id=case_id,
            tenant_id=admission.tenant_id,
            user_id=principal.user_id,
            role=grant.role,
            permission=permission,
        )

    def authorized_case_ids(
        self,
        principal: ResearchPrincipal,
        permission: CasePermission = "view",
    ) -> Select[tuple[uuid.UUID]]:
        """Build the Case-ID boundary for collection queries.

        Tenant administrators may inspect an admitted legacy Case only while
        it has no real grants.  Once any grant exists, administrators follow
        the same explicit role permissions as every other principal.
        """
        permission = self._permission(permission)
        admitted_case_ids = select(CaseTenantAdmission.research_case_id).where(
            CaseTenantAdmission.tenant_id == principal.tenant_id
        )
        permitted_roles = tuple(
            role
            for role, permissions in _ROLE_PERMISSIONS.items()
            if permission in permissions
        )
        explicitly_granted = (
            select(CaseAccessGrant.research_case_id)
            .join(
                CaseTenantAdmission,
                CaseTenantAdmission.research_case_id
                == CaseAccessGrant.research_case_id,
            )
            .where(
                CaseTenantAdmission.tenant_id == principal.tenant_id,
                CaseAccessGrant.user_id == principal.user_id,
                CaseAccessGrant.role.in_(permitted_roles),
            )
        )
        if "tenant_administrator" not in principal.roles:
            return explicitly_granted

        any_grant = exists(
            select(CaseAccessGrant.id).where(
                CaseAccessGrant.research_case_id
                == CaseTenantAdmission.research_case_id
            )
        )
        own_permitted_grant = exists(
            select(CaseAccessGrant.id).where(
                CaseAccessGrant.research_case_id
                == CaseTenantAdmission.research_case_id,
                CaseAccessGrant.user_id == principal.user_id,
                CaseAccessGrant.role.in_(permitted_roles),
            )
        )
        return admitted_case_ids.where(
            or_(
                and_(permission in {"view", "admin"}, ~any_grant),
                own_permitted_grant,
            )
        )

    def require_locked(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        permission: CasePermission,
    ) -> CaseAuthorization:
        """Authorize while holding the Case mutation lock until commit.

        Long-running provider operations call this after the provider returns
        and immediately before they persist output. Grant mutations acquire
        the same admission-row lock, so a committed revocation wins before
        any new domain output can be written.
        """
        permission = self._permission(permission)
        admission = self._lock_case_admission(case_id, principal)
        return self._require_for_admission(admission, principal, permission)

    def grant(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        user_id: uuid.UUID,
        role: CaseAccessRole,
        *,
        reason: str | None = None,
    ) -> CaseAccessGrant:
        role = self._role(role)
        admission = self._lock_case_admission(case_id, principal)
        self._require_for_admission(admission, principal, "admin")
        existing_grant_id = self._session.scalar(
            select(CaseAccessGrant.id)
            .where(CaseAccessGrant.research_case_id == case_id)
            .limit(1)
        )
        if existing_grant_id is None and role != "owner":
            raise ValidationFailedError("first Case grant must be owner")
        target = self._target_user(admission, user_id)
        return self._insert_grant(
            case_id=case_id,
            principal=principal,
            target=target,
            role=role,
            reason=reason,
        )

    def list_grants(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
    ) -> list[tuple[CaseAccessGrant, ResearchUser]]:
        """List explicit collaborators after proving Case admin permission."""
        self.require(case_id, principal, "admin")
        return [
            (grant, user)
            for grant, user in self._session.execute(
                select(CaseAccessGrant, ResearchUser)
                .join(ResearchUser, ResearchUser.id == CaseAccessGrant.user_id)
                .where(CaseAccessGrant.research_case_id == case_id)
                .order_by(ResearchUser.display_name, ResearchUser.id)
            )
        ]

    def grant_initial_owner_for_new_case(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        user_id: uuid.UUID,
        role: CaseAccessRole = "owner",
        *,
        reason: str | None = None,
    ) -> CaseAccessGrant:
        """Assign the creator inside the still-open new-Case transaction.

        This method is intentionally reserved for ``EventResearchService``;
        public access-management flows must call :meth:`grant` instead.
        """
        role = self._role(role)
        if role != "owner" or user_id != principal.user_id:
            raise ValidationFailedError("initial Case grant must assign its creator owner")
        admission = self._lock_case_admission(case_id, principal)
        target = self._target_user(admission, user_id)
        research_case = self._session.get(ResearchCase, case_id)
        if research_case is None or research_case.created_by != principal.actor:
            self._hidden()
        existing_count = self._session.scalar(
            select(func.count(CaseAccessGrant.id)).where(
                CaseAccessGrant.research_case_id == case_id
            )
        )
        if int(existing_count or 0) != 0:
            raise ValidationFailedError("initial Case owner already exists")
        return self._insert_grant(
            case_id=case_id,
            principal=principal,
            target=target,
            role=role,
            reason=reason,
        )

    def require_assignable_user(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        user_id: uuid.UUID,
    ) -> ResearchUser:
        """Resolve an active same-tenant user with an explicit grant to the Case."""
        self.require(case_id, principal, "edit")
        admission = self._session.scalar(
            select(CaseTenantAdmission).where(
                CaseTenantAdmission.research_case_id == case_id,
                CaseTenantAdmission.tenant_id == principal.tenant_id,
            )
        )
        if admission is None:
            self._hidden()
        target = self._target_user(admission, user_id)
        self._grant_or_404(case_id, target.id)
        return target

    def _insert_grant(
        self,
        *,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        target: ResearchUser,
        role: CaseAccessRole,
        reason: str | None,
    ) -> CaseAccessGrant:
        existing_id = self._session.scalar(
            select(CaseAccessGrant.id).where(
                CaseAccessGrant.research_case_id == case_id,
                CaseAccessGrant.user_id == target.id,
            )
        )
        if existing_id is not None:
            raise ValidationFailedError("case access grant already exists")
        now = _utcnow()
        grant = CaseAccessGrant(
            research_case_id=case_id,
            user_id=target.id,
            role=role,
            granted_by_principal_id=principal.actor,
            reason=self._reason(reason),
            created_at=now,
            updated_at=now,
        )
        self._session.add(grant)
        self._session.flush()
        self._audit(
            "case_access_granted",
            grant,
            principal,
            old_role=None,
            new_role=role,
            reason=grant.reason,
        )
        return grant

    def change_role(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        user_id: uuid.UUID,
        role: CaseAccessRole,
        *,
        reason: str | None = None,
    ) -> CaseAccessGrant:
        role = self._role(role)
        admission = self._lock_case_admission(case_id, principal)
        self._require_for_admission(admission, principal, "admin")
        self._target_user(admission, user_id, require_active=False)
        grant = self._grant_or_404(case_id, user_id)
        self._reject_last_owner_removal(
            case_id,
            grant,
            replacement_role=role,
        )
        old_role = grant.role
        grant.role = role
        grant.granted_by_principal_id = principal.actor
        grant.reason = self._reason(reason)
        grant.updated_at = _utcnow()
        self._session.flush()
        self._audit(
            "case_access_role_changed",
            grant,
            principal,
            old_role=old_role,
            new_role=role,
            reason=grant.reason,
        )
        return grant

    def revoke(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
        user_id: uuid.UUID,
        *,
        reason: str | None = None,
    ) -> None:
        admission = self._lock_case_admission(case_id, principal)
        self._require_for_admission(admission, principal, "admin")
        self._target_user(admission, user_id, require_active=False)
        grant = self._grant_or_404(case_id, user_id)
        self._reject_last_owner_removal(
            case_id,
            grant,
            replacement_role=None,
        )
        old_role = grant.role
        normalized_reason = self._reason(reason)
        self._audit(
            "case_access_revoked",
            grant,
            principal,
            old_role=old_role,
            new_role=None,
            reason=normalized_reason,
        )
        self._session.delete(grant)
        self._session.flush()

    @staticmethod
    def _permission(permission: str) -> CasePermission:
        if permission not in {"view", "edit", "review", "admin"}:
            raise ValidationFailedError("unknown case permission")
        return cast(CasePermission, permission)

    @staticmethod
    def _role(role: str) -> CaseAccessRole:
        if role not in _ROLE_PERMISSIONS:
            raise ValidationFailedError("unknown case access role")
        return cast(CaseAccessRole, role)

    @staticmethod
    def _reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        normalized = reason.strip()
        if not normalized:
            raise ValidationFailedError("case access reason must not be blank")
        return normalized

    def _lock_case_admission(
        self,
        case_id: uuid.UUID,
        principal: ResearchPrincipal,
    ) -> CaseTenantAdmission:
        statement = select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id,
            CaseTenantAdmission.tenant_id == principal.tenant_id,
        )
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        admission = self._session.scalar(statement)
        if admission is None:
            self._hidden()
        return admission

    def _require_for_admission(
        self,
        admission: CaseTenantAdmission,
        principal: ResearchPrincipal,
        permission: CasePermission,
    ) -> CaseAuthorization:
        grant_role = self._session.scalar(
            select(CaseAccessGrant.role).where(
                CaseAccessGrant.research_case_id
                == admission.research_case_id,
                CaseAccessGrant.user_id == principal.user_id,
            )
        )
        if (
            "tenant_administrator" in principal.roles
            and grant_role is None
            and permission in {"view", "admin"}
        ):
            any_grant = self._session.scalar(
                select(CaseAccessGrant.id)
                .where(
                    CaseAccessGrant.research_case_id
                    == admission.research_case_id
                )
                .limit(1)
            )
            if any_grant is None:
                return CaseAuthorization(
                    case_id=admission.research_case_id,
                    tenant_id=admission.tenant_id,
                    user_id=principal.user_id,
                    role=None,
                    permission=permission,
                )
        if grant_role is None:
            self._hidden()
        role = cast(CaseAccessRole, grant_role)
        if permission not in _ROLE_PERMISSIONS[role]:
            raise PermissionDeniedError(
                f"case {permission} permission is required"
            )
        return CaseAuthorization(
            case_id=admission.research_case_id,
            tenant_id=admission.tenant_id,
            user_id=principal.user_id,
            role=role,
            permission=permission,
        )

    def _target_user(
        self,
        admission: CaseTenantAdmission,
        user_id: uuid.UUID,
        *,
        require_active: bool = True,
    ) -> ResearchUser:
        target = self._session.get(ResearchUser, user_id)
        if (
            target is None
            or (require_active and not target.active)
            or target.tenant_id != admission.tenant_id
        ):
            self._hidden()
        return target

    def _reject_last_owner_removal(
        self,
        case_id: uuid.UUID,
        grant: CaseAccessGrant,
        *,
        replacement_role: CaseAccessRole | None,
    ) -> None:
        if grant.role != "owner" or replacement_role == "owner":
            return
        active_owner_count = self._session.scalar(
            select(func.count(CaseAccessGrant.id))
            .join(ResearchUser, ResearchUser.id == CaseAccessGrant.user_id)
            .where(
                CaseAccessGrant.research_case_id == case_id,
                CaseAccessGrant.role == "owner",
                ResearchUser.active.is_(True),
            )
        )
        target_is_active = self._session.scalar(
            select(ResearchUser.active).where(ResearchUser.id == grant.user_id)
        )
        remaining_active_owners = int(active_owner_count or 0) - int(
            target_is_active is True
        )
        if remaining_active_owners < 1:
            raise ValidationFailedError("cannot remove or downgrade the last owner")

    def _grant_or_404(
        self, case_id: uuid.UUID, user_id: uuid.UUID
    ) -> CaseAccessGrant:
        grant = self._session.scalar(
            select(CaseAccessGrant)
            .where(
                CaseAccessGrant.research_case_id == case_id,
                CaseAccessGrant.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        if grant is None:
            self._hidden()
        return grant

    def _audit(
        self,
        event_type: str,
        grant: CaseAccessGrant,
        principal: ResearchPrincipal,
        *,
        old_role: CaseAccessRole | None,
        new_role: CaseAccessRole | None,
        reason: str | None,
    ) -> None:
        emit_event(
            self._session,
            type=event_type,
            aggregate_type="case_access_grant",
            aggregate_id=grant.id,
            ref_type="research_case",
            ref_id=grant.research_case_id,
            origin="operational",
            actor=principal.actor,
            payload={
                "case_id": str(grant.research_case_id),
                "user_id": str(grant.user_id),
                "old_role": old_role,
                "new_role": new_role,
                "reason": reason,
            },
        )

    @staticmethod
    def _hidden() -> None:
        raise NotFoundError("event research case not found")
