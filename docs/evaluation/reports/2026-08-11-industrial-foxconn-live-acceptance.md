# 工业富联真实单案例验收记录

- 验收日期：2026-08-11
- 隔离账本：`.local/industrial-foxconn-real-acceptance.db`（迁移至 `0050`）
- Case：`43d6eb7c-9ef0-4b6c-a76f-7cce0cbaa1cc`
- 租户：`industrial-foxconn-live-acceptance`
- 来源方式：本机 `GILDATA_TOKEN` 调用 Gildata；未使用 mock、SQLite fixture 或主库既有 Case。

## 可复现输入

所有 Gildata 材料均以 `local-demo-gildata-v1` 准入合同冻结，允许本地研究与展示；`available_at` 保留来源发布日期，`acquired_at` 为本次重建时间。

| 材料 | 可得时间 | SHA-256 |
| --- | --- | --- |
| 海通《工业富联(601138)：盈利整体平稳增长 AI业务表现强劲》 | 2024-03-25 | `c3cd8aa6f0d47421560b4d3fc8e393c9cb36fa9491f376d4504bc60e65492c30` |
| 工业富联 2024 年年度报告 | 2025-04-30 | `c2428f9ea7f8b306b6ce2f9d1b5e0475ad77136bc772b24abb360e9520666d1e` |
| 515050 2024Q4 股票持仓明细 | 2025-03-31 | `baa430c4ed1dde76d528644785e0ae80fc6f785e5e84ebf7a6d056c8e4fe7355` |
| 华夏中证 5G 通信主题 ETF 2024 年年度报告 | 2025-03-31 | `f5b4d274c910b4e0004a99d34dedc662128d3bbb9b86827f4d36c9fbc5b84b1f` |
| 工业富联与中证全指日度行情 | 2025-04-30 07:00 UTC | `aa3be365316c3bdab595b594fc48feb0365b36407364d3efbfe4f03e0b850ede` |

## 人工发布的历史预测裁决

- 冻结预测：工业富联 2024 年归母净利润 `25,149,000,000 CNY`
- 后续实际：`23,216,000,000 CNY`，来源可得于 2025-04-30
- 规则：`forecast-numeric-v1`，相对容差 10%
- 结果：偏差约 7.69%，机器候选与人工确认均为 `supported`（得到支持）
- 结论边界：只说明该预测在预先冻结的数值规则下得到支持；不解释或推断股票价格因果，不构成投资建议。

## 市场与基金边界

- 市场窗口仅记录 2025-04-29 收盘至 2025-04-30 收盘，较中证全指的相对表现为 -0.55%；页面明确标注为观测，不建立年报与价格表现的因果关系。
- 515050 对 601138.SH 的权重为 5.33%，报告期为 2024-12-31、披露日为 2025-03-31；覆盖状态为 `partial`，因此只展示历史披露，不表示实时仓位或精确基金暴露。

## 默认 HTTP 页面回放

使用 Vite 默认 `/api/v1` 代理连接隔离 FastAPI 服务，并以租户 `industrial-foxconn-live-acceptance` 打开 Case。页面已展示：

1. 已发布的范围受限研究结论和 3 条已审核证据；
2. `25,149,000,000` 对 `23,216,000,000` 的人工发布预测裁决；
3. Gildata 日度行情的来源、可得时间、-0.55% 相对表现与非因果提示；
4. 515050 的历史、部分覆盖且已过期的披露标签。

截图仅作本地验收辅助，位于未纳入版本控制的 `.local/industrial-foxconn-live-ui.png` 与 `.local/industrial-foxconn-live-market-ui.png`。
