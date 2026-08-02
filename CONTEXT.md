# Fund Engine Research Context

Fund Engine is an industry-research evidence library that turns source material into auditable thesis assessments, then links those assessments to companies, stocks, funds, and dated holdings disclosures.

## Research

**ResearchCase**:
A persistent research dossier organized around one industry topic. It contains multiple versioned theses, their evidence, assessments, and review history.
_Avoid_: News page, topic feed, one-off report

**Thesis**:
A testable, falsifiable, and time-bounded proposition within a ResearchCase.
_Avoid_: Theme, conclusion, recommendation

**AIAssessment**:
An immutable provisional AI judgment about a Thesis based on a frozen evidence snapshot. Its result is `supported`, `contradicted`, or `insufficient_evidence`, and it remains visibly unreviewed until a human decision exists.
_Avoid_: Final conclusion, confidence score

**ReviewDecision**:
An immutable human decision that confirms, modifies, or rejects a Proposal or an AIAssessment and records the reason without replacing the machine output.
_Avoid_: Edit, approval flag, mutable review state

**Proposal**:
An immutable machine or human suggestion to create a SourceStatement, EvidenceLink, CausalEdge, EntityAlignment, or AIAssessment. A Proposal is not a formal research relationship until a ReviewDecision publishes a reviewed version.
_Avoid_: Evidence, approved relation, automatic fact

**HistoricalBasis**:
The explicit cutoff and ledger/projection watermarks used to answer what information was visible at a point in time across evidence, graph, search, valuation, and holding disclosures.
_Avoid_: Latest state, search snapshot

## Sources and Evidence

**DocumentVersion**:
An immutable version of a source document identified by its content hash, publication time, and acquisition metadata.
_Avoid_: Document, latest file

**SourceSpan**:
An exact, reproducible location inside a DocumentVersion, such as a page region, paragraph, table cell, or character range.
_Avoid_: Citation URL, excerpt without location

**SourceStatement**:
One atomic statement explicitly made by a source, typed as a disclosed fact, management attribution, forecast, or research opinion. It records what the source says, not whether the statement is objectively true.
_Avoid_: Fact, evidence, Claim

**EvidenceLink**:
A versioned argument that explains why a SourceStatement supports, contradicts, or contextualizes a Thesis for a defined time and scope.
_Avoid_: Automatic SUPPORTS edge, semantic similarity

**EvidenceSnapshot**:
The frozen set of DocumentVersions, SourceStatements, and EvidenceLinks visible to one AIAssessment at its cutoff time.
_Avoid_: Current database state

**CausalEdge**:
A proposed transmission relationship between two domain factors with its own evidence requirements. A positive company result or a source attribution does not by itself establish a CausalEdge.
_Avoid_: Correlation, supply-chain adjacency

## Investment Expression

**ThemeRole**:
A company's explicit role in an industry theme or causal chain, including its scope, applicable period, and supporting source.
_Avoid_: Theme membership tag

**HoldingDisclosure**:
A fund's disclosed position in a stock, preserving both the holding report period and the publication date.
_Avoid_: Current holding, real-time position

**Expression**:
A stock or fund used to express exposure to a supported research idea after considering valuation, exposure, freshness, and constraints. It is not a recommendation by itself.
_Avoid_: Pick, recommendation, portfolio

## Implementation Status (2026-08-02)

Four hardening rounds landed on top of the MVP (commits `5963ffb`, `631b9c2`, `95cf64f`, `c472e36`):

**Hybrid recall (P0).** `RecallService` now fuses two legs with RRF: BM25 over coarse tokens plus a local, deterministic char-n-gram TF-IDF dense leg (recovers sub-word matches the whole-CJK-run tokenizer makes BM25-invisible); the lexical signal is fused as a third leg. `mode="bm25"` remains as the evaluation baseline. `backend/scripts/eval_recall_ab.py` replays the frozen AI-compute slice against the human-curated gold links: overall recall@20 0.7333 → 1.0000 (4 gold statements recovered, 0 lost), with a hybrid-below-baseline regression guard. A real embedding backend can replace `tfidf_rank` behind the same contract.

**Second gold case + real PDF path (P1).** New frozen case 锂电储能链 (`seed_storage_chain_case.py`): 6 fixtures — including the first real binary PDF (`06_sungrow_annual_summary.pdf`) — 15 statements, 15 links, 3 theses with human reviews, fund penetration, human causal chain. `app/services/pdf_text.py` parses PDF text layers into reproducible spans (CJK soft-wrap rejoining, table-block line preservation, fail-closed on text-less PDFs; documents stamped `parser_version=pypdf-v1`). The dataset manifest is now v2 (per-case hash sets attributed by `source_url` prefix); the release gate runs 10 checks including `pdf_fixture_parse_gold`.

**Compliance rewrite loop (P2).** The three-action compliance contract is live: REFUSE-category hits refuse immediately and never reach the rewrite stage; REWRITE-category hits (target price / return promise) get exactly one LLM rewrite attempt (`rewrite-v1` prompt), the result is re-evaluated through the same gate, and any residual hit refuses the whole run. Repaired assessments record `rewritten_for_compliance` on the AIRun. 422 still signals a refused rerun to the frontend.

**Research-ops KPIs (P3).** `GET /api/v1/research-ops/kpis?case_id=&as_of=` derives management metrics from the ledger only: review throughput (with pending queue via effective review state), human-AI agreement (assessment- and link-level; null when no data), and judgment latency (evidence→assessment, assessment→first-review, in days). Supports point-in-time replay via `as_of`.

**Quality posture:** 218 backend tests (+24 across the four rounds), release gate 10 checks green via `docs/evaluation/reproduce.sh`, frontend contract regenerated after the KPI endpoint.
