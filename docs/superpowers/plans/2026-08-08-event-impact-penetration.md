# Event Impact Penetration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Convert domestic or overseas events into evidence-gated impact paths from companies, including unlisted companies, to Chinese A-shares and Chinese fund holdings.

**Architecture:** Current event-factor scope versions remain the immutable research boundary. New append-only impact hypotheses, company relations, and typed observations belong to one scope version; a service classifies hypotheses with transparent evidence components assembled from existing Company → Stock → ValuationSnapshot → HoldingDisclosure ledger data. Automatic research schedules cancellable impact work, a read API projects the latest scope only, and a React drill-down stays secondary to the conclusion-first workbench.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic/PostgreSQL, Pydantic v2, React, TypeScript, Vitest, Playwright, existing China stock/fund ledger, configurable China market-data gateway.

---

## File Structure

- Create backend/app/models/event_impact.py — append-only impact models and relation-review audit.
- Create backend/alembic/versions/0019_event_impact_traces.py — tables, indexes, PostgreSQL immutability triggers.
- Create backend/app/services/event_impact.py — candidate resolution, classification, review-task policy.
- Create backend/app/services/china_market_data.py — ledger-first China asset-data gateway.
- Modify backend/app/services/auto_research.py — impact task planning and safe worker dispatch.
- Create backend/app/schemas/v1/event_impact.py and backend/app/queries/event_impact.py — trace DTO and batched projection.
- Modify frontend/src/domain/eventResearch.ts, frontend/src/data/httpResearchAdapter.ts, frontend/src/data/mockResearchAdapter.ts — client contract.
- Create frontend/src/pages/prototype/EventImpactTraceScreen.tsx — responsive factor drill-down.
- Add backend/tests/test_event_impact.py, backend/tests/test_event_impact_api.py, frontend/src/tests/EventImpactTraceScreen.test.tsx, and frontend/e2e/event-impact.spec.ts.

### Task 1: Persist immutable impact records

**Files:**
- Create: backend/app/models/event_impact.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0019_event_impact_traces.py
- Create: backend/tests/test_event_impact.py

- [ ] **Step 1: Write the failing persistence tests**

    def test_impact_relation_is_scope_bound_and_append_only(session, event_case, scope_v1, china_company, statement):
        hypothesis = EventImpactHypothesis(
            research_case_id=event_case.id, scope_version_id=scope_v1.id,
            statement="资本开支增加会提高中国供应商订单",
            classification="candidate", rank=1, score_components={},
            explanation="等待验证", created_at=NOW,
        )
        session.add(hypothesis); session.commit()
        relation = CompanyImpactRelation(
            hypothesis_id=hypothesis.id, scope_version_id=scope_v1.id,
            affected_company_id=china_company.id,
            relation_kind="supplier", direction="benefits", mechanism="订单传导",
            status="candidate", source_statement_id=statement.id, created_at=NOW,
        )
        session.add(relation); session.commit()
        assert relation.scope_version_id == scope_v1.id
        assert append_only_update_fails(session, relation)

    def test_unlisted_company_relation_requires_no_stock(session, event_case, scope_v1, unlisted_company, statement):
        relation = make_relation(session, event_case.id, scope_v1.id, unlisted_company, statement)
        assert relation.affected_company_id == unlisted_company.id
        assert stocks_for_company(session, unlisted_company.id) == []

- [ ] **Step 2: Run the test and verify it fails**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py

Expected: collection fails because app.models.event_impact does not exist.

