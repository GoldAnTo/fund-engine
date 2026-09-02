# Live Walkthrough Reliability Design

## Goal

Make the authenticated live walkthrough distinguish a genuinely unavailable
provider from an application defect, retry only transient provider failures
within an explicit bound, and report historical Case non-existence honestly.

## Decisions

1. Historical Case reads remain point-in-time correct. A cutoff before the
   Case was created continues to return `404 not_found`; the walkthrough
   records it as `case_not_created_at_cutoff`, rather than an operational
   failure.
2. The extraction command maps only `LLMProviderError` and
   `LLMMalformedResponseError` to `503 upstream_unavailable`. Programming and
   persistence errors remain `500` so they cannot be hidden as provider
   incidents.
3. `LLMClient` performs a small, configurable number of retries for its
   provider exception boundary. Every attempt has the existing finite timeout;
   a final failure still raises the stable safe error and creates one failed
   `AIRun` through the existing extractor transaction.
4. The Gildata client uses the same bounded retry policy for transport errors
   only. Protocol and application-level response errors are not retried.

## Verification

- Unit tests prove retry count, no retry for malformed protocol data, and
  preservation of the safe public error.
- API tests prove historical pre-creation reads are represented as an expected
  walkthrough observation and that genuine programming errors still return
  500.
- The live Cambricon walkthrough runs from P0 through P12 with configured
  credentials. Its summary reports provider outcomes separately from
  application defects and never prints credentials or licensed request URLs.
