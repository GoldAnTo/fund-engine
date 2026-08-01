# AI 算力链 评估金标集（Gold Set）

冻结的固定垂直切片，用于"AI 算力链" ResearchCase 的可审计评估。所有源材料均为
手工准备的纯文本样例（模拟公告/财报/研报），不联网、不调用 Docling，内容哈希固定，
可由 `backend/app/scripts/seed_ai_compute_case.py` 离线重放。

## 冻结源材料

每个文件经 `DocumentService.freeze` 按内容 SHA256 冻结为不可变 `DocumentVersion`。
重放相同字节返回同一版本。

| 文件 | 类型 | 发布日 (published_at) | content_sha256 |
|---|---|---|---|
| `01_cambricon_private_placement_announcement.txt` | 公告 | 2026-03-15 | `090ff982b25f8d229c1ee0f9d1dac4979d44792d8243176e28965376fbfc9085` |
| `02_fii_annual_report_disclosure.txt` | 年报披露 | 2026-03-28 | `83fb7a2b36dd924ec20260c74d99cab29007d694379bc1e4cdf22ea02f45b3e7` |
| `03_sk_hynix_quarterly_report.txt` | 财报（季报） | 2026-04-30 | `05b380015ea9676cd0c98532b46507fc3e6b894bf0a7f1620ab026cb31887db5` |
| `04_cambricon_annual_report.txt` | 财报（年报） | 2026-04-25 | `fd3a58ceb4f80758d6cecd1c598cce6a3ac85d87c02e03a2c6d8fe7862df71dd` |
| `05_citic_research_report.txt` | 研报 | 2026-06-10 | `0d6896517a2992e80245690055f72ab3c744a67811dfe19652d3c6c87c7261fd` |
| `06_guosen_research_report.txt` | 研报 | 2026-06-18 | `4bf9216183340ea08539da8ef199dab7ea864555a557ef9b19fe391583aff564` |
| `07_cloud_vendor_capex_note.txt` | 公告（云厂商CapEx指引） | 2026-05-20 | `d78649bdad8b8d650f3461ca9aa52b36d0d9b406ff1a388c8499bbf0cf218947` |

- DocumentVersion 数量：7（≥6 要求）
- 可定位 SourceSpan 总数：34（≥30 要求），均由 `[PAGE n][PARA m]` 标记解析
- 涉及公司：寒武纪、工业富联、SK海力士

## 评估 cutoff 日期

- EvidenceSnapshot cutoff：`2026-12-31T23:59:59+00:00`
- 该 cutoff 晚于所有材料的 `available_at`（冻结时刻），故全部 EvidenceLink 在快照内可见，
  保证每条 AIAssessment 可沿 `snapshot → evidence_link → statement → span` 完整回溯。
- 未来发布材料排除语义由 `ResearchRepository.visible_links(available_at <= cutoff)` 保证，
  并由 `tests/test_time_travel.py` 单独验证。

## 三条 Thesis

| Key | 命题 | AIAssessment 结论 |
|---|---|---|
| T1 | 2026年云厂商资本开支高增长将持续驱动AI算力需求扩张 | supported |
| T2 | 云厂商算力采购将沿供应链向代工/ODM端传导，带动工业富联AI服务器收入兑现 | insufficient_evidence |
| T3 | 寒武纪将在2026年兑现算力芯片出货并支撑当前估值 | contradicted |

结论覆盖 `supported / contradicted / insufficient_evidence` 各至少一个。

## 直接 / 间接证据角色

每条 EvidenceLink 的 `role` 为 `supports / contradicts / contextualizes` 之一，
且必带非空 `reason`、`scope`、`available_at`。

### T1（supported）
- supports：`s_cloud_capex_total`（云厂商CapEx指引，直接披露事实）
- supports：`s_cloud_capex_msft`（微软CapEx，直接披露事实）
- supports：`s_hynix_revenue`（SK海力士营收高增，直接披露事实，间接验证需求落地）
- supports：`s_citic_capex_forecast`（研报预测，间接）
- contextualizes：`s_citic_hbm_opinion`（HBM景气与算力需求一致，间接背景）

