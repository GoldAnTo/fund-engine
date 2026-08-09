# Mechanism Template and Verification Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a protocol-required thesis become eligible for formal research only after a reviewed immutable mechanism template, executable verification rules, and a counter explanation are recorded.

**Architecture:** Persist template versions, nodes, edges, Case selections and verification-rule versions as append-only ledger records. Seed only `overseas_ai_capex_to_china_hardware/v1`; human reviewers select it and append rules. The gate reads persisted effective versions only, and the AI assessment generator refuses blocked protocol theses before freezing a snapshot.

**Case-scope correction (2026-08-09):** A template is reusable, but its verification rules are never global template defaults. Every rule version belongs to one `ResearchCase`, and its supersession chain is resolved by `(research_case_id, mechanism_edge_id)`. Selecting the same template in a second Case therefore starts with no rules and cannot inherit another Case's assumptions, reviewer, or timing window.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite/PostgreSQL, React, TypeScript, pytest, Vitest.

---

## File map

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0025_mechanism_protocol.py` | Append-only schema and PostgreSQL immutability triggers. |
| `backend/app/models/research_protocol.py` | Template, node, edge, selection and rule ORM records. |
| `backend/app/repositories/research_protocol.py` | Effective selection/rule reads and append-only writes. |
| `backend/app/services/mechanism_templates.py` | Idempotent approved AI CapEx v1 seed. |
| `backend/app/services/research_protocol.py` | Admission validation and substantive gate. |
| `backend/app/api/v1/research_protocol.py` | Template, selection, rule and read-model routes. |
| `backend/app/ai/assessment_gen.py` | Pre-snapshot gate enforcement. |
| `frontend/src/app/researchOsApi.ts` | Typed API facade. |
| `frontend/src/features/case/CasePages.tsx` | Visible template/rule configuration and audit history. |
| `backend/tests/test_mechanism_protocol*.py` | Storage, gate and API tests. |
| `frontend/src/tests/ResearchOsPages.test.tsx` | User-visible protocol state tests. |

### Task 1: Add immutable mechanism protocol storage

**Files:**
- Create: `backend/alembic/versions/0025_mechanism_protocol.py`
- Modify: `backend/app/models/research_protocol.py`
- Modify: `backend/app/models/ledger.py`
- Test: `backend/tests/test_mechanism_protocol.py`

- [ ] **Step 1: Write the failing ledger test**

```python
def test_mechanism_protocol_records_are_append_only(session):
    template = MechanismTemplateVersion(
        template_key="overseas_ai_capex_to_china_hardware", version=1,
        display_name="海外 AI CapEx 到中国硬件", industry_scope="ai_hardware",
        approved_by="owner", reason="initial", created_at=NOW,
    )
    session.add(template); session.flush()
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(MechanismTemplateVersion)
            .where(MechanismTemplateVersion.id == template.id)
            .values(display_name="changed")
        )
```

- [ ] **Step 2: Verify the test fails**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol.py -q`

Expected: collection fails because `MechanismTemplateVersion` does not exist.

- [ ] **Step 3: Add minimal storage**

Create immutable `MechanismTemplateVersion`, `MechanismNodeVersion`, `MechanismEdgeVersion`, `CaseMechanismSelectionVersion`, and `VerificationRuleVersion`. Nodes use only `required_for_outcome`, `required_for_attribution`, `alternative_explanation`, or `scope_guard`; rules contain expected direction, metric ID, support and contradiction predicates, allowed source roles, observed/available windows, next event, reviewer, reason and timestamp. Add all tables to `IMMUTABLE_TABLES` and migration trigger loops.

```python
class CaseMechanismSelectionVersion(Base):
    __tablename__ = "case_mechanism_selection_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mechanism_template_versions.id"), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("case_mechanism_selection_versions.id"))
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Verify storage**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol.py -q`