- [ ] **Step 3: Add focused append-only models**

    class EventImpactHypothesis(Base):
        __tablename__ = "event_impact_hypotheses"
        id = mapped_column(Uuid, primary_key=True, default=_uuid)
        research_case_id = mapped_column(Uuid, ForeignKey("research_cases.id"), index=True)
        scope_version_id = mapped_column(Uuid, ForeignKey("event_research_scope_versions.id"), index=True)
        statement = mapped_column(Text, nullable=False)
        classification = mapped_column(String(16), nullable=False)
        rank = mapped_column(Integer, nullable=False)
        score_components = mapped_column(JSON, nullable=False)
        explanation = mapped_column(Text, nullable=False)
        created_at = mapped_column(DateTime(timezone=True), nullable=False)

    class CompanyImpactRelation(Base):
        __tablename__ = "company_impact_relations"
        id = mapped_column(Uuid, primary_key=True, default=_uuid)
        hypothesis_id = mapped_column(Uuid, ForeignKey("event_impact_hypotheses.id"), index=True)
        scope_version_id = mapped_column(Uuid, ForeignKey("event_research_scope_versions.id"), index=True)
        affected_company_id = mapped_column(Uuid, ForeignKey("companies.id"), index=True)
        relation_kind = mapped_column(String(32), nullable=False)
        direction = mapped_column(String(16), nullable=False)
        mechanism = mapped_column(Text, nullable=False)
        status = mapped_column(String(16), nullable=False)
        source_statement_id = mapped_column(Uuid, ForeignKey("source_statements.id"))
        created_at = mapped_column(DateTime(timezone=True), nullable=False)

    class CompanyImpactRelationReview(Base):
        __tablename__ = "company_impact_relation_reviews"
        id = mapped_column(Uuid, primary_key=True, default=_uuid)
        relation_id = mapped_column(Uuid, ForeignKey("company_impact_relations.id"), index=True)
        outcome = mapped_column(String(24), nullable=False)
        reason = mapped_column(Text, nullable=False)
        reviewer = mapped_column(String(128), nullable=False)
        created_at = mapped_column(DateTime(timezone=True), nullable=False)

    class CompanyImpactObservation(Base):
        __tablename__ = "company_impact_observations"
        id = mapped_column(Uuid, primary_key=True, default=_uuid)
        relation_id = mapped_column(Uuid, ForeignKey("company_impact_relations.id"), index=True)
        kind = mapped_column(String(24), nullable=False)
        status = mapped_column(String(16), nullable=False)
        source_statement_id = mapped_column(Uuid, ForeignKey("source_statements.id"))
        valuation_snapshot_id = mapped_column(Uuid, ForeignKey("valuation_snapshots.id"))
        summary = mapped_column(Text, nullable=False)
        as_of_date = mapped_column(Date)
        created_at = mapped_column(DateTime(timezone=True), nullable=False)

Constrain classifications to candidate, key, alternative, background, unresolved; relation kinds to supplier, customer, competitor, partner, industry_peer; directions to benefits, harms, mixed, unknown; observation kinds to event, relation, operating, market, peer_control, fund; observation statuses to candidate, verified, rejected, insufficient; and review outcomes to accepted, rejected, needs_more. The relation itself remains immutable; queries resolve its effective human state from the latest review. Import the module in backend/app/models/__init__.py.

- [ ] **Step 4: Add migration 0019**

Create all four tables, indexes on research_case_id/scope_version_id/rank, hypothesis_id/affected_company_id, relation_id/kind/status, and relation_id/created_at for review lookup. Add PostgreSQL BEFORE UPDATE OR DELETE triggers that raise for every new append-only table. SQLite metadata tests must run without those triggers.

- [ ] **Step 5: Verify green and commit**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py
    git add backend/app/models/event_impact.py backend/app/models/__init__.py backend/alembic/versions/0019_event_impact_traces.py backend/tests/test_event_impact.py
    git commit -m "feat: persist event impact traces"

Expected: test passes.

### Task 2: Resolve source-backed companies and company relations

**Files:**
- Create: backend/app/services/event_impact.py
- Modify: backend/app/services/event_research_scope.py
- Modify: backend/tests/test_event_impact.py

- [ ] **Step 1: Write failing resolver tests**

    def test_refresh_creates_source_backed_candidate_relation(session, event_case, source_statement):
        resolver = FakeImpactResolver([ResolvedImpactCompany(
            company_name="未上市服务器 ODM", company_type="unlisted_supplier",
            relation_kind="supplier", direction="benefits", mechanism="交付需求增加",
            source_statement_id=source_statement.id,
        )])
        result = EventImpactResearchService(session, resolver=resolver).refresh(event_case.id)
        assert result.hypotheses_created == 1
        assert only_relation(session, event_case.id).status == "candidate"
        assert only_relation(session, event_case.id).source_statement_id == source_statement.id

    def test_unbacked_ai_relation_is_never_key(session, event_case):
        EventImpactResearchService(session, resolver=FakeImpactResolver([UNBACKED])).refresh(event_case.id)
        assert only_relation(session, event_case.id).status == "candidate"
        assert only_hypothesis(session, event_case.id).classification != "key"

