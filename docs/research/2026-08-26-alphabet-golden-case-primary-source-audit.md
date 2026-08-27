# Alphabet golden-case: primary-source audit

**Purpose.** This note records the first-party/authoritative raw materials,
market-freeze policies, and separately labelled machine assumptions used by
Task 7's bundled Alphabet fixture. It does not establish an investment view.
The exact fixture cutoff is **2026-08-25T23:59:59Z**.

## Source-register policy

- A value is fixture-safe only when its source role, period, unit/currency,
  locator and raw-file SHA-256 can travel with the fact.
- The original publisher gives calendar dates, but not a publication time with
  timezone, for the two documents below.  If the contract requires datetimes,
  the loader should use the documented conservative convention
  `YYYY-MM-DDT23:59:59Z` for both `published_at` and `available_at`; it must
  also retain the publisher's original date.  These documents are safely before
  the 2026-08-25 cutoff under that convention.
- The raw files were captured with `Accept-Encoding: identity`, verified
  against an independently recorded byte length and SHA-256, and then stored as
  deterministic `gzip` sidecars (`mtime=0`). Runtime fixture loading is fully
  offline. The manifest pins both the compressed-file SHA-256 and the
  decompressed raw SHA-256/size before any market member is accepted.

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

## 3. Frozen market inputs

The market resolver uses only immutable snapshot rows prepared from the
authenticated bundle. It performs no browser form submission and no network
fetch. Nasdaq availability at `2026-08-25T20:00:00Z` is an explicit provider
policy for the official close, not a timestamp reported inside the payload.

| Capture | Value / locator | Raw bytes / raw SHA-256 | Gzip SHA-256 |
| --- | --- | --- | --- |
| Nasdaq GOOG official close | `343.34 USD`; `rows[08/25/2026].close`; [official history endpoint](https://api.nasdaq.com/api/quote/GOOG/historical?assetclass=stocks&fromdate=2026-08-24&todate=2026-08-25&limit=10) | `492`; `29e981036b84e13b09724c2d839e1253459c0535c0c9770eb9cb3b5f2a24fe4a` | `1d3a8ddb7f9bfb5f3796e374286342c5464592795d686fa41869dfc46ca005b4` |
| Nasdaq GOOGL official close | `346.96 USD`; `rows[08/25/2026].close`; [official history endpoint](https://api.nasdaq.com/api/quote/GOOGL/historical?assetclass=stocks&fromdate=2026-08-24&todate=2026-08-25&limit=10) | `493`; `c75b9b645e16992c0d5a778517900173ace83d1d3cb76400e68d890a6ba23a6c` | `9a1936ff929051de753954ee8e11393abff327c0186e3ad79bb6eb6716350a1a` |
| Federal Reserve H.10 USD/CNY | `6.7210` CNY per USD on 2026-08-21; locator `H10/H10/RXI_N.B.CH`; [official DDP CSV, lastobs=10](https://www.federalreserve.gov/datadownload/Output.aspx?filetype=csv&from=&label=include&lastobs=10&layout=seriescolumn&rel=H10&series=60f32914ab61dfab590e0e470153e3ae&to=&type=package); available 2026-08-24T20:15:09Z | `3,672`; `0377197ee8f124a336bbad0cb3b0a3ee08db0671232db438a452aa030dc13682` | `258ee0df12f2300994a0d4f84c97d91738306353fa4a2e5f13d89affae0d8cb2` |
| Alphabet Q2 2026 exhibit 99.1 | Balance sheet, outstanding-share counts, and statements-of-income EPS denominators; [SEC exhibit](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000066/googexhibit991q22026.htm); available 2026-07-22T20:02:09Z | `371,494`; `a01f6bd87c7fa0dcb562493dda7348a1a37d017b4a4b5edb39b915b45688237e` | `642866bb873c7497038040a0961400f76a66f7e35334ecd59e182f2765a7d1d4` |
| Alphabet Q2 2026 Form 10-Q | Notes 3, 6, 7, and 9; [SEC filing](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm); available 2026-07-23T10:04:02Z | `2,464,133`; `fecfbc2683f630380b17937278ce3745eca150eb90e21a945fd6b78fe19728c7` | `c1b712a1bc32a53376a8d811fc005a4a242c1e2accd0c5136b0eb7c2533dd641` |
| Alphabet 2025 Form 10-K | Note 11 legal rights, voting and conversion; [SEC filing](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm); available 2026-02-05T11:04:47Z | `2,616,499`; `c2f6301004f35411a20611c14ff01d80a85c0bcbab6053c80d8cc7f6fc747161` | `861545fb50f2d68c242f928f76d569816a14dd1c2b1f61b18c7100580a0f0bf9` |

### Share-class and per-share policy

At 2026-06-30 the actual outstanding counts were Class A `5,868m`,
Class B `835m`, and Class C `5,527m`, totaling basic shares of `12,230m`.
The Q2 diluted weighted-average EPS denominator was `12,309m`; the basic EPS
weighted-average denominator of `12,151m` is retained only as an audit
distinction and is not substituted for the actual outstanding count.

Class B is not folded into the GOOGL/Class A rights record: it retains its ten
votes and conversion-to-Class-A legal identity. It has no listed market quote,
so the frozen-equity bridge applies an explicit **GOOGL price proxy only**:

`market equity = (Class A 5,868 + Class B 835) × GOOGL 346.96 + Class C 5,527 × GOOG 343.34 = 4,223,313.06 USDm`.

Company equity value for either listed class is divided by the company
`diluted_shares` denominator, then adjusted by that security's typed conversion,
ADR, and dividend-rights ratios. Alphabet's listed Class A and Class C economic
ratios are all one, so their intrinsic per-share values are equal; their return
ranges can differ because their observed market prices differ.

### Capital bridge policy

Policy `alphabet-capital-bridge.v1` records: cash `55,911`; investments
`318,024` (`186,563` marketable + `131,461` non-marketable); debt carrying value
including current `100,164` (`101,085` face less `921` unamortized); minority
interest `7,100` (including RNCI `824`); and other adjustments `19,000` for the
mandatory convertible preferred liquidation preference. Its `18,023` carrying/
APIC amount is documented but is not substituted. Pension liabilities are an
explicit `policy_excluded` zero adjustment, not a reported zero balance.

The exact reverse target closes as:

`4,223,313.06 + 100,164 + 7,100 + 19,000 - 55,911 - 318,024 = 3,975,642.06 USDm`.

## 4. Strategy assumptions are not source facts

`strategy_assumptions.json` is versioned `alphabet.machine-candidate.v1`. Every
numeric path and scenario override is explicitly marked `assumption`, has a
versioned key, rationale, and equation, and carries no source-fact reference.
The five-year paths begin in FY2026. The base, bull, and bear mechanisms are
`search_cloud_resilience`, `ai_monetization_and_utilization`, and
`search_disruption_and_capital_drag`. These values are machine candidates for
user judgment; they are not Alphabet guidance or official forecasts and must
not be published as such.

## Safe fixture scope

The regulatory filing and company material cover all six business modules. The
separate authenticated market bundle supplies only frozen market and capital
inputs. The five-year model remains a separately typed assumption set. No
regulatory-decision fact is supplied; that remains an explicit `ResearchGap`
unless an original regulator/court source is added.