Expected: PASS; update/delete attempts raise `ImmutableLedgerError`.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0025_mechanism_protocol.py backend/app/models/research_protocol.py backend/app/models/ledger.py backend/tests/test_mechanism_protocol.py
git commit -m "feat: add immutable mechanism protocol ledger"
```

### Task 2: Seed and select the narrow AI CapEx template

**Files:**
- Create: `backend/app/services/mechanism_templates.py`
- Modify: `backend/app/repositories/research_protocol.py`
- Modify: `backend/app/services/research_protocol.py`
- Test: `backend/tests/test_mechanism_protocol.py`

- [ ] **Step 1: Write failing seed and selection tests**

```python
def test_seeded_ai_capex_template_has_required_and_alternative_nodes(session):
    template = seed_ai_capex_template(session)
    roles = set(session.scalars(
        select(MechanismNodeVersion.role)
        .where(MechanismNodeVersion.template_version_id == template.id)
    ))
    assert {"required_for_outcome", "required_for_attribution",
            "alternative_explanation", "scope_guard"}.issubset(roles)

def test_case_selection_is_append_only(session, protocol_case):
    template = seed_ai_capex_template(session)
    selected = ResearchProtocolService(session).select_template(
        protocol_case.id, template.id, reviewer="human", reason="适用范围已核对"
    )
    assert selected.template_version_id == template.id
```

- [ ] **Step 2: Verify failure**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol.py -q`

Expected: import failure for `seed_ai_capex_template`.

- [ ] **Step 3: Implement the idempotent seed and selection**

Use this exact v1 node list; append main-path edges in listed order plus one scope guard edge. A repeated seed returns its existing key/version and creates no rows.

```python
AI_CAPEX_NODES = (
    ("customer_capex", "客户实际 CapEx", "required_for_attribution"),
    ("architecture_allocation", "投向相关网络/产品架构", "required_for_attribution"),
    ("target_order_share", "目标公司订单或份额", "required_for_outcome"),
    ("delivery_shipment", "交付或出货", "required_for_outcome"),
    ("business_line_revenue", "相关业务收入", "required_for_outcome"),
    ("asp_margin", "ASP、成本和毛利率", "alternative_explanation"),
    ("non_target_revenue", "非目标业务收入", "scope_guard"),
)
```

`select_template` validates reviewer/reason and appends a new Case selection; it never updates an earlier selection.

- [ ] **Step 4: Verify seed and selection**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol.py -q`

Expected: PASS; repeated seed does not duplicate a template version.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mechanism_templates.py backend/app/repositories/research_protocol.py backend/app/services/research_protocol.py backend/tests/test_mechanism_protocol.py
git commit -m "feat: add reviewed AI capex mechanism template"
```

### Task 3: Add verification rules and make the gate substantive

**Files:**
- Modify: `backend/app/repositories/research_protocol.py`
- Modify: `backend/app/services/research_protocol.py`
- Modify: `backend/app/ai/assessment_gen.py`
- Test: `backend/tests/test_mechanism_protocol.py`
- Test: `backend/tests/test_ai_engine.py`

- [ ] **Step 1: Write failing gate tests**

```python
def test_gate_requires_rules_for_required_edges_and_counter_hypothesis(session, protocol_thesis):
    setup_approved_outcome_and_template(session, protocol_thesis)
    result = ResearchProtocolService(session).check_researchability(protocol_thesis.id)
    assert result.reason_codes == [
        "missing_verification_rule",
        "insufficient_primary_metrics",
        "missing_counter_hypothesis",
    ]

def test_blocked_protocol_thesis_never_freezes_snapshot(session, protocol_thesis, fake_client):
    with pytest.raises(ValidationError, match="researchability gate blocked"):
        AssessmentGenerator(fake_client).generate(protocol_thesis.id, CUTOFF, session)
    assert session.scalars(select(EvidenceSnapshot)).all() == []
```

- [ ] **Step 2: Verify failure**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol.py backend/tests/test_ai_engine.py -q`

Expected: gate still returns placeholders and assessment proceeds.

- [ ] **Step 3: Implement strict rule admission and gate**

```python
def validate_verification_rule(edge, metric, value):
    if value.expected_direction not in {"increase", "decrease", "stable", "mixed"}:
        raise ValidationError("verification expected_direction is invalid")
    if not value.support_predicate.strip() or not value.contradiction_predicate.strip():
        raise ValidationError("verification needs support and contradiction predicates")
    if not set(value.allowed_source_roles).issubset(set(metric.allowed_source_roles)):
        raise ValidationError("verification source roles exceed metric permission")
