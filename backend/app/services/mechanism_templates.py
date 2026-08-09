"""Approved, narrow mechanism templates.

Only the explicitly reviewed overseas AI CapEx to China hardware path is seeded
here. It is a protocol skeleton, not evidence that any company path holds.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research_protocol import (
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MechanismTemplateVersion,
)


AI_CAPEX_TEMPLATE_KEY = "overseas_ai_capex_to_china_hardware"
AI_CAPEX_NODES = (
    ("customer_capex", "客户实际 CapEx", "required_for_attribution"),
    ("architecture_allocation", "投向相关网络/产品架构", "required_for_attribution"),
    ("target_order_share", "目标公司订单或份额", "required_for_outcome"),
    ("delivery_shipment", "交付或出货", "required_for_outcome"),
    ("business_line_revenue", "相关业务收入", "required_for_outcome"),
    ("asp_margin", "ASP、成本和毛利率", "alternative_explanation"),
    ("non_target_revenue", "非目标业务收入", "scope_guard"),
)
AI_CAPEX_EDGES = (
    ("capex_to_architecture", "customer_capex", "architecture_allocation"),
    ("architecture_to_order", "architecture_allocation", "target_order_share"),
    ("order_to_delivery", "target_order_share", "delivery_shipment"),
    ("delivery_to_revenue", "delivery_shipment", "business_line_revenue"),
    ("revenue_to_margin", "business_line_revenue", "asp_margin"),
    ("revenue_to_scope_guard", "business_line_revenue", "non_target_revenue"),
)


def seed_ai_capex_template(session: Session) -> MechanismTemplateVersion:
    template = session.scalar(
        select(MechanismTemplateVersion)
        .where(MechanismTemplateVersion.template_key == AI_CAPEX_TEMPLATE_KEY)
        .where(MechanismTemplateVersion.version == 1)
        .limit(1)
    )
    if template is not None:
        return template
    now = datetime.now(timezone.utc)
    template = MechanismTemplateVersion(
        template_key=AI_CAPEX_TEMPLATE_KEY,
        version=1,
        display_name="海外 AI CapEx 到中国硬件",
        industry_scope="ai_hardware",
        approved_by="human:industry-owner",
        reason="首个受控机制模板；仅覆盖海外客户投入至中国硬件业务线的可验证路径",
        created_at=now,
    )
    session.add(template)
    session.flush()
    nodes = {
        key: MechanismNodeVersion(
            template_version_id=template.id,
            node_key=key,
            display_name=name,
            role=role,
            created_at=now,
        )
        for key, name, role in AI_CAPEX_NODES
    }
    session.add_all(nodes.values())
    session.flush()
    session.add_all(
        MechanismEdgeVersion(
            template_version_id=template.id,
            edge_key=edge_key,
            source_node_id=nodes[source].id,
            target_node_id=nodes[target].id,
            created_at=now,
        )
        for edge_key, source, target in AI_CAPEX_EDGES
    )
    session.flush()
    return template