### T2（insufficient_evidence）
- supports：`s_fii_ai_server_rev`（AI服务器收入高增，间接，公司整体口径）
- supports：`s_fii_mgmt_visibility`（管理层订单能见度表态，间接）
- supports：`s_citic_transmission_forecast`（研报预测传导兑现，间接）
- contradicts：`s_guosen_transmission_caution`（国信认为传导弹性需审慎验证，间接）
- contextualizes：`s_fii_segment_gap`（分部数据缺失，限制证据强度，间接）

### T3（contradicted）
- contextualizes：`s_cambricon_placement_use`（募投方向与兑现一致，间接）
- supports：`s_cambricon_mgmt_delivery`（管理层出货表态，间接）
- supports：`s_cambricon_shipment`（出货同比+60%，直接披露事实）
- supports：`s_citic_cambricon_valuation`（研报维持买入，间接）
- contradicts：`s_guosen_cambricon_valuation`（国信认为估值透支，间接）

> 直接证据 = 来自公告/财报的 `disclosed_fact`；间接证据 = `management_attribution` /
> `forecast` / `research_opinion`，或虽为披露事实但口径为公司整体（非命题业务线）。

## 已知 scope 不匹配（人工标注）

1. **公司整体指标误用于业务线命题**：`s_fii_ai_server_rev` 的 `scope` 标注
   `{"level":"company","segment":"AI服务器","note":"公司整体口径，非AI服务器分部单独披露"}`。
   工业富联披露的是公司/云计算板块整体收入，T2 命题针对"AI服务器分部传导"，分部数据未单独披露，
   该证据仅能间接支持，不能作为直接传导证据。该不匹配记入 T2 的 `gaps`。
2. **整体口径背景证据**：`s_fii_segment_gap` 以 `scope={"level":"company",...}` 显式标注
   分部缺失，作为 contextualizes 而非 supports。
3. **估值口径差异**：T3 中中信按 2026E PE≈85 倍看多，国信按 2026E PE>300 倍看空，
   两者 `scope` 均含 `{"valuation":"PE"}`，口径相同但结论相反，触发 `contradicted`。

## 人工标签

- T1：人工确认（supported），证据链自云厂商CapEx披露至存储营收，无直接矛盾。
- T2：人工维持 insufficient_evidence，缺少云厂商CapEx→代工端订单的直接传导披露，
  且研报观点分歧，需更多分部数据。
- T3：人工维持 contradicted，国信估值透支观点与兑现假设直接冲突，且公司尚未盈利。

以上三条人工标签以 `ReviewDecision`（outcome=confirmed，reviewer=`seed-human-reviewer`）
形式随 seed 落库，每条引用对应 AIAssessment 且不覆盖原 AI 结论；门禁检查
`review_outcomes_tracked` 要求金标切片中每条 AIAssessment 都有人工复核记录。

## 公司 / 标的 / 基金披露

- Company（3）：寒武纪 688256、工业富联 601138、SK海力士 000660.KS
- Stock（3）：688256.SH(SSE)、601138.SH(SSE)、000660.KS(KRX)
- ValuationSnapshot：寒武纪 PE_TTM 380.5 / PB 12.3；工业富联 PE_TTM 25.6 / PB 3.1；
  SK海力士 PE_TTM 9.8（as_of 2026-06-30，source=wind）
- ThemeRole（3，均挂 ResearchCase）：寒武纪=算力芯片受益方、工业富联=AI服务器代工方、
  SK海力士=HBM/存储供应方
- Fund（2）：华夏国证半导体芯片ETF联接(008888)、国泰CES半导体行业混合(012345)
- HoldingDisclosure：均带 `report_period` 与 `published_at`，覆盖寒武纪/工业富联/SK海力士；
  另含一条 2025H1 过期披露（见 failure-cases.md）

## 可重复性

- 离线运行：`cd backend && .venv/bin/python -m app.scripts.seed_ai_compute_case --reset-test-db`
- 验收测试：`cd backend && .venv/bin/pytest tests/test_seed_ai_compute_case.py -q`
- 相同输入字节 → 相同 content_sha256，`DocumentService.freeze` 对同哈希返回既有版本。
