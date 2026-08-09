from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.models.ledger import ImmutableLedgerError
from app.models.research_protocol import MechanismTemplateVersion


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