- [ ] **Step 2: Run tests and verify red**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py -k 'source_backed or unbacked'

Expected: EventImpactResearchService is missing.

- [ ] **Step 3: Implement resolver boundary**

    @dataclass(frozen=True)
    class ResolvedImpactCompany:
        company_name: str
        company_type: str
        relation_kind: str
        direction: str
        mechanism: str
        source_statement_id: uuid.UUID | None

    class EventImpactResearchService:
        def refresh(self, case_id):
            scope = latest_scope_or_raise(self._session, case_id)
            statements = admissible_event_statements(self._session, case_id)
            for factor in active_scope_factors(self._session, scope.id):
                hypothesis = self._append_hypothesis(scope, factor.statement)
                for candidate in self._resolver.resolve(factor.statement, statements):
                    company = resolve_or_create_company(self._session, candidate.company_name, candidate.company_type)
                    self._append_relation(hypothesis, company, candidate)
            return self._classify(scope.id)

admissible_event_statements only accepts same-case statements whose document source passes source admission. _append_relation writes scope_version_id from the owning hypothesis. resolve_or_create_company keeps Company.type and never creates Stock rows for unlisted entities. The production resolver may call LLMClient for structured candidates only outside a transaction; tests inject FakeImpactResolver.

- [ ] **Step 4: Schedule a fresh scope-specific refresh**

After EventResearchScopeService.update flushes its new scope and evidence assignments, schedule an impact refresh for that scope id. Do not copy predecessor hypotheses or relations to the new scope; historical rows remain audit records.

- [ ] **Step 5: Verify green and commit**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py tests/test_event_research_scope.py
    git add backend/app/services/event_impact.py backend/app/services/event_research_scope.py backend/tests/test_event_impact.py
    git commit -m "feat: resolve event impact companies"

### Task 3: Collect China A-share and fund data without fabricating coverage

**Files:**
- Create: backend/app/services/china_market_data.py
- Modify: backend/app/services/event_impact.py
- Modify: backend/tests/test_event_impact.py

- [ ] **Step 1: Write failing data-status tests**

    def test_listed_relation_needs_operating_market_and_peer_evidence(session, listed_relation):
        EventImpactResearchService(session, market_data=LedgerChinaMarketData(session)).collect_data(
            listed_relation.id, as_of=date(2026, 8, 8)
        )
        assert observation_statuses(session, listed_relation.id) == {
            "operating": "verified", "market": "verified", "peer_control": "verified"
        }

    def test_unlisted_relation_never_gets_stock_or_fund_observation(session, unlisted_relation):
        EventImpactResearchService(session, market_data=LedgerChinaMarketData(session)).collect_data(
            unlisted_relation.id, as_of=date(2026, 8, 8)
        )
        assert observation_kinds(session, unlisted_relation.id) == {"relation", "operating"}

    def test_partial_holding_is_not_computable(session, verified_stock_relation):
        fund = EventImpactResearchService(session).fund_exposure(
            verified_stock_relation.id, as_of=date(2026, 8, 8)
        )[0]
        assert fund.computable is False
        assert fund.exposure is None
        assert fund.coverage_status == "partial"

- [ ] **Step 2: Run tests and verify red**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py -k 'listed_relation or unlisted_relation or partial_holding'

Expected: LedgerChinaMarketData and collect_data are missing.

