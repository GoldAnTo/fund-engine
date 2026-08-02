# 锂电储能链 评估金标集（Gold Set）

第二个冻结垂直切片，与 `ai-compute-gold-set.md` 并列，用于"锂电储能链" ResearchCase
的可审计评估。文本样例沿用手工 marker 约定；**本切片首次包含真实二进制 PDF 原件**，
经 `app/services/pdf_text`（pypdf）解析入帐，覆盖真实格式文档的解析路径。所有材料为
模拟数据，不构成任何投资依据。可由 `backend/app/scripts/seed_storage_chain_case.py`
离线重放。

## 冻结源材料

`.txt` 文件经 `[PAGE n][PARA m]` 标记解析；`.pdf` 文件经 pypdf 文本层抽取
（段落按空行切分，表格块保留行结构），DocumentVersion 以 `parser_version=pypdf-v1`
标记，与 docling 世代的文本夹具可区分。

| 文件 | 类型 | 发布日 (published_at) | content_sha256 |
|---|---|---|---|
| `01_eve_capacity_announcement.txt` | 公告（产能扩建） | 2026-02-10 | `073853cdf37db707b1de6e3cfbcd7dcffe1575d04484c9fdc46dd494ee26b915` |
| `02_catl_annual_report.txt` | 财报（年报） | 2026-03-20 | `8eedcd070fb337551122c4080266ba5714b50cd3b6615fc9e61cc8f752dc0f7d` |
| `03_sungrow_quarterly_report.txt` | 财报（季报） | 2026-04-28 | `b5b7994bc1790fc975487e174ddf12f7c9a399423927e232042016b36d19fab8` |
| `04_citic_storage_research.txt` | 研报 | 2026-06-05 | `ed12b1e03786f766b4008ee6ae7e8376d1ddeae0de819567957b77beae22e145` |
| `05_lithium_price_tracker.txt` | 数据简报（碳酸锂价格） | 2026-05-15 | `4d99beeb6a76eac86bc4596e99abad191486cc68650d1118f5dd8e3553a101a4` |
| `06_sungrow_annual_summary.pdf` | 财报摘要（真实 PDF） | 2026-03-25 | `36bd078218edc404cad4a5080f69ab58adb85bf45e984fd5ba48da734d39f3b6` |

- DocumentVersion 数量：6（5 个文本夹具 + 1 个真实 PDF）
- SourceSpan 总数：21（其中 PDF 贡献 2 个）
- SourceStatement 数量：15；EvidenceLink 数量：15
- 涉及公司：宁德时代、亿纬锂能、阳光电源
- PDF 生成器：`backend/tests/fixtures/storage_chain/_make_pdf_fixture.py`（一次性，
  重跑会改变内容哈希并触发门禁 `hash drifted` 失败；重新冻结需同步更新清单）

## 评估 cutoff 日期

- EvidenceSnapshot cutoff：`2026-12-31T23:59:59+00:00`
- 该 cutoff 晚于所有材料的 `available_at`，保证每条 AIAssessment 可沿
  `snapshot → evidence_link → statement → span` 完整回溯（由门禁检查 3 强制）。

## 三条 Thesis

| Key | 命题 | AIAssessment 结论 |
|---|---|---|
| T1 | 2026年全球储能装机高增长将驱动锂电池需求持续扩张 | supported |
| T2 | 碳酸锂价格回升将在2026年修复锂电中游材料环节盈利 | insufficient_evidence |
| T3 | 宁德时代储能业务高增长将支撑其当前估值溢价持续 | contradicted |

每条 AIAssessment 均带一条人工 ReviewDecision（outcome=confirmed），原始 AI 结论
不被覆盖（append-only）。

## 基金披露穿透

- 基金：易方达中证新能源主题ETF联接（011479）、广发储能产业混合（016858）
- 2026Q1 披露（published 2026-04-22）：宁德时代 / 阳光电源 / 亿纬锂能持仓
- 保留一条陈旧披露（2025H1，published 2025-07-24），用于时点排除检查的"新取代旧"语义

## 因果链（T3，人工编写已复核）

全球储能需求爆发 → 头部电池厂储能出货增长 → 宁德时代储能收入兑现 →
国内电芯价格战侵蚀毛利率 → 估值溢价难以仅靠收入高增长维持

## 门禁覆盖

- 通用检查（追溯完整性、披露双时点、时点排除、AI/人工边界、复核覆盖率）自动覆盖本切片
- 清单哈希校验按 `source_url` 前缀归属到 `storage-chain` 案例逐案例强制
- `pdf_fixture_parse_gold` 检查对 PDF 原件重解析，比对金标期望事实（fail-closed）
