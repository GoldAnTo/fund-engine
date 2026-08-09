from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app.models.ledger import ImmutableLedgerError, ResearchCase
from app.models.research_protocol import MechanismNodeVersion, MechanismTemplateVersion
from app.services.mechanism_templates import seed_ai_capex_template
from app.services.research_protocol import ResearchProtocolService


def test_mechanism_template_versions_are_append_only(session) -> None:
    template = MechanismTemplateVersion(
        template_key="overseas_ai_capex_to_china_hardware",
        version=1,
        display_name="海外 AI CapEx 到中国硬件",
        industry_scope="ai_hardware",
        approved_by="human:owner",
        reason="初始模板",
        created_at=datetime.now(timezone.utc),
    )
    session.add(template)
    session.flush()

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(MechanismTemplateVersion)
            .where(MechanismTemplateVersion.id == template.id)
            .values(display_name="被改写")
        )


def test_seeded_ai_capex_template_has_required_and_alternative_nodes(session) -> None:
    template = seed_ai_capex_template(session)
    roles = set(session.scalars(
        select(MechanismNodeVersion.role)
        .where(MechanismNodeVersion.template_version_id == template.id)
    ))

    assert {"required_for_outcome", "required_for_attribution", "alternative_explanation", "scope_guard"}.issubset(roles)
    assert seed_ai_capex_template(session).id == template.id


def test_case_template_selection_is_append_only(session) -> None:
    case = ResearchCase(
        title="机制协议 Case", industry_topic="ai", created_by="human", created_at=datetime.now(timezone.utc)
    )
    session.add(case)
    session.flush()
    template = seed_ai_capex_template(session)

    selection = ResearchProtocolService(session).select_template(
        case.id, template.id, reviewer="human:reviewer", reason="适用范围已核对"
    )

    assert selection.research_case_id == case.id
    assert selection.template_version_id == template.id