- [ ] **Step 3: Implement ledger-first gateway**

    class ChinaMarketData(Protocol):
        def operating_observations(self, stock, *, as_of): ...
        def market_observations(self, stock, *, as_of): ...
        def peer_observations(self, stock, *, as_of): ...
        def fund_holdings(self, stock_ids, *, as_of): ...

    class LedgerChinaMarketData:
        def operating_observations(self, stock, *, as_of):
            return metric_snapshots(self._session, stock.id, {"REVENUE_YOY", "GROSS_MARGIN", "ORDER_GUIDANCE"}, as_of)
        def market_observations(self, stock, *, as_of):
            return metric_snapshots(self._session, stock.id, {"EVENT_RETURN_1D", "TURNOVER_RATE", "PE_TTM"}, as_of)

Use immutable ValuationSnapshot rows as raw metrics and append typed observations with metric definition, source, source-tool/path when present, as-of date, and status. A configured provider can fetch Chinese announcements, statements, quotes and holdings, but must normalize them to ledger records first. Missing credentials or empty provider data appends insufficient observations and a gap, never synthetic values.

- [ ] **Step 4: Reuse fund point-in-time rules**

Reuse PenetrationQueries visibility: published_at <= as_of and latest report per fund/stock. Aggregate only verified listed-company relations. Set computable false and exposure None when no visible disclosure, disclosure exceeds the research freshness cutoff, or coverage is below 0.80; always return report period, published_at, source, covered positions and coverage ratio.

- [ ] **Step 5: Verify green and commit**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py
    git add backend/app/services/china_market_data.py backend/app/services/event_impact.py backend/tests/test_event_impact.py
    git commit -m "feat: collect China asset impact data"

### Task 4: Classify key factors through transparent evidence competition

**Files:**
- Modify: backend/app/services/event_impact.py
- Modify: backend/app/services/event_conclusion.py
- Modify: backend/tests/test_event_impact.py
- Modify: backend/tests/test_event_research_lifecycle.py

- [ ] **Step 1: Write failing classification tests**

    def test_complete_hypothesis_becomes_key(session, complete_impact_event):
        key = EventImpactResearchService(session).classify(complete_impact_event.id)[0]
        assert key.classification == "key"
        assert key.score_components == {
            "event": 1, "company": 1, "operating": 1,
            "market": 1, "peer_control": 1, "fund_coverage": 1,
        }

    def test_market_only_hypothesis_is_alternative_and_blocks_draft(session, market_only_event):
        EventImpactResearchService(session).classify(market_only_event.id)
        with pytest.raises(ValidationFailedError, match="impact coverage"):
            EventConclusionService(session).create_draft(market_only_event.id)

    def test_no_key_factor_sets_unresolved_gap_without_draft(session, event_without_candidates):
        EventImpactResearchService(session).classify(event_without_candidates.id)
        assert lifecycle(session, event_without_candidates.id).status == "exhausted"
        assert "不能确定关键因素" in lifecycle(session, event_without_candidates.id).current_gap
        assert EventConclusionService(session).latest(event_without_candidates.id) is None

- [ ] **Step 2: Run tests and verify red**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py -k 'becomes_key or market_only or no_key'

Expected: classification and the conclusion gate are absent.

- [ ] **Step 3: Implement the fixed classification rule**

    def classify_hypothesis(hypothesis, relations, observations, fund_coverages):
        score = {
            "event": int(has_verified_kind(observations, "event")),
            "company": int(has_verified_relation(relations)),
            "operating": int(has_verified_kind(observations, "operating")),
            "market": int(has_verified_kind(observations, "market")),
            "peer_control": int(has_verified_kind(observations, "peer_control")),
            "fund_coverage": int(any(item.computable for item in fund_coverages)),
        }
        if all(score[name] for name in ("event", "company", "operating", "market", "peer_control")):
            return "key", score, "事件、传导、经营、市场和对照证据完整"
        if score["event"] and (score["company"] or score["market"]):
            return "alternative", score, "存在部分支持，但缺少区分替代解释的完整证据"
        return "background", score, "仅有环境线索，未形成可验证资产传导"

Append a new assessment rather than editing a prior one. Rank key factors first by completed components, then alternatives, retaining factor order for ties. EventConclusionService.create_draft must require at least one current-scope key assessment plus existing formal-evidence coverage, otherwise raise ValidationFailedError with impact coverage is insufficient. With the existing case → lifecycle lock, no key factor sets exhausted, next_human_action edit_factors, and a gap beginning 不能确定关键因素.