```

For an approved binding, read the effective Case template selection. Missing selection returns `missing_mechanism_template`. Required edges without an effective rule return `missing_verification_rule`; missing an alternative rule or a contradiction predicate returns `missing_counter_hypothesis`; business-line outcomes with fewer than two independent required-edge metric IDs return `insufficient_primary_metrics`. Return `ready` only after all checks. At the top of `AssessmentGenerator.generate`, call the gate before any snapshot/read transaction and raise `ValidationError(f"researchability gate blocked: {', '.join(result.reason_codes)}")` for a blocked protocol thesis.

- [ ] **Step 4: Verify the gate and assessment block**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol.py backend/tests/test_ai_engine.py backend/tests/test_research_protocol.py -q`

Expected: PASS; legacy theses retain assessment behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/research_protocol.py backend/app/services/research_protocol.py backend/app/ai/assessment_gen.py backend/tests/test_mechanism_protocol.py backend/tests/test_ai_engine.py
git commit -m "feat: enforce mechanism verification gate"
```

### Task 4: Expose reviewer APIs and complete the Case protocol page

**Files:**
- Modify: `backend/app/schemas/v1/research_protocol.py`
- Modify: `backend/app/api/v1/research_protocol.py`
- Modify: `frontend/src/app/researchOsApi.ts`
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os-overrides.css`
- Test: `backend/tests/test_mechanism_protocol_api.py`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Write failing API and UI tests**

```python
def test_case_protocol_api_selects_template_and_appends_rule(cmd_client, protocol_case):
    templates = cmd_client.get("/api/v1/mechanism-templates")
    selected = cmd_client.post(
        f"/api/v1/research-cases/{protocol_case.id}/mechanism-selection",
        json={"template_version_id": templates.json()[0]["id"], "reviewer": "human", "reason": "适用范围已核对"},
    )
    assert selected.status_code == 201
```

```tsx
expect(await screen.findByText("海外 AI CapEx 到中国硬件")).toBeVisible();
expect(screen.getByText("验证规则与反证")).toBeVisible();
expect(screen.getByText(/系统不会把市场表现自动写成机制成立/)).toBeVisible();
```

- [ ] **Step 2: Verify failure**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol_api.py -q`

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: API returns 404 and UI text is absent.

- [ ] **Step 3: Implement reviewer routes and visible audit controls**

Add `GET /mechanism-templates`, `POST /research-cases/{case_id}/mechanism-selection`, `GET /research-cases/{case_id}/mechanism-protocol`, and `POST /research-cases/{case_id}/mechanism-edges/{edge_id}/verification-rules`. The Case page displays selected template/version, every node role, each Case-scoped rule's permitted source roles, support/contradiction predicates, next event, reviewer, reason and timestamp. Each mutation appends a new record and refreshes the read model; failures leave the prior protocol intact.

- [ ] **Step 4: Regenerate contracts and verify**

Run: `bash scripts/sync-contract.sh --update`

Run: `backend/.venv/bin/python -m pytest backend/tests/test_mechanism_protocol_api.py backend/tests/test_mechanism_protocol.py -q`

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run build`

Expected: all pass; generated contract only adds the protocol routes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/v1/research_protocol.py backend/app/api/v1/research_protocol.py backend/tests/test_mechanism_protocol_api.py frontend/openapi.json frontend/src/contracts/v1.ts frontend/src/app/researchOsApi.ts frontend/src/features/case/CasePages.tsx frontend/src/styles/research-os-overrides.css frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "feat: review case mechanism protocol"
```

## Self-review

- Spec coverage: Tasks 1-2 deliver versioned mechanism objects and only the confirmed AI CapEx path; Task 3 enforces necessary nodes, independent metrics, alternatives and falsifiers before formal assessment; Task 4 provides traceable human configuration rather than hidden automation.
- Explicit exclusions: no model can select a template, approve a rule or infer a threshold; no market observation/fund disclosure becomes causal proof; no trading, target-price or return forecast is introduced.
- The API uses the same immutable IDs stored by the gate. Template upgrades append a new version and case selection; they do not rewrite conclusions.
