"""Version conditional valuation inputs without mutating published research."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import ConflictError, ValidationError
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchFinancialDraft,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_market_inputs import (
    CompanyResearchMarketInputs,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchPublicationService,
)

_PAYLOAD_FIELDS = {
    "schema_version",
    "parent_manifest_hash",
    "cutoff_at",
    "status",
    "baseline",
    "market",
    "inputs",
    "result",
}
_SCHEMA = "company-research.financial-draft.v1"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class CompanyResearchFinancialWorkspace:
    """Authenticate frozen ancestry, persist drafts and replay deterministic outputs."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]):
        self._session = session
        self._now = now
        self._products = ProductRepository(session)

    def _parent(self, project_id: UUID, revision_id: UUID | None = None):
        if self._products.project(project_id) is None:
            raise NotFoundError("company research project not found")
        if revision_id is None:
            head = self._products.company_research_revision_head(project_id)
            if head is None:
                raise ValidationError(
                    "financial drafts require a frozen company research revision"
                )
            revision_id = head.id
        frozen = CompanyResearchPublicationService(
            self._session, now=self._now
        ).revision(project_id, revision_id)
        if frozen.company.external_key != "US:ALPHABET:COMPANY":
            raise ValidationError("financial drafts currently support only Alphabet")
        return frozen

    def _snapshots(self, frozen):
        from app.underwriting.services.company_research_financial_baseline import (
            load_alphabet_financial_baseline,
        )

        baseline = load_alphabet_financial_baseline(frozen.cutoff_at)
        if not isinstance(baseline, dict) or baseline.get(
            "content_hash"
        ) != canonical_hash({k: v for k, v in baseline.items() if k != "content_hash"}):
            raise ValidationError("financial baseline content hash is invalid")
        return baseline, self._frozen_market(frozen)

    def _frozen_market(self, frozen):
        from app.underwriting.persistence.company_research_repository import (
            CompanyResearchRepository,
        )
        from app.underwriting.services.company_research_financial_model import (
            financial_model_market_snapshot,
        )

        judgment = next(a for a in frozen.artifacts if a.kind == "judgment_context")
        row = self._session.get(CompanyResearchArtifactVersion, judgment.id)
        bindings = row.payload["_lineage"]["market_snapshot_bindings"]
        if not bindings:
            return None
        context = CompanyResearchMarketInputs(
            self._session, now=self._now
        ).resolve_frozen(
            project_id=frozen.project_id,
            cutoff_at=frozen.cutoff_at,
            bindings=tuple(
                CompanyResearchRepository.market_binding_from_payload(binding)
                for binding in bindings
            ),
            frozen_company_id=frozen.company.object_id,
            frozen_security_ids={
                security.external_key: security.object_id
                for security in frozen.securities
            },
        )
        return financial_model_market_snapshot(context)

    def _rows(self, project_id: UUID):
        return list(
            self._session.scalars(
                select(CompanyResearchFinancialDraft)
                .where(
                    CompanyResearchFinancialDraft.project_id == project_id,
                )
                .order_by(CompanyResearchFinancialDraft.sequence)
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _input_hash(project_id, parent_revision_id, payload):
        return canonical_hash(
            {
                "schema_version": "company-research.financial-input.v1",
                "project_id": str(project_id),
                "parent_revision_id": str(parent_revision_id),
                **{
                    k: payload[k]
                    for k in (
                        "parent_manifest_hash",
                        "cutoff_at",
                        "baseline",
                        "market",
                        "inputs",
                    )
                },
            }
        )

    @staticmethod
    def _request_hash(
        project_id,
        parent_revision_id,
        expected_latest_id,
        baseline_content_hash,
        inputs,
    ):
        return canonical_hash(
            {
                "project_id": str(project_id),
                "parent_revision_id": str(parent_revision_id),
                "expected_latest_id": str(expected_latest_id)
                if expected_latest_id
                else None,
                "baseline_content_hash": baseline_content_hash,
                "inputs": inputs,
            }
        )

    @staticmethod
    def _content_hash(row):
        return canonical_hash(
            {
                "id": str(row.id),
                "project_id": str(row.project_id),
                "parent_revision_id": str(row.parent_revision_id),
                "sequence": row.sequence,
                "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
                "parent_content_hash": row.parent_content_hash,
                "idempotency_key": row.idempotency_key,
                "request_hash": row.request_hash,
                "input_hash": row.input_hash,
                "created_at": _utc(row.created_at).isoformat(),
                "payload": row.payload,
            }
        )

    @staticmethod
    def _record(row):
        return {
            "id": str(row.id),
            "project_id": str(row.project_id),
            "parent_revision_id": str(row.parent_revision_id),
            "sequence": row.sequence,
            "created_at": _utc(row.created_at).isoformat(),
            "input_hash": row.input_hash,
            "content_hash": row.content_hash,
            **deepcopy({k: v for k, v in row.payload.items() if k != "schema_version"}),
        }

    def _replay(self, rows, *, until: UUID | None = None):
        from app.underwriting.services.company_research_financial_baseline import (
            authenticate_financial_baseline_snapshot,
        )
        from app.underwriting.services.company_research_financial_model import (
            replay_financial_model,
        )

        parents = {}
        records = []
        previous = None
        for sequence, row in enumerate(rows, 1):
            payload = row.payload
            if (
                not isinstance(payload, dict)
                or set(payload) != _PAYLOAD_FIELDS
                or payload.get("schema_version") != _SCHEMA
                or payload.get("status") != "unreviewed"
                or row.sequence != sequence
                or row.supersedes_id != (previous.id if previous else None)
                or row.parent_content_hash
                != (previous.content_hash if previous else None)
                or row.content_hash != self._content_hash(row)
                or row.input_hash
                != self._input_hash(row.project_id, row.parent_revision_id, payload)
            ):
                raise ValidationError(
                    "financial draft content or version chain is invalid"
                )
            if not isinstance(
                payload["baseline"], dict
            ) or row.request_hash != self._request_hash(
                row.project_id,
                row.parent_revision_id,
                row.supersedes_id,
                payload["baseline"].get("content_hash"),
                payload["inputs"],
            ):
                raise ValidationError("financial draft request binding is invalid")
            if row.parent_revision_id not in parents:
                frozen = self._parent(row.project_id, row.parent_revision_id)
                parents[row.parent_revision_id] = (frozen, self._frozen_market(frozen))
            frozen, market = parents[row.parent_revision_id]
            baseline = authenticate_financial_baseline_snapshot(
                payload["baseline"], frozen.cutoff_at
            )
            if (
                payload["parent_manifest_hash"] != frozen.manifest_hash
                or payload["cutoff_at"] != frozen.cutoff_at.isoformat()
                or payload["baseline"] != baseline
                or payload["market"] != market
            ):
                raise ValidationError(
                    "financial draft snapshots differ from authenticated sources"
                )
            if not isinstance(payload["result"], dict):
                raise ValidationError("financial draft result is invalid")
            calculated = replay_financial_model(
                inputs=deepcopy(payload["inputs"]),
                baseline=deepcopy(payload["baseline"]),
                market=deepcopy(payload["market"]),
                cutoff_at=frozen.cutoff_at,
                result_schema_version=payload["result"].get("schema_version"),
            )
            if calculated != payload["result"]:
                raise ValidationError("financial draft result does not match replay")
            records.append(self._record(row))
            if row.id == until:
                return records
            previous = row
        if until is not None:
            raise NotFoundError("financial draft not found")
        return records

    def read(self, project_id: UUID) -> dict:
        from app.underwriting.services.company_research_financial_model import (
            candidate_financial_inputs,
        )

        with self._session.no_autoflush:
            frozen = self._parent(project_id)
            baseline, market = self._snapshots(frozen)
            records = self._replay(self._rows(project_id))
            return {
                "project_id": str(project_id),
                "parent_revision_id": str(frozen.id),
                "parent_manifest_hash": frozen.manifest_hash,
                "cutoff_at": frozen.cutoff_at.isoformat(),
                "baseline": baseline,
                "market": market,
                "initial_inputs": candidate_financial_inputs(
                    baseline=deepcopy(baseline), cutoff_at=frozen.cutoff_at
                ),
                "latest": records[-1] if records else None,
                "history": [
                    {
                        k: r[k]
                        for k in (
                            "id",
                            "sequence",
                            "created_at",
                            "input_hash",
                            "content_hash",
                        )
                    }
                    for r in reversed(records)
                ],
            }

    def draft(self, project_id: UUID, draft_id: UUID) -> dict:
        with self._session.no_autoflush:
            rows = self._rows(project_id)
            if not any(row.id == draft_id for row in rows):
                raise NotFoundError("financial draft not found")
            return self._replay(rows, until=draft_id)[-1]

    def save(
        self,
        *,
        project_id: UUID,
        parent_revision_id: UUID,
        expected_latest_id: UUID | None,
        baseline_content_hash: str,
        inputs: dict,
        idempotency_key: str,
    ) -> dict:
        from app.underwriting.services.company_research_financial_model import (
            calculate_financial_model,
        )

        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or idempotency_key != idempotency_key.strip()
            or len(idempotency_key) > 255
        ):
            raise ValidationError("financial draft idempotency key is invalid")
        if not isinstance(inputs, dict):
            raise ValidationError("financial model inputs must be an object")
        self._products.lock_project(project_id)
        frozen = self._parent(project_id, parent_revision_id)
        request_hash = self._request_hash(
            project_id,
            parent_revision_id,
            expected_latest_id,
            baseline_content_hash,
            inputs,
        )
        existing = self._session.scalar(
            select(CompanyResearchFinancialDraft)
            .where(
                CompanyResearchFinancialDraft.idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            if (
                existing.project_id != project_id
                or existing.request_hash != request_hash
            ):
                raise ConflictError(
                    "financial draft idempotency key has different inputs"
                )
            return self.draft(project_id, existing.id)
        current_parent = self._products.company_research_revision_head(
            project_id, lock=True
        )
        if current_parent is None or current_parent.id != parent_revision_id:
            raise ConflictError(
                "financial draft parent is no longer the current frozen revision"
            )
        rows = self._rows(project_id)
        self._replay(rows)
        previous = rows[-1] if rows else None
        if expected_latest_id != (previous.id if previous else None):
            raise ConflictError("financial draft expected latest version is stale")
        baseline, market = self._snapshots(frozen)
        if baseline_content_hash != baseline["content_hash"]:
            raise ConflictError("financial baseline has changed; reload before saving")
        result = calculate_financial_model(
            inputs=deepcopy(inputs),
            baseline=deepcopy(baseline),
            market=deepcopy(market),
            cutoff_at=frozen.cutoff_at,
        )
        payload = {
            "schema_version": _SCHEMA,
            "parent_manifest_hash": frozen.manifest_hash,
            "cutoff_at": frozen.cutoff_at.isoformat(),
            "status": "unreviewed",
            "baseline": baseline,
            "market": market,
            "inputs": deepcopy(inputs),
            "result": result,
        }
        row = CompanyResearchFinancialDraft(
            id=uuid4(),
            project_id=project_id,
            parent_revision_id=parent_revision_id,
            sequence=previous.sequence + 1 if previous else 1,
            supersedes_id=previous.id if previous else None,
            parent_content_hash=previous.content_hash if previous else None,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_hash=self._input_hash(project_id, parent_revision_id, payload),
            payload=payload,
            created_at=_utc(self._now()),
        )
        row.content_hash = self._content_hash(row)
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def export(self, project_id: UUID, draft_id: UUID) -> dict:
        value = self.draft(project_id, draft_id)
        lines = [
            "# Alphabet 条件估值模型草稿",
            "",
            "状态：未经审核的条件情景；不修改原冻结报告，不构成正式修订或投资结论。",
            "",
            f"- 草稿：{value['id']}（序号 {value['sequence']}）",
            f"- 原冻结版本：{value['parent_revision_id']}",
            f"- 原报告 Manifest hash：{value['parent_manifest_hash']}",
            f"- 资料截止：{value['cutoff_at']}",
            f"- 输入 hash：{value['input_hash']}",
            f"- 内容 hash：{value['content_hash']}",
            "",
            "集团口径；金额为百万美元，每股价值为美元。价值/价格差不是年化收益率。",
            "",
            "## 情景估值摘要",
            "",
            "| 情景 | 企业价值（百万美元） | 证券 | 每股价值（美元） | 截止口径股价（美元） | 价值/价格差 |",
            "| --- | ---: | --- | ---: | ---: | ---: |",
        ]
        names = {"base": "基准", "bull": "乐观", "bear": "悲观"}
        result = value["result"]
        inputs = value["inputs"]
        for scenario in result.get("scenarios", []):
            name = names.get(scenario["scenario_id"], scenario["scenario_id"])
            securities = scenario.get("securities") or [{}]
            for security in securities:
                lines.append(
                    "| "
                    + " | ".join(
                        (
                            name,
                            _number(scenario.get("enterprise_value_usd_million")),
                            _markdown(
                                security.get("security_external_key", "市场快照不可用")
                            ),
                            _number(security.get("value_usd_per_share")),
                            _number(security.get("market_price_usd")),
                            _number(
                                security.get("value_price_gap_ratio"), percent=True
                            ),
                        )
                    )
                    + " |"
                )
        lines.extend(
            [
                "",
                "## 五年预测",
                "",
                "所有前瞻数值均为研究假设；集团FCFF不代表Cloud分部FCF。",
                "",
            ]
        )
        for scenario in result.get("scenarios", []):
            lines.extend(
                [
                    f"### {names.get(scenario['scenario_id'], scenario['scenario_id'])}",
                    "",
                    _markdown(scenario["mechanism"]),
                    "",
                    "| 年度 | 收入 | 营业利润 | 折旧 | 资本开支 | 营运资本净投入 | FCFF |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for row in scenario.get("rows", []):
                lines.append(
                    "| "
                    + " | ".join(
                        (
                            str(row["fiscal_year"]),
                            *(
                                _number(row.get(key))
                                for key in (
                                    "revenue",
                                    "operating_income",
                                    "depreciation",
                                    "capex",
                                    "working_capital_change",
                                    "fcff",
                                )
                            ),
                        )
                    )
                    + " |"
                )
            lines.extend(
                [
                    "",
                    f"终值FCFF：{_number(scenario.get('terminal_fcff_usd_million'))}；终值占企业价值：{_number(scenario.get('terminal_value_share'), percent=True)}。",
                    "",
                ]
            )
        lines.extend(["## 估值参数与假设理由", "", "| 参数 | 假设 |", "| --- | ---: |"])
        for key, label in (
            ("discount_rate", "集团FCFF贴现率"),
            ("terminal_growth", "永续增长率"),
            ("terminal_roic", "终值增量投入资本回报率"),
            ("first_year_cash_flow_fraction", "首年剩余现金流比例"),
        ):
            lines.append(f"| {label} | {_number(inputs.get(key), percent=True)} |")
        for key, label in (
            ("discount_rate_rationale", "贴现率依据"),
            ("terminal_rationale", "终值依据"),
            ("timing_rationale", "首年现金流时点依据"),
        ):
            if inputs.get(key):
                lines.extend(["", f"{label}：{_markdown(inputs[key])}"])
        labels = {
            "revenue": "收入",
            "operating_margin": "经营利润率",
            "cash_tax_rate": "经营现金税率",
            "depreciation": "折旧",
            "capex": "资本开支",
            "working_capital_change": "营运资本净投入",
        }
        for scenario in inputs.get("scenarios", []):
            lines.extend(
                [
                    "",
                    f"### {names.get(scenario['scenario_id'], scenario['scenario_id'])}假设依据",
                    "",
                ]
            )
            for key, rationale in scenario.get("rationales", {}).items():
                lines.append(f"- {labels.get(key, key)}：{_markdown(rationale)}")
        for policy in result.get("policies", []):
            lines.extend(["", _markdown(policy)])
        lines.extend(["", "## 来源与研究缺口", ""])
        for source in value["baseline"].get("sources", []):
            lines.append(
                f"- [{_markdown(source['title'])}]({source['url']})；可用时间 {_markdown(source['available_at'])}；原件 SHA-256：{source['raw_content_hash']}"
            )
        if value["market"] is not None:
            lines.extend(["", "### 已冻结市场来源", ""])
            for binding in value["market"].get("snapshot_bindings", []):
                source = binding["source_ref"]
                lines.append(
                    f"- [{_markdown(source['source_role'])}]({source['source_url']})：{_markdown(source['source_locator'])}；快照内容 hash：{binding['snapshot_content_hash']}"
                )
        lines.append("")
        for warning in result.get("warnings", []):
            lines.append(f"- {_markdown(warning)}")
        # A user-entered rationale cannot close the replay block with backticks.
        snapshot = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        fence = "`" * max(
            3, 1 + max((len(run) for run in re.findall(r"`+", snapshot)), default=0)
        )
        lines.extend(
            [
                "",
                "<details>",
                "<summary>机器可重放快照</summary>",
                "",
                f"{fence}json",
                snapshot,
                fence,
                "",
                "</details>",
                "",
            ]
        )
        content = "\n".join(lines)
        return {
            "filename": f"alphabet-conditional-model-{draft_id}.md",
            "media_type": "text/markdown",
            "content": content,
            "content_hash": sha256(content.encode("utf-8")).hexdigest(),
        }


def _markdown(value: object) -> str:
    return (
        html.escape(str(value), quote=False)
        .replace("|", "&#124;")
        .replace("\n", " ")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _number(value: object, *, percent: bool = False) -> str:
    if value is None:
        return "—"
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "—"
        return f"{number * 100:,.2f}%" if percent else f"{number:,.2f}"
    except InvalidOperation:
        return "—"