- [ ] **Step 4: Verify green**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py tests/test_event_research_lifecycle.py tests/test_event_research_scope.py

Expected: PASS.

- [ ] **Step 5: Commit**

    git add backend/app/services/event_impact.py backend/app/services/event_conclusion.py backend/tests/test_event_impact.py backend/tests/test_event_research_lifecycle.py
    git commit -m "feat: gate event factors on impact evidence"

### Task 5: Run impact research automatically and cancel it safely

**Files:**
- Modify: backend/app/services/auto_research.py
- Modify: backend/app/repositories/auto_research.py
- Modify: backend/app/services/event_impact.py
- Modify: backend/tests/test_event_impact.py
- Modify: backend/tests/test_event_research_scope.py

- [ ] **Step 1: Write failing automatic-task and cancellation tests**

    def test_run_contains_impact_tasks_for_current_scope(session, event_case, scope_v1):
        run = AutoResearchService(session).start(
            event_case.id, thesis_ids=current_scope_thesis_ids(session, scope_v1.id)
        )
        assert task_types_for_run(session, run.id) >= {
            "impact_companies", "impact_operating", "impact_market",
            "impact_peer", "impact_fund", "impact_alternative",
        }

    def test_scope_replacement_prevents_running_impact_write(session, running_impact_task, event_case):
        replace_scope_in_other_session(event_case.id, ["新因素 A", "新因素 B", "新因素 C"])
        release_fake_provider(running_impact_task)
        assert no_impact_rows_for_cancelled_task(session, running_impact_task.id)
        assert fresh_run_has_only_current_scope_impact_tasks(session, event_case.id)

- [ ] **Step 2: Run tests and verify red**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py tests/test_event_research_scope.py -k 'impact_tasks or prevents_running'

Expected: automatic research does not plan impact tasks.

- [ ] **Step 3: Add task types and dispatch**

For every current-scope thesis, create impact_companies, impact_operating, impact_market, impact_peer, impact_fund, and impact_alternative tasks. Dispatch them through EventImpactResearchService with the existing provider-safe sequence: commit task-running state before provider work; call _claim_task_output_slot before append-only writes; re-read cancellation before marking completion.

Create OperationalTask with task_type review_company_impact only when an unverified candidate has a resolved A-share Stock, a visible Chinese fund holding, and no admissible relationship statement. It references the relation id and names the missing source. Unlisted or low-impact candidates remain automatic evidence gaps.

- [ ] **Step 4: Verify green**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact.py tests/test_event_research_scope.py tests/test_auto_research.py

Expected: PASS; PostgreSQL barrier cases may skip when TEST_DATABASE_URL is unset.

- [ ] **Step 5: Commit**

    git add backend/app/services/auto_research.py backend/app/repositories/auto_research.py backend/app/services/event_impact.py backend/tests/test_event_impact.py backend/tests/test_event_research_scope.py
    git commit -m "feat: automate event impact research"

### Task 6: Expose a latest-scope impact trace API and client contract

**Files:**
- Create: backend/app/schemas/v1/event_impact.py
- Create: backend/app/queries/event_impact.py
- Modify: backend/app/api/v1/event_research.py
- Create: backend/tests/test_event_impact_api.py
- Modify: frontend/src/domain/eventResearch.ts
- Modify: frontend/src/data/httpResearchAdapter.ts
- Modify: frontend/src/data/mockResearchAdapter.ts
- Modify: frontend/src/tests/HttpResearchAdapter.test.ts

