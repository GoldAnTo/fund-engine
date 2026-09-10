"""Conditional model drafts never rewrite the authenticated published research."""

from __future__ import annotations

import sys
from copy import deepcopy
from hashlib import sha256
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.models.ledger import ValidationError
from app.underwriting.hashing import canonical_hash
from tests.underwriting.test_company_research_api import (
    BASE,
    _run_public_company_research_pipeline,
)


@pytest.fixture
def numeric_dependencies(monkeypatch):
    """Isolate persistence while parallel agents implement numeric/source modules."""
    baseline = {
        "schema_version": "test.baseline.v1",
        "facts": [{"value": "10"}],
        "sources": [],
        "research_gaps": [],
    }
    baseline["content_hash"] = canonical_hash(baseline)
    source = ModuleType("app.underwriting.services.company_research_financial_baseline")
    source.load_alphabet_financial_baseline = lambda cutoff: deepcopy(baseline)

    def authenticate_snapshot(snapshot, cutoff_at):
        if snapshot != baseline:
            raise ValidationError("financial baseline snapshot is invalid")
        return deepcopy(snapshot)

    source.authenticate_financial_baseline_snapshot = authenticate_snapshot
    model = ModuleType("app.underwriting.services.company_research_financial_model")
    model.candidate_financial_inputs = lambda baseline, cutoff_at: {"revenue": "20"}

    def calculate(inputs, baseline, market, cutoff_at):
        if (
            set(inputs) != {"revenue"}
            or not isinstance(inputs["revenue"], str)
            or not inputs["revenue"].isdigit()
        ):
            raise ValidationError("unknown or invalid financial input")
        return {
            "schema_version": "test.result.v1",
            "conditional_value": str(int(inputs["revenue"]) * 2),
        }

    model.calculate_financial_model = calculate

    def replay(inputs, baseline, market, cutoff_at, result_schema_version):
        if result_schema_version != "test.result.v1":
            raise ValidationError("financial result version is unsupported")
        return calculate(inputs, baseline, market, cutoff_at)

    model.replay_financial_model = replay
    model.financial_model_market_snapshot = lambda context: {
        "market_at": context.market_at.isoformat(),
        "source": "authenticated existing snapshots",
    }
    monkeypatch.setitem(sys.modules, source.__name__, source)
    monkeypatch.setitem(sys.modules, model.__name__, model)
    return baseline


