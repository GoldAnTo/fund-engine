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