- [ ] **Step 1: Write failing API and adapter tests**

    def test_trace_returns_current_scope_and_unlisted_boundary(api_client, event_case):
        body = api_client.get(f"/api/v1/event-research/{event_case.id}/impact-trace").json()
        relation = next(row for row in body["factors"][0]["relations"] if row["company_type"] == "unlisted_supplier")
        assert body["scope_version"] == 2
        assert relation["stocks"] == []
        assert relation["fund_exposure"] == []

    def test_trace_has_no_numeric_exposure_for_partial_holding(api_client, event_case):
        fund = api_client.get(f"/api/v1/event-research/{event_case.id}/impact-trace").json()["factors"][0]["funds"][0]
        assert fund["computable"] is False
        assert fund["exposure"] is None
        assert fund["coverage_status"] == "partial"

    def test_accepting_high_impact_relation_appends_a_review_and_reclassifies(api_client, relation):
        response = api_client.post(
            f"/api/v1/event-research/impact-relations/{relation.id}/review",
            json={"outcome": "accepted", "reason": "公告明确披露供应关系", "reviewer": "researcher"},
        )
        assert response.status_code == 201
        assert latest_relation_review(relation.id).outcome == "accepted"
        assert api_client.get(f"/api/v1/event-research/{relation.case_id}/impact-trace").json()["factors"][0]["relations"][0]["effective_status"] == "verified"

    it("maps unlisted and partial-fund boundaries", async () => {
      const trace = await adapter.getEventImpactTrace("case-1");
      expect(trace.factors[0].relations[0].companyType).toBe("unlisted_supplier");
      expect(trace.factors[0].relations[0].stocks).toEqual([]);
      expect(trace.factors[0].funds[0]).toMatchObject({ computable: false, exposure: null });
    });

- [ ] **Step 2: Run tests and verify red**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact_api.py
    cd frontend && npm test -- HttpResearchAdapter.test.ts

Expected: the route and getEventImpactTrace do not exist.

- [ ] **Step 3: Implement DTOs, query, route, adapters and mocks**

Define EventImpactTraceDTO with scope_version, as_of, factors, alternatives and progress. Nested relation, stock, observation and fund DTOs include all source/status fields, the latest review outcome, and effective_status. EventImpactQueries.trace bulk-fetches latest-scope rows and never returns predecessor scope rows. Fund DTOs always include report period, publish time, source, coverage ratio, coverage status, computable and nullable exposure.

    @router.get("/{case_id}/impact-trace", response_model=EventImpactTraceDTO)
    def event_impact_trace(case_id: uuid.UUID, db: Session = Depends(get_db)):
        return EventImpactQueries(db).trace(case_id)

    @router.post("/impact-relations/{relation_id}/review", status_code=status.HTTP_201_CREATED)
    def review_impact_relation(relation_id: uuid.UUID, payload: ReviewImpactRelationRequest, db: Session = Depends(get_db)):
        return EventImpactResearchService(db).review_relation(relation_id, **payload.model_dump())

ReviewImpactRelationRequest validates outcome, nonempty reason and reviewer length. review_relation locks the owning case/lifecycle, appends CompanyImpactRelationReview, refreshes only that relation's classification, and never mutates the original relation. Add EventImpactTrace and reviewEventImpactRelation to the frontend domain, a snake-case mapper, and mock data containing a verified listed company, an unlisted candidate, a market-only alternative, a computable fund, and a partial fund with safe non-example source URLs.

- [ ] **Step 4: Verify green and commit**

    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_impact_api.py tests/test_event_research_api.py
    cd frontend && npm test -- HttpResearchAdapter.test.ts && npm run typecheck
    git add backend/app/schemas/v1/event_impact.py backend/app/queries/event_impact.py backend/app/api/v1/event_research.py backend/tests/test_event_impact_api.py frontend/src/domain/eventResearch.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/tests/HttpResearchAdapter.test.ts
    git commit -m "feat: expose event impact traces"

Expected: tests and typecheck pass.

### Task 7: Build and verify the responsive impact drill-down

**Files:**
- Create: frontend/src/pages/prototype/EventImpactTraceScreen.tsx
- Modify: frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx
- Modify: frontend/src/main.tsx
- Modify: frontend/src/styles-prototype.css
- Create: frontend/src/tests/EventImpactTraceScreen.test.tsx
- Create: frontend/e2e/event-impact.spec.ts