@pytest.fixture
def frozen(api_client, session):
    workspace = _run_public_company_research_pipeline(
        api_client, session, fixed_model_clock=True
    )
    project_id = workspace["project_id"]
    memo = next(a for a in workspace["artifacts"] if a["kind"] == "memo")
    confirmed = api_client.post(
        f"{BASE}/projects/{project_id}/judgment-confirmations",
        json={
            "expected_lock_version": workspace["draft"]["lock_version"],
            "expected_memo_id": memo["id"],
            "expected_memo_content_hash": memo["content_hash"],
            "markdown": "Preserved frozen research: insufficient reviewed operating inputs.",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    version = confirmed.json()["draft"]["lock_version"]
    preview = api_client.post(
        f"{BASE}/projects/{project_id}/publication-preview",
        json={"expected_lock_version": version},
    )
    assert preview.status_code == 200, preview.text
    published = api_client.post(
        f"{BASE}/projects/{project_id}/publish",
        headers={"Idempotency-Key": "financial-parent"},
        json={
            "expected_lock_version": version,
            "expected_manifest_hash": preview.json()["manifest_hash"],
        },
    )
    assert published.status_code == 201, published.text
    return published.json()


def test_financial_workspace_reads_authenticated_parent_without_changing_it(
    api_client, frozen, numeric_dependencies
):
    project_id = frozen["project_id"]
    before = api_client.get(f"{BASE}/projects/{project_id}/workspace").json()
    response = api_client.get(f"{BASE}/projects/{project_id}/financial-model")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["parent_revision_id"] == frozen["id"]
    assert value["parent_manifest_hash"] == frozen["manifest_hash"]
    assert value["latest"] is None and value["history"] == []
    assert value["baseline"]["content_hash"]
    assert value["market"] is not None
    assert api_client.get(f"{BASE}/projects/{project_id}/workspace").json() == before


def _open(api_client, frozen):
    path = f"{BASE}/projects/{frozen['project_id']}/financial-model"
    response = api_client.get(path)
    assert response.status_code == 200, response.text
    return path, response.json()


def _save(
    api_client, path, workspace, *, key="financial-1", expected=None, inputs=None
):
    return api_client.post(
        path + "/drafts",
        headers={"Idempotency-Key": key},
        json={
            "parent_revision_id": workspace["parent_revision_id"],
            "expected_latest_id": expected,
            "baseline_content_hash": workspace["baseline"]["content_hash"],
            "inputs": inputs or workspace["initial_inputs"],
        },
    )


def test_append_replay_export_and_exact_retry_preserve_frozen_workspace(
    api_client, frozen, numeric_dependencies
):
    path, workspace = _open(api_client, frozen)
    old_workspace = api_client.get(
        f"{BASE}/projects/{frozen['project_id']}/workspace"
    ).json()
    first = _save(api_client, path, workspace)
    assert first.status_code == 201, first.text
    one = first.json()
    assert one["status"] == "unreviewed" and one["sequence"] == 1
    assert one["result"]["conditional_value"] == "40"
    assert one["baseline"] == numeric_dependencies
    assert one["parent_manifest_hash"] == frozen["manifest_hash"]
    assert _save(api_client, path, workspace).json() == one
    second = _save(
        api_client,
        path,
        workspace,
        key="financial-2",
        expected=one["id"],
        inputs={"revenue": "25"},
    )
    assert second.status_code == 201, second.text
    two = second.json()
    assert two["sequence"] == 2 and two["result"]["conditional_value"] == "50"
    assert api_client.get(path + f"/drafts/{one['id']}").json() == one
    current = api_client.get(path).json()
    assert current["latest"] == two and [x["sequence"] for x in current["history"]] == [
        2,
        1,
    ]
    # An old exact request remains idempotent after a newer version exists.
    assert _save(api_client, path, workspace).json() == one
    exported = api_client.get(path + f"/drafts/{one['id']}/export")
    assert exported.status_code == 200, exported.text
    export = exported.json()
    assert export["media_type"] == "text/markdown"
    assert sha256(export["content"].encode()).hexdigest() == export["content_hash"]
    assert (
        one["content_hash"] in export["content"]
        and frozen["manifest_hash"] in export["content"]
    )
    assert "未经审核" in export["content"] and "40" in export["content"]
    assert (
        api_client.get(f"{BASE}/projects/{frozen['project_id']}/workspace").json()
        == old_workspace
    )


def test_conflicts_and_invalid_inputs_do_not_append(
    api_client, frozen, numeric_dependencies
):
    path, workspace = _open(api_client, frozen)
    first = _save(api_client, path, workspace)
    assert first.status_code == 201, first.text
    for response in (
        _save(api_client, path, workspace, key="financial-1", inputs={"revenue": "30"}),
        _save(api_client, path, workspace, key="financial-2"),
    ):
        assert response.status_code == 409, response.text
    invalid = _save(
        api_client,
        path,
        workspace,
        key="invalid",
        expected=first.json()["id"],
        inputs={"revenue": "20", "unexpected": True},
    )
    assert invalid.status_code == 422, invalid.text
    wrong_baseline = deepcopy(workspace)
    wrong_baseline["baseline"]["content_hash"] = "a" * 64
    assert (
        _save(
            api_client,
            path,
            wrong_baseline,
            key="wrong-source",
            expected=first.json()["id"],
        ).status_code
        == 409
    )
    assert len(api_client.get(path).json()["history"]) == 1


def test_nonfrozen_and_cross_project_requests_cannot_read_or_save(
    api_client, session, frozen, numeric_dependencies
):
    from tests.underwriting.test_company_research_persistence import _project

    other = _project(session)
    session.commit()
    path, workspace = _open(api_client, frozen)
    saved = _save(api_client, path, workspace).json()
    other_path = f"{BASE}/projects/{other.id}/financial-model"
    assert api_client.get(other_path).status_code in {404, 422}
    assert api_client.get(other_path + f"/drafts/{saved['id']}").status_code == 404
    assert _save(api_client, other_path, workspace).status_code in {404, 422}
    assert api_client.get(path + f"/drafts/{uuid4()}").status_code == 404


@pytest.mark.parametrize(
    "field", ["inputs", "result", "baseline", "market", "parent_manifest_hash"]
)
def test_tampered_snapshot_cannot_be_replayed_or_exported(
    api_client, session, frozen, numeric_dependencies, field
):
    path, workspace = _open(api_client, frozen)
    from app.underwriting.persistence.company_research_models import (
        CompanyResearchFinancialDraft,
    )
    from tests.underwriting.test_company_research_persistence import _tamper_row

    saved = _save(api_client, path, workspace).json()
    row = session.scalar(select(CompanyResearchFinancialDraft))
    payload = deepcopy(row.payload)
    payload[field] = {"corrupt": True} if field != "parent_manifest_hash" else "a" * 64
    _tamper_row(session, CompanyResearchFinancialDraft, row.id, payload=payload)
    session.commit()
    for suffix in (f"/drafts/{saved['id']}", f"/drafts/{saved['id']}/export", ""):
        response = api_client.get(path + suffix)
        assert response.status_code == 422, response.text


def test_unknown_request_fields_and_missing_idempotency_key_are_rejected(
    api_client, frozen, numeric_dependencies
):
    path, workspace = _open(api_client, frozen)
    body = {
        "parent_revision_id": frozen["id"],
        "expected_latest_id": None,
        "baseline_content_hash": workspace["baseline"]["content_hash"],
        "inputs": workspace["initial_inputs"],
    }
    assert api_client.post(path + "/drafts", json=body).status_code == 422
    assert (
        api_client.post(
            path + "/drafts",
            headers={"Idempotency-Key": "new"},
            json={**body, "human_confirmed": True},
        ).status_code
        == 422
    )


def test_new_save_rejects_a_superseded_frozen_parent_but_exact_retry_still_replays(
    api_client, frozen, numeric_dependencies, monkeypatch
):
    from types import SimpleNamespace

    from app.underwriting.persistence.product_repository import ProductRepository

    path, workspace = _open(api_client, frozen)
    one = _save(api_client, path, workspace).json()
    monkeypatch.setattr(
        ProductRepository,
        "company_research_revision_head",
        lambda self, project_id, **kwargs: SimpleNamespace(id=uuid4()),
    )
    response = _save(
        api_client, path, workspace, key="new-after-revision", expected=one["id"]
    )
    assert response.status_code == 409, response.text
    assert _save(api_client, path, workspace).json() == one


def test_export_leads_with_reviewable_tables_before_replay_json(
    api_client, frozen, numeric_dependencies
):
    path, workspace = _open(api_client, frozen)
    one = _save(api_client, path, workspace).json()
    exported = api_client.get(path + f"/drafts/{one['id']}/export").json()["content"]
    for heading in (
        "情景估值摘要",
        "五年预测",
        "估值参数与假设理由",
        "来源与研究缺口",
        "<details>",
    ):
        assert heading in exported
    assert exported.index("情景估值摘要") < exported.index("<details>")


@pytest.mark.parametrize("field", ["result", "baseline", "market", "request_hash"])
def test_rehashing_a_corrupt_record_does_not_bypass_source_or_calculation_auth(
    api_client, session, frozen, numeric_dependencies, field
):
    from sqlalchemy.orm.attributes import set_committed_value

    from app.underwriting.persistence.company_research_models import (
        CompanyResearchFinancialDraft,
    )
    from app.underwriting.services.company_research_financial_workspace import (
        CompanyResearchFinancialWorkspace,
    )
    from tests.underwriting.test_company_research_persistence import _tamper_row

    path, workspace = _open(api_client, frozen)
    saved = _save(api_client, path, workspace).json()
    row = session.scalar(select(CompanyResearchFinancialDraft))
    payload = deepcopy(row.payload)
    if field == "request_hash":
        set_committed_value(row, "request_hash", "b" * 64)
        changed = {"request_hash": row.request_hash}
    else:
        if field == "result":
            payload[field]["conditional_value"] = "999"
        else:
            payload[field]["corrupt"] = True
        set_committed_value(row, "payload", payload)
        changed = {"payload": payload}
    new_hash = CompanyResearchFinancialWorkspace._content_hash(row)
    _tamper_row(
        session, CompanyResearchFinancialDraft, row.id, content_hash=new_hash, **changed
    )
    session.commit()
    response = api_client.get(path + f"/drafts/{saved['id']}")
    assert response.status_code == 422, response.text


def test_authenticated_frozen_manifest_tampering_blocks_new_financial_reads(
    api_client, session, frozen, numeric_dependencies
):
    from app.underwriting.persistence.product_models import UnderwritingRevisionManifest
    from tests.underwriting.test_company_research_persistence import _tamper_row

    row = session.scalar(select(UnderwritingRevisionManifest))
    _tamper_row(session, UnderwritingRevisionManifest, row.id, content_hash="c" * 64)
    session.commit()
    response = api_client.get(f"{BASE}/projects/{frozen['project_id']}/financial-model")
    assert response.status_code == 422, response.text


def test_real_baseline_and_calculator_integrate_with_frozen_market(api_client, frozen):
    path, workspace = _open(api_client, frozen)
    assert (
        workspace["baseline"]["schema_version"]
        == "company-research.financial-baseline.v1"
    )
    response = _save(api_client, path, workspace, key="real-conditional-model")
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["status"] == "unreviewed"
    assert saved["market"]["snapshot_bindings"]
    assert api_client.get(path + f"/drafts/{saved['id']}").json() == saved
    exported = api_client.get(path + f"/drafts/{saved['id']}/export")
    assert exported.status_code == 200, exported.text
    markdown = exported.json()["content"]
    assert "NASDAQ:GOOG" in markdown and "| 2026 |" in markdown
    assert saved["baseline"]["sources"][0]["url"] in markdown
    assert saved["baseline"]["sources"][0]["raw_content_hash"] in markdown
    assert "机器可重放快照" in markdown and "不是年化收益率" in markdown


def test_newer_valid_market_snapshot_does_not_change_frozen_draft_replay(
    api_client, session, frozen, numeric_dependencies
):
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.underwriting.domain.product_contracts import (
        FxQuoteDirection,
        FXSnapshotInput,
    )
    from app.underwriting.fixtures.alphabet_golden_case import CapturedProvenance
    from app.underwriting.services.company_research_market_inputs import (
        CompanyResearchMarketInputs,
    )
    from app.underwriting.services.market_snapshots import MarketSnapshotService

    path, workspace = _open(api_client, frozen)
    response = _save(api_client, path, workspace)
    assert response.status_code == 201, response.text
    saved = response.json()
    export_path = path + f"/drafts/{saved['id']}/export"
    original_export = api_client.get(export_path).json()
    installed = datetime(2026, 9, 10, tzinfo=UTC)
    available = datetime(2026, 8, 25, 21, tzinfo=UTC)
    source = CapturedProvenance(
        "https://example.test/new-fx",
        "synthetic later available FX observation",
        "d" * 64,
        "test-later-fx.v1",
    )
    market = MarketSnapshotService(session, now=lambda: installed)
    newer = market.freeze_fx(
        FXSnapshotInput(
            base_currency="USD",
            quote_currency="CNY",
            rate=Decimal("7.1"),
            quote_direction=FxQuoteDirection.QUOTE_PER_BASE,
            market_at=datetime(2026, 8, 24, tzinfo=UTC),
            available_at=available,
            source_id=source.source_url,
            raw_hash=source.raw_hash,
        )
    )
    CompanyResearchMarketInputs(session, now=lambda: installed)._persist_capture(
        "fx",
        newer.id,
        "primary",
        source,
        available,
    )
    session.commit()
    # The normal resolver must keep selecting newer eligible observations.
    current = CompanyResearchMarketInputs(session).resolve(
        project_id=UUID(frozen["project_id"]),
        cutoff_at=datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC),
        fresh=True,
    )
    assert current.fx_snapshot_ids == (newer.id,)
    assert (
        api_client.get(
            f"{BASE}/projects/{frozen['project_id']}/revisions/{frozen['id']}"
        ).status_code
        == 200
    )
    replayed = api_client.get(path + f"/drafts/{saved['id']}")
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == saved
    assert api_client.get(export_path).json() == original_export
    assert api_client.get(path).json()["market"] == workspace["market"]


def test_saved_recipe_replays_after_default_baseline_and_calculator_change(
    api_client, frozen, numeric_dependencies, monkeypatch
):
    path, workspace = _open(api_client, frozen)
    response = _save(api_client, path, workspace)
    assert response.status_code == 201, response.text
    saved = response.json()
    export_path = path + f"/drafts/{saved['id']}/export"
    original_export = api_client.get(export_path).json()

    def new_default(*args, **kwargs):
        raise ValidationError("new defaults cannot process the previous version")

    source = sys.modules[
        "app.underwriting.services.company_research_financial_baseline"
    ]
    model = sys.modules["app.underwriting.services.company_research_financial_model"]
    monkeypatch.setattr(source, "load_alphabet_financial_baseline", new_default)
    monkeypatch.setattr(model, "calculate_financial_model", new_default)
    replayed = api_client.get(path + f"/drafts/{saved['id']}")
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == saved
    assert api_client.get(export_path).json() == original_export
    assert _save(api_client, path, workspace).json() == saved


def test_0074_migration_blocks_raw_update_and_delete(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    from app.models.ledger import Base
    from app.underwriting.persistence.company_research_models import (
        CompanyResearchFinancialDraft,
    )

    table = CompanyResearchFinancialDraft.__table__
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    Base.metadata.create_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if t.name != table.name]
    )
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/0074_company_research_financial_drafts.py"
    )
    spec = importlib.util.spec_from_file_location("financial_draft_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.upgrade()
        conn.execute(
            text(
                f"INSERT INTO {table.name} (id,project_id,parent_revision_id,sequence,idempotency_key,request_hash,input_hash,content_hash,payload,created_at) VALUES (:id,:project,:revision,1,'key',:hash,:hash,:hash,'{{}}','2026-09-10 00:00:00')"
            ),
            {
                "id": uuid4().hex,
                "project": uuid4().hex,
                "revision": uuid4().hex,
                "hash": "a" * 64,
            },
        )
        for sql in (f"UPDATE {table.name} SET sequence=2", f"DELETE FROM {table.name}"):
            with pytest.raises(IntegrityError, match="append-only"):
                conn.execute(text(sql))
    engine.dispose()
