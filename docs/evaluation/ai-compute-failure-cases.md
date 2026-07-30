# AI 算力链 评估保留失败 / 歧义案例（Failure Cases）

本文件保留"AI 算力链"切片中已知失败或歧义的评估场景，用于回归与防退化。每条案例说明
错误形态、在切片中的体现、以及账本层如何防范。这些案例不作为通过标准，而是作为
"必须被正确识别为失败/歧义"的负样本。

## 1. 未来发布泄漏（future publication leakage）

- **错误形态**：将 `available_at` 晚于 cutoff 的证据纳入 EvidenceSnapshot，导致 AIAssessment
  使用了截止时刻尚未可见的材料。
- **切片体现**：所有材料在冻结时刻 `available_at = now`，快照 cutoff 设为 `2026-12-31`，
  全部可见；若误将某研报 `available_at` 录为晚于 cutoff，则会泄漏。
- **防范**：`ResearchRepository.visible_links` 以 `EvidenceLink.available_at <= cutoff` 过滤；
  `AssessmentService.freeze_snapshot` 仅纳入可见链接。该语义由
  `tests/test_time_travel.py` 验证（future_link 被排除）。
- **回归断言**：评估时若某 snapshot.evidence_link_ids 含 available_at > cutoff 的链接，判定失败。

## 2. 整体公司指标误用于业务线命题（total-company metric for business-line thesis）

- **错误形态**：用公司整体收入/利润去支持某个业务线（分部）命题，忽略口径不匹配。
- **切片体现**：T2 命题针对"AI服务器分部传导"，而 `s_fii_ai_server_rev` 来自工业富联
  云计算板块整体口径，AI服务器分部数据未单独披露（`s_fii_segment_gap`）。
- **防范**：该 EvidenceLink 的 `scope` 显式标注
  `{"level":"company","segment":"AI服务器","note":"公司整体口径，非AI服务器分部单独披露"}`，
  T2 结论为 `insufficient_evidence` 并将"分部数据未单独披露"写入 `gaps`，不升级为 supported。
- **回归断言**：评估时若业务线命题被公司整体口径证据判为 supported 且未在 gaps 标注口径不匹配，
  判定失败。

## 3. 矛盾研报观点（contradictory research opinion）

- **错误形态**：两份研报对同一命题给出相反结论，系统仅采纳一方或简单平均，掩盖分歧。
- **切片体现**：
  - T2：中信(`s_citic_transmission_forecast`)认为代工传导兑现，国信(`s_guosen_transmission_caution`)
    认为传导弹性需审慎验证 —— 以 `contradicts` 显式记录。
  - T3：中信(`s_citic_cambricon_valuation`)维持买入(2026E PE≈85)，国信(`s_guosen_cambricon_valuation`)
    认为估值透支(2026E PE>300) —— 以 `contradicts` 显式记录，T3 结论 `contradicted`。
- **防范**：`EvidenceLink.role` 必须区分 `supports/contradicts/contextualizes`；AIAssessment 不得
  用置信度数值掩盖矛盾，必须给出 `supported/contradicted/insufficient_evidence` 之一。
- **回归断言**：评估时若存在相反研报观点却未以 `contradicts` 记录，或结论未反映矛盾，判定失败。

## 4. 缺失直接传导证据（missing direct transmission evidence）

- **错误形态**：仅有间接/定性证据却判定命题 supported，缺少因果传导的直接披露。
- **切片体现**：T2 缺少"云厂商CapEx -> 工业富联代工端订单"的直接传导披露，仅有公司整体收入、
  管理层定性表态与研报预测。
- **防范**：T2 结论 `insufficient_evidence`，`gaps` 写入
  "缺少云厂商CapEx向工业富联代工端订单传导的直接披露证据"。
- **回归断言**：评估时若命题在仅含间接证据时被判为 supported，判定失败。

## 5. 过期基金披露（stale fund disclosure）

- **错误形态**：将过期（早期报告期）的基金持仓当作当前主题暴露，忽略披露时效。
- **切片体现**：`fund_b`(国泰CES半导体) 对工业富联存在两条 HoldingDisclosure：
  - `report_period=2025-06-30, published_at=2025-07-24`（过期，source=fund-report-2025H1）
  - `report_period=2026-03-31, published_at=2026-04-22`（较新，source=fund-report-2026Q1）
- **防范**：`ExposureService.for_fund` 按 `published_at <= as_of` 过滤可见披露，并按
  `report_period` 取每只股票最新一条（`choose_latest_disclosure_per_stock`），过期披露被较新披露覆盖。
  该语义由 `tests/test_exposure.py` 验证（latest report_period wins）。
- **回归断言**：评估时若 as_of 时点暴露计算使用了被更晚报告期披露覆盖的过期披露，判定失败。

## 6. 未盈公司估值口径风险（附加歧义）

- **错误形态**：对尚未盈利公司直接套用 PE，或忽略 PE 口径差异得出错误结论。
- **切片体现**：寒武纪 2025 年仍亏损(`s_cambricon...` 年报)，但研报以 2026E PE 估值；
  中信与国信对 2026E PE 给出 85 倍与 >300 倍的巨大差异。
- **防范**：ValuationSnapshot 以 `metric_name/definition` 显式记录口径（PE_TTM 定义为
  总市值/近四月归母净利润）；ThemeRole 与 HoldingDisclosure 独立于估值口径，不因 PE 高低变更。
- **回归断言**：评估时若估值口径未在 `definition` 标注或对亏损公司使用 TTM 盈利 PE 未提示，判定失败。