- [ ] **Step 1: Write failing screen tests**

    it("drills one factor into event, company, A-share and fund evidence", async () => {
      renderImpactTrace("event-tsm");
      expect(await screen.findByRole("heading", { name: "海外 AI 投入如何影响中国资产？" })).toBeVisible();
      await userEvent.click(screen.getByRole("button", { name: /资本开支上调/ }));
      expect(screen.getByText("事件与相关公司")).toBeVisible();
      expect(screen.getByText("中国公司传导")).toBeVisible();
      expect(screen.getByText("A 股数据验证")).toBeVisible();
      expect(screen.getByText("中国基金持仓暴露")).toBeVisible();
    });

    it("renders an unlisted company as a transmission node with no asset exposure", async () => {
      renderImpactTrace("event-tsm");
      expect(await screen.findByText("未上市 · 传导节点")).toBeVisible();
      expect(screen.getByText("不计算股票或基金暴露")).toBeVisible();
    });

    it("does not show a fabricated value for partial holdings", async () => {
      renderImpactTrace("event-tsm");
      expect(await screen.findByText("披露过期，暂不可计算")).toBeVisible();
      expect(screen.queryByText("10.5%")).not.toBeInTheDocument();
    });

    it("lets the researcher review only a high-impact unresolved relation", async () => {
      renderImpactTrace("event-tsm");
      await userEvent.click(await screen.findByRole("button", { name: "审核供应关系" }));
      await userEvent.click(screen.getByRole("button", { name: "确认关系成立" }));
      expect(await screen.findByRole("status")).toHaveTextContent("已确认关系，系统正在重新评估因素");
    });

- [ ] **Step 2: Run tests and verify red**

    cd frontend && npm test -- EventImpactTraceScreen.test.tsx

Expected: page and route are missing.

- [ ] **Step 3: Implement page and navigation**

Add route /events/:caseId/impact and a secondary Link named 查看影响穿透 from the workbench; it must not replace the lifecycle's single primary action. Render conclusion/boundary summary, factor buttons with aria-pressed, four labeled layers, company relations, A-share observations, and fund coverage cards. Render safe HTTP(S) source links only. Show an inline review control only when effective_status is candidate and the API marks the relation high impact; its dialog collects a reason, calls reviewEventImpactRelation, leaves the dialog open on error, announces success with role status, and reloads the trace. Use these explicit states: 已验证影响, 市场反应已观察, 传导待验证, 未上市 · 传导节点, 不计算股票或基金暴露, 披露过期，暂不可计算, 持仓覆盖不足，暂不可计算.

At max-width 900px switch to one column, preserve conclusion before factor selector, render table rows as labeled cards and prevent horizontal overflow.

- [ ] **Step 4: Add a narrow-browser test**

    test("keeps China-only boundaries usable at 390px", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/events/event-tsm/impact?client=mock");
      await expect(page.getByText("未上市 · 传导节点")).toBeVisible();
      await expect(page.getByText("不计算股票或基金暴露")).toBeVisible();
      await expect(page.getByText("披露过期，暂不可计算")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });

- [ ] **Step 5: Run full verification and commit**

    cd frontend && npm test -- EventImpactTraceScreen.test.tsx EventResearchWorkbenchScreen.test.tsx && npm run typecheck && npm run build
    cd frontend && PW_BROWSER_CHANNEL=chrome PW_PORT=5194 npm run e2e -- event-impact.spec.ts event-research.spec.ts --reporter=line
    cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q
    git add frontend/src/pages/prototype/EventImpactTraceScreen.tsx frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx frontend/src/main.tsx frontend/src/styles-prototype.css frontend/src/tests/EventImpactTraceScreen.test.tsx frontend/e2e/event-impact.spec.ts
    git commit -m "feat: add event impact drill-down"

Expected: all tests pass; PostgreSQL-only concurrency cases may skip when TEST_DATABASE_URL is unset.

## Final Verification

- [ ] Run git diff main...HEAD --check; expect no output.
- [ ] Run backend full suite, frontend suite, typecheck, production build, and both event browser specs from Task 7.
- [ ] Run cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/alembic upgrade head followed by alembic current; expect 0019 (head).
- [ ] Inspect domestic-event and overseas-event fixtures through GET /api/v1/event-research/{case_id}/impact-trace; confirm only current-scope, source-backed rows generate key factors and numerical exposure.
