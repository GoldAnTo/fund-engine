# Live Walkthrough Reliability Design

## Goal

Make the authenticated live walkthrough distinguish a genuinely unavailable
provider from an application defect, retry transient provider failures within
an explicit bound, and report historical Case non-existence honestly. The
September 3 extraction hardening extends that same bound with one explicit,
extraction-only malformed-JSON correction policy; other callers remain on the
original terminal-malformed policy.

## Decisions

1. Historical Case reads remain point-in-time correct. A cutoff before the
   Case was created continues to return `404 not_found`; the walkthrough
   records it as `case_not_created_at_cutoff`, rather than an operational
   failure.
2. The extraction command maps only `LLMProviderError` and
   `LLMMalformedResponseError` to `503 upstream_unavailable`. A model candidate
   that fails the ledger's quote-continuity validation is discarded like other
   malformed candidate fields; programming and persistence errors remain 500.
3. `LLMClient` performs a small, configurable number of retries for its
   provider exception boundary. Every attempt has the existing finite timeout;
   a final failure still raises the stable safe error and creates one failed
   `AIRun` through the existing extractor transaction. Malformed responses are
   terminal unless a caller explicitly supplies a fixed correction protocol;
   only `StatementExtractor` does so, under the same total attempt budget.
4. The Gildata client uses the same bounded retry policy for transport errors
   only. Protocol and application-level response errors are not retried.
5. The walkthrough preserves both ledger review gates: it confirms atomic
   claim candidates before they publish `SourceStatement`s, and confirms
   `evidence_link` proposals before it reviews the published `EvidenceLink`s.
   A proposal identifier is never treated as an evidence-link identifier.
6. Assessment review is constrained to its frozen evidence snapshot. A later
   factual cross-check remains an audit observation and never upgrades a
   zero-evidence assessment to a directional conclusion.

## Verification

- Unit tests prove the shared retry count, default no-retry behavior for
  malformed protocol data, extraction-only correction, and preservation of the
  safe public error.
- API tests prove historical pre-creation reads are represented as an expected
  walkthrough observation and that genuine programming errors still return
  500.
- Walkthrough support tests fix the payload contracts for both explicit human
  review stages.
- The live Cambricon walkthrough runs from P0 through P12 with configured
  credentials. Its summary reports provider outcomes separately from
  application defects and never prints credentials or licensed request URLs.
