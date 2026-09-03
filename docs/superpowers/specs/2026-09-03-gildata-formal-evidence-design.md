# Gildata Formal Evidence Admission Design

## Goal

Let material retrieved with the configured Gildata licence move through the
normal research path — frozen document, atomic-claim review, evidence proposal,
human proposal decision, and formal `EvidenceLink` publication — without
weakening URL validation for untrusted sources.

## Chosen approach

`gildata://` remains an internal, stable provider reference.  It is not
rewritten to a made-up public HTTP URL.  Formal admission is instead granted
only when its immutable `SourceContract` declares a live, authorised Gildata
licence with both AI-processing and display rights.

The alternative of treating every non-HTTP URL as valid would weaken the source
boundary.  Replacing a provider URI with a synthetic HTTP URL would make audit
and replay dishonest.  Requiring a public URL for all provider material would
discard the licensed-provider workflow that the source policy already models.

## Data flow

1. The Gildata ingest command freezes each result with its existing provider
   URI and records a `SourceContract` of type `licensed_provider` plus a
   provider record.  The contract is created only from explicit local
   deployment configuration declaring the licence rights; absent or false
   rights fail closed.
2. Evidence-proposal admission first resolves that contract.  A valid,
   active, rights-granting Gildata contract admits its provider URI.  All other
   sources continue through the existing HTTP(S), public-host and parser checks.
3. Proposal review publishes an `EvidenceLink` only when this contract-aware
   admission permits it.  Review queue status exposes the actual decision.
4. The live walkthrough sets no rights itself.  It consumes deployment-owned
   configuration and reports missing authorisation as a configuration failure,
   never as an implicit approval.

## Configuration

The deployment declares the Gildata licence through explicit booleans, both
defaulting to false:

```dotenv
GILDATA_ALLOW_AI_PROCESSING=true
GILDATA_ALLOW_DISPLAY=true
```

The token authorises calls to the provider; these declarations capture the
separate downstream-use right.  Neither value nor the token is emitted in
logs, summaries, or commits.

## Verification

- A contract-backed `gildata://` document with both rights publishes a formal
  evidence link through the real proposal decision endpoint.
- Missing, expired, or restricted contracts keep the provider URI inadmissible.
- Generic invalid non-HTTP URLs remain rejected.
- The live walkthrough confirms the full chain with configured credentials and
  reports provider outages separately from admission failures.
