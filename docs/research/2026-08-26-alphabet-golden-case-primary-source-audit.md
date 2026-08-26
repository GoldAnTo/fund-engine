# Alphabet golden-case: primary-source audit

**Purpose.** This note records only first-party/authoritative raw materials that
may be used for Task 7's bundled Alphabet fixture.  It does not establish an
investment view.  The fixture source cutoff is **2026-08-25**.

## Source-register policy

- A value is fixture-safe only when its source role, period, unit/currency,
  locator and raw-file SHA-256 can travel with the fact.
- The original publisher gives calendar dates, but not a publication time with
  timezone, for the two documents below.  If the contract requires datetimes,
  the loader should use the documented conservative convention
  `YYYY-MM-DDT23:59:59Z` for both `published_at` and `available_at`; it must
  also retain the publisher's original date.  These documents are safely before
  the 2026-08-25 cutoff under that convention.
- The hashes below are SHA-256 over a direct byte download on 2026-08-26.
  Fixture installation must download/check again; this note is not a trusted
  root.

## 1. Regulatory filing

| Field | Value |
| --- | --- |
| Source role | `regulatory_filing` |
| Document | Alphabet Inc., Form 10-K, fiscal year ended 2025-12-31; CIK 0001652044; accession 0001652044-26-000018 |
| Source URL | [SEC filing detail](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/0001652044-26-000018-index.htm); [primary HTML](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm) |
| Published / available | Filed 2026-02-05; SEC accepted 2026-02-04 21:56:03 (filing detail page). For a date-only fixture convention: `2026-02-05T23:59:59Z`. |
| Raw SHA-256 | `c2f6301004f35411a20611c14ff01d80a85c0bcbab6053c80d8cc7f6fc747161` |

Fixture candidates (all dollar values are USD millions; annual values have
`period_start=2025-01-01`, `period_end=2025-12-31`):

| Claim / value | Exact source locator | Safe as fixture fact? |
| --- | --- | --- |
| FY25 revenue by source: Google Search & other `224,532`; YouTube ads `40,367`; Google subscriptions, platforms, and devices `48,030`; Google Cloud `58,705`; Other Bets `1,537`; consolidated revenue `402,836`. | Item 7, *Results of Operations*, Revenue by type table, pp. 32-33. | Yes — `reported`, USD million, annual. |
| FY25 segment revenue: Google Services `342,721`; Google Cloud `58,705`; Other Bets `1,537`. Segment operating income/(loss): `139,404`, `13,910`, `(7,515)` respectively. | Item 8, Note 15 *Segment Information*, pp. 86-87. | Yes — `reported`, USD million, annual. |
| FY25 net cash from operating activities `164,713`; capital expenditures `91,447`; Class A + Class C stock repurchases `45,398`. | Item 7, *Liquidity and Capital Resources*, p. 32 (and the related cash-flow / repurchase tables). | Yes — `reported`, USD million, annual; capex belongs in the corporate-capital-allocation module. |
| Google Services earns principally from advertising on Search & other, YouTube and Google Network; subscriptions/platforms/devices include YouTube services, Google One, Google Play and Pixel. Cloud principally earns consumption and subscription fees. Other Bets principally earns autonomous-transportation and internet-services revenue. | Item 1, *Business*, pp. 4-5. | Yes, as a source-backed descriptive string in the business map — not as a numeric forecast input. |
| GOOGL is Class A common stock and GOOG is Class C capital stock; Class A carries one vote per share and Class C carries no voting rights; their rights otherwise match. | Cover page (registered securities); Item 8, Note 11 *Stockholders' Equity*, pp. 77-78. | Yes, but only as security-specific rights metadata. It must not be treated as a Company-level per-share valuation fact. |

## 2. Alphabet earnings material

| Field | Value |
| --- | --- |
| Source role | `company_material` |
| Document | *Alphabet Announces Fourth Quarter and Fiscal Year 2025 Results* |
| Source URL | [Alphabet IR announcement](https://abc.xyz/investor/news/news-details/2026/Alphabet-Announces-Fourth-Quarter-2025-and-Fiscal-Year-Results-2026-KEvZIMKBLS/default.aspx); [official linked earnings-release PDF](https://s206.q4cdn.com/479360582/files/doc_news/2026/Feb/04/attachments/2025q4-alphabet-earnings-release.pdf) |
| Published / available | 2026-02-04 (the IR announcement); date-only fixture convention: `2026-02-04T23:59:59Z`. |
| Raw SHA-256 | `8bb2778772fc60c0290cfeac3b24d345a07b8918a7708ec8fcef7239243922f9` |

| Claim / value | Exact source locator | Safe as fixture fact? |
| --- | --- | --- |
| Q4 FY25 revenue `113,828`; Google Services `95,862`; Search & other `63,073`; YouTube ads `11,383`; subscriptions/platforms/devices `13,578`; Cloud `17,664`; Other Bets `370`. | Earnings-release PDF, p. 2, *Q4 2025 Supplemental Information*. | Yes — `reported`, USD million, Q4 period. |
| FY25 revenue `402,836`; operating income `129,039`; net income `132,170`; diluted EPS `10.81`. | PDF, p. 1, *Q4 2025 Financial Highlights*; also pp. 5-6 financial statements. | Yes — `reported`; use the 10-K as the preferred source when a duplicate audited annual fact exists. |
| FY25 cash and cash equivalents `30,708`; marketable securities `96,135`; property and equipment `246,597`; long-term debt `46,547`. | PDF, p. 4, consolidated balance sheets. | Yes — `reported`, USD million, point-in-time 2025-12-31. |
| FY25 operating cash flow `164,713`; purchases of property and equipment `91,447`; depreciation of property and equipment `21,136`. | PDF, p. 6, consolidated cash flows. | Yes — `reported`, USD million, annual. |
| YouTube 2025 ads plus subscriptions exceeded `$60bn`; Cloud ended 2025 above a `$70bn` annual run rate; 2026 CapEx is anticipated at `$175bn–$185bn`. | PDF, p. 1, CEO statement / highlights. | Only conditionally: preserve as `management_guidance`/management statement, its forward-looking nature and wording. Do **not** convert it into a reported historical value or an unconstrained valuation input. |

## Regulatory boundary

No `official_regulator` fact is proposed for the minimal fixture.  The 10-K
mentions regulatory and antitrust risk, but it is not a substitute for the
underlying regulator decision.  If a future fixture makes a regulatory claim
(for example, a court-ordered remedy, fine, or binding obligation), it must
carry the original regulator/court document as its own `official_regulator`
source, exact locator and raw hash.  Do not derive such a claim from Alphabet's
risk-factor prose.

## Safe fixture scope

The two sources cover the required business modules without inventing market
data: Search/ads, YouTube, Cloud, subscriptions/platforms/devices (other
services), Other Bets, and corporate capital allocation.  They do **not**
provide a validated market price, FX rate, forward five-year operating model,
or a regulatory-decision fact.  Those must remain explicit `ResearchGap`s (or
be supplied later through separately authenticated sources), rather than being
silently filled with a mock or a derived assumption.
