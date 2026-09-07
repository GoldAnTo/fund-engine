# 验收记录 · SPEC §12 + §13

> 严格按 [SPEC §12](file:///Users/xiongjiali/code/fund-engine/docs/superpowers/specs/2026-09-03-evidence-first-company-research-workbench-design.md) (验收)
> 与 [§13](file:///Users/xiongjiali/code/fund-engine/docs/superpowers/specs/2026-09-03-evidence-first-company-research-workbench-design.md) (设计原则) 对账。

## §12.1 可视检查 — 全部 PASS

最近一次 `node capture.mjs` 截图结果 (15 张图, 全部进入 `./screenshots/`):

| 页面 | 案例 | 截图 | 主要内容确认 |
| --- | --- | --- | --- |
| 01 研究库 | CATL | [01-library.png](./screenshots/01-library.png) | 搜索栏 + 搜索结果卡 + 最近研究列表 |
| 02 研究概览 | CATL | [02-overview.png](./screenshots/02-overview.png) | 三条关键发现 + 最大缺口 + 9 个模块 |
| 03 商业模式 | CATL | [03-business.png](./screenshots/03-business.png) | 三业务单元 + KPI 横排 + 机制 + 反证 |
| 04 预测与情景 | CATL | [04-forecast.png](./screenshots/04-forecast.png) | 驱动树 + 5 年桥接 + 三情景 + 待确认输入 |
| 05 价值判断 | CATL | [05-valuation-catl.png](./screenshots/05-valuation-catl.png) | as-of + 三情景区间 + 反向 DCF + 敏感性 |
| 06 证据中心 | CATL | [06-evidence.png](./screenshots/06-evidence.png) | 类型化原子陈述 + SourceContract 卡 |
| 07 备忘录 | CATL | [07-versions.png](./screenshots/07-versions.png) | 5 段备忘录 + 22 cite + 版本链 |
| 02 研究概览 | Alphabet | [A-overview.png](./screenshots/A-overview.png) | 阻塞 banner + "当前不可判断" |
| 05 价值判断 | Alphabet | [05-valuation-alphabet.png](./screenshots/05-valuation-alphabet.png) | **不展示**三情景 / DCF / 敏感性 / 回报区间 |

每张报告:`hasWorkbench=true`, `hasWorkface` 在工作面时 `true`, `isUnanswerable=true` 时正确为 `true`。
全部 `page-error` 与 `console-error` 检查无任何记录。

## §12.3 浏览器端到端剧本 — 全部 PASS

运行:

```bash
node e2e-flow.mjs
```

**最近一次结果**: 52 通过 / 0 失败。

### 章节 1: 输入公司 → 启动研究 (3 项)

| 检查 | 结果 |
| --- | --- |
| 研究库 h1 含 "选择公司开始研究" | ✓ |
| 搜索结果带 case=catl 链接 | ✓ (n=4) |
| 进入概览 (URL 同步) | ✓ |

### 章节 2: 概览阶段时间线 + 三条关键发现 + 最大缺口 (6 项)

| 检查 | 实测 |
| --- | --- |
| 概览 h1 含 "海外扩张能让公司 FY27 净利率回到 12% 以上" | ✓ |
| 阶段时间线条目数 | 9 (SPEC §9 9 个模块) |
| 关键发现卡数 | 3 (SPEC §9 三条) |
| 最大缺口阻塞卡存在 | ✓ |
| 回答能力 = 可回答 | ✓ |

### 章节 3: 证据中心 + 精确原文阅读器 (6 项)

| 检查 | 实测 |
| --- | --- |
| 证据中心 h1 "每一条陈述都有谱系" | ✓ |
| 证据行数 (CATL) | 12 条 (含反证与未知并列) |
| SourceContract 卡 | ✓ |
| 阅读器打开 | ✓ |
| 阅读器左侧原文 (`.reader-page`) | ✓ |
| 阅读器右侧谱系段数 (`.chain-step`) | 5 (6 段含 merge 节点) |

### 章节 4: 商业模式 → 预测 → 价值判断 (14 项)

| 检查 | 实测 |
| --- | --- |
| 商业模式 h1 "宁德时代如何赚钱" | ✓ |
| 业务单元卡数 | 3 (动力电池 / 储能 / 材料与回收) |
| KPI 横排 (`.kpi-strip`) | ✓ |
| 反证并排保留 (`.counterevidence-strip`) | ✓ |
| 预测 h1 "从经营驱动到三情景预测" | ✓ |
| 驱动树 (`.drivers-tree`) | ✓ |
| 财务桥接 (`.bridge-table`) | ✓ (含历史 2 年 + 预测 3 年) |
| 三情景 (`.scenario`) | ✓ (n=3) |
| 估值 h1 "当前价格所隐含的条件" | ✓ |
| 市场快照 as-of (`.snapshot-asof`) | ✓ (价格 / FX / 市值 / 要求回报) |
| 三情景企业价值 (`.scenario-rv`) | ✓ |
| 反向 DCF (`.reverse-implied`) | ✓ |
| 敏感性表 (`.sensitivity-table`) | ✓ (WACC 8/9/10% × g 1.5/2/2.5/3%) |

### 章节 5: 关键输入工作面 (5 项)

| 检查 | 实测 |
| --- | --- |
| 工作面 (`.workface`) 存在 | ✓ |
| 关键输入条目数 | 6 (FY27 ASP / 海外占比 / capex / 波兰 / WACC / 户储) |
| 点击"确认"动作触发 | ✓ |
| 5 个动作按钮全在 | ✓ (确认 / 编辑 / 标记 Unknown / 接受缺口 / 补充授权资料) |

### 章节 6: 保存唯一冻结 + 导出 Markdown (11 项)

| 检查 | 实测 |
| --- | --- |
| 备忘录 h1 "完整研究备忘录" | ✓ |
| § 段数 (`.memo-section`) | 5 (§1 边界 / §2 公司如何赚钱 / §3 驱动 / §4 情景 / §5 反证) |
| 段内引用 (`.cite`) | 22 个 |
| 导出 Markdown 按钮 (`.data-action="export-md"`) | ✓ |
| 导出文件落盘 | ✓ 6452 字节 |
| 落盘文件 SHA-256 | `0x07e5f1a092c2...` (最近一次跑) |
| sessionStorage 写入 `lastExport` | ✓ |
| **磁盘 hash 前 12 位 = sessionStorage hash 前 12 位** | ✓ (07e5f1a092c2 ≡ 07e5f1a092c2) |
| 冻结条目 (`sessionStorage["wb.frozen"]`) | 1 (单 v4 冻结) |
| 冻结条目 hash + basis + cutoff_at 全字段 | ✓ |

> 实际导出的最新文件: [e2e/catl-research-07e5f1a092c2-...md](./e2e/catl-research-07e5f1a092c2-01a42912-531b-4409-b60f-7e249485e75d.md)

### 章节 7: 版本回放 (2 项)

| 检查 | 结果 |
| --- | --- |
| 版本链含回放按钮 | ✓ (n=3: v1 / v2 / v3) |
| 回放触发 toast 通知 | ✓ |

### 章节 8: Alphabet 不可回答剧本 (5 项)

| 检查 | 结果 |
| --- | --- |
| `is-unanswerable` 类已切换 | ✓ |
| 阻塞 banner (`.banner--blocked`) | ✓ |
| 结论 h1 "当前不可判断" | ✓ |
| 估值页面 unanswerable-card 存在 | ✓ (含"不展示以下数字"清单) |
| 冻结按钮 disabled | ✓ (按 SPEC §8) |

## §12.4 双案例对照

| 维度 | CATL · 300750.SZ (可回答) | Alphabet · GOOGL (不可回答) |
| --- | --- | --- |
| 研究问题 | 海外扩张能否将 FY27 净利率拉回 12% 以上 | AI 推理工作负载下资本开支节奏是否可持续 |
| basis | basis-catl-2026q2 | basis-alph-2026q2 |
| 截止 | 2026-06-30 16:00 CST | 2026-06-30 16:00 ET |
| 策略版本 / 模型版本 | v2025.4 / v2025.4-r3 | 同上 |
| 资料数 / 已复核 | 41 / 38 | 38 / 11 |
| 已确认事实 | 171 | 41 |
| 开放缺口 | 4 | 17 |
| 阻塞项 | 0 | 4 (AI 推理毛利 / 搜索份额 / 反垄断 / Cloud 增速口径) |
| 价值判断层 | 显示三情景 + 反向 DCF + 敏感性 | **不显示** — "当前不可判断" |
| 冻结按钮 | 启用 | **disabled**, 标题 "阻塞不可保存" |
| 已冻结版本数 | v1 / v2 / v3 / **本次 v4** | v1 (draft, 未冻结) |

### CATL 关键数字 (估值页可验)

- 当前价格 ¥224.6, FX 7.18, 市值 ¥9,880 亿, 要求回报 9.0%
- Base ¥215–248, Bull ¥268–308, Bear ¥152–188
- 反向 DCF 隐含 FCFF 永续增速 2.4% (高于分析师一致 1.9%)
- 敏感性: WACC 8/9/10% × g 1.5/2/2.5/3% → 12 个值

### Alphabet 阻塞时显式不展示 (估值页清单)

按 SPEC §8 显式屏蔽:

- 三情景企业价值区间
- 反向 DCF 隐含条件
- WACC × 永续增速 敏感性表
- 要求回报比较
- 任何目标价 / 买卖指令

但仍展示:

- 已确认的部分事实 (Q1 2026 营收、Cloud 增速的口径冲突、capex 规模)
- "升级可回答"的外部触发条件 (Q3 2026 财报 / SimilarWeb 授权 / 反垄断裁决)

## §12.5 失败 / 阻塞剧本 (Alphabet 场景覆盖)

### 阻塞原因链

```
AI 推理单位毛利 missing (披露缺失)
  ↓ 阻塞财务桥接 (无法填完 5 年模型)
  ↓ 阻塞三情景并列 (无法满足"机制不同")
  ↓ 阻塞估值 / 反向 DCF / 敏感性 (SPEC §8 强制停止)
  ↓ 阻塞冻结 (按钮 disabled)
```

### 并列保留项 (不交给 AI 选择)

- Cloud 增速: 管理层 +30% vs 一致 +24% — 两种口径并列保留在证据中心冲突块
- 用 UI 提示用户到"关键输入确认"工作面中选择采用何种口径

### 升级可回答的外部条件 (按 SPEC §8 触发条件记录)

| 触发条件 | 来源 | 等待日期 |
| --- | --- | --- |
| Q3 2026 财报披露 AI 推理口径 | 公司 10-Q + Earnings call | 2026-10 |
| SimilarWeb / Statcounter 第三方授权 | 数据采购 / 申请 | 待定 |
| 反垄断最终裁决 | 司法部进度 | 待定 |

## §13 设计原则对账 (在原型里执行)

| 原则 | 在哪里执行 | 验证位置 |
| --- | --- | --- |
| 避免 AI slop · 不要渐变文字 | tokens.css 没用 `--gradient-text` 配色 | grep 无 |
| 避免 AI slop · 不要 glassmorphism | app.css 无 `backdrop-filter` | grep 无 |
| 避免 AI slop · 不要 hero-metric 卡 | 估值页用 row 布局 (`.snapshot-asof` 4 等宽) 而非 hero 数字 | 截图 [05-valuation-catl.png](./screenshots/05-valuation-catl.png) |
| 避免 AI slop · 不要卡片栅格 | 业务单元用 `auto-fit minmax(240px, 1fr)`, 数据上下文用等分 | 截图 |
| 证据先于叙事 · 每数字可点击溯源 | 每 KPI / 反证 / 情景都有 `data-open="reader"` 或 `data-workface="inputs"` | e2e 章节 2-5 |
| 不可回答 ≠ 不可展示 | `is-unanswerable` 类仍展示 h1 + 部分事实 + 不展示数字层 | 截图 [05-valuation-alphabet.png](./screenshots/05-valuation-alphabet.png) |
| 反证与未知并列保留 | 驱动树同时容纳 fact/assumption/derived/counter/unknown 五色 | 截图 [04-forecast.png](./screenshots/04-forecast.png) |
| 历史不漂移 | 备忘录每段 `[^cite]` 标签 22 个, 顶端固定截止日 | 截图 [07-versions.png](./screenshots/07-versions.png) |
| 缺失 ≠ 零 | Unknown 类驱动力在该字段显示破折号"—,", 不是 0 | 截图 [04-forecast.png](./screenshots/04-forecast.png) |
| 许可范围内展示原文 | 阅读器顶部 banner 明确"许可允许段落引用与被引单元格" | 截图 [W-reader-catl.png](./screenshots/W-reader-catl.png) (上轮截图) |

## 已演进的修复记录 (回放路径)

| 问题 | 修复 | 文件 |
| --- | --- | --- |
| 内部 `/path?case=...` 链接退出 SPA | 加全局拦截器 + `#/path` 格式 | [app.js](./app.js) |
| `bindInternal` 每次 render 重复注册 click 监听器, 触发 N 次 | AbortController 撤销上一次注册 | [app.js](./app.js) |
| 初次实现用 FNV-1a (BigInt), headless 浏览器无 BigInt | 替换为完整 FIPS 180-4 SHA-256 | [app.js](./app.js) |
| action 监听器放在 `document.addEventListener` 没被触发 | action 移到 `bindInternal` 内并 stopPropagation | [app.js](./app.js) |
| Screenshots 旧命名 (05-valuation.png) 现在同时存在 catl/alphabet 双版本 | 增加 05-valuation-catl.png 与 05-valuation-alphabet.png | screenshots/ |
