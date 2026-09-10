# Alphabet historical financial baseline v1

This independent data package supports the conditional group financial worksheet.
It does not modify the governed evidence fixtures, live source bundle contracts,
historical research scopes, or the formal valuation readiness gate.

`baseline.json` contains six period-specific sets of financial facts: FY2024,
FY2025, Q2 2025, Q2 2026, H1 2025 and H1 2026. Amounts are USD millions;
EPS denominators are millions of weighted-average shares; rates are ratios.
Capital expenditure and repurchase cash outflows use positive magnitudes while
the source quote retains parentheses. Working-capital-related cash flow rows
retain their reported signs and are not asserted to be a DCF change in NWC.

`proofs.json` identifies each reported fact's exact source table anchor, period
header, units, row label, numeric column and sign convention. The loader reads
the originals again, checks the pinned SHA-256 of their uncompressed bytes,
extracts visible text, verifies each quote and number, and recomputes derived
facts using Decimal arithmetic with precision 28. Both JSON commitments are
pinned in the versioned loader. Returned content hashes bind source metadata,
facts, quotes, definitions, formulas, dependencies and research gaps.

Exact-byte text extraction has an immutable LRU cache capped at four sources.
Every call still rereads and verifies the JSON package and each original's hash
before using that cache, and returns a fresh independently mutable JSON object.

The two SEC HTML gzip originals are copied byte-for-byte from the already
governed Alphabet golden-case capture. The two PDFs are copied byte-for-byte
from the official IR disclosures captured and verified on 2026-09-09. The
annual PDF was discovered through https://abc.xyz/investor/default.aspx and
its public financial-report feed. It is a distinct original from the SEC HTML
and has its own hash. Sources are historical documents with the conservative
availability boundaries recorded in the governed sources. Capture time is not
used as the historical publication time. No network is used by the loader.

The Q2 2026 release and 10-Q do not contain a numerical FY2026 capital expenditure
guidance range. The older February 2026 guidance must not be attributed to Q2 or
converted into realized history. Missing operating drivers, RPO comparability,
SBC definitions, FCF versus FCFF, cash-tax and share-denominator limitations are
explicit research gaps rather than manufactured financial observations.
