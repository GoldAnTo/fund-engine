# 证据优先的公司研究工作台 · 双案例交互原型

> 严格按 [SPEC §9](file:///Users/xiongjiali/code/fund-engine/docs/superpowers/specs/2026-09-03-evidence-first-company-research-workbench-design.md) 设计。
> 验收报告见 [ACCEPTANCE.md](./ACCEPTANCE.md)。

## 启动

```bash
cd prototype/company-research-workbench
python3 -m http.server 8020
# 浏览器: http://localhost:8020/
```

URL 参数:

- `?case=catl` (默认) 或 `?case=alphabet` 切换案例
- `#/library|#/overview|#/business|#/forecast|#/valuation|#/evidence|#/versions` 切换页面
- `?workface=inputs` 或 `?workface=reader` 直接打开工作面

## 7 个研究页面

| # | 路径 | 主要内容 |
| --- | --- | --- |
| 01 | `#/library` | 选择公司/证券、最近研究、状态过滤 |
| 02 | `#/overview` | 研究问题、阶段时间线、三条关键发现、最大缺口、下一验证事件 |
| 03 | `#/business` | 业务单元 + 单位经济 + 资本占用 + 机制 + KPI + 反证 |
| 04 | `#/forecast` | 驱动树 (fact/assumption/derived/counter/unknown) + 5 年财务桥接 + 三情景 |
| 05 | `#/valuation` | 市场快照 as-of + 三情景企业价值区间 + 反向 DCF + 敏感性 + 阻塞 |
| 06 | `#/evidence` | 每条原子陈述的来源 + locator + 内容哈希 + SourceContract 政策 |
| 07 | `#/versions` | 5 段阅读路径的备忘录 + 版本链 + 导出 Markdown (含 SHA-256 哈希) |

## 2 个工作面

- **精确原文阅读器** (`?workface=reader`): 左侧文档原文 (段落 + 表格命中单元格 + locator 下划线), 右侧 6 段谱系: 来源登记 → 冻结 → 精确 locator → 原子陈述 + 复核状态 → 进入哪些模型输入 → 备忘录引用
- **关键输入确认** (`?workface=inputs`): 仅列出对预测 / 价值判断有实质影响的输入; 每项有 5 个动作 (确认 / 编辑 / 标记 Unknown / 接受缺口 / 补充授权资料)

## 端到端验收 (SPEC §12.3)

```bash
node e2e-flow.mjs   # 52 项检查 / 应全部通过
node capture.mjs    # 输出 15 张截图到 ./screenshots/
```

详细测试记录、双案例对照、§13 验收对账表 → [ACCEPTANCE.md](./ACCEPTANCE.md)

## 文件结构

```
company-research-workbench/
├── README.md                # 本文件: 启动 + 入口
├── ACCEPTANCE.md            # §12 + §13 验收记录
├── index.html
├── tokens.css               # OKLCH 设计 token
├── app.css                  # AppShell + 7 页面 + 2 工作面
├── app.js                   # 状态机 + 渲染 + 工作面 + 导出 / 冻结 / SHA-256
├── fixtures.js              # 双案例 fixture (猫子反序)
├── screens/
│   ├── library.js           # 01
│   ├── overview.js          # 02
│   ├── business.js          # 03
│   ├── forecast.js          # 04
│   ├── valuation.js         # 05
│   ├── evidence.js          # 06
│   ├── versions.js          # 07
│   └── workfaces.js         # §9.1 + §9.2
├── screenshots/             # 15 张视觉验证
├── e2e/                     # 导出的 markdown 验证
├── e2e-flow.mjs             # 端到端剧本
└── capture.mjs              # 截图脚本
```

## 与主仓库 frontend 的设计 token 一致性

- 颜色 token: `--c-canvas / --c-paper / --c-ink / --c-evidence / --c-thesis / --c-supports / --c-contradicts` 直接对齐
  主仓库 [frontend/src/styles/tokens.css](file:///Users/xiongjiali/code/fund-engine/frontend/src/styles/tokens.css)
- 字体: `--font-sans` 与主仓库一致; 备忘录单独引入 `--font-serif`

## 关键设计原则执行 (§13)

- 避免 AI slop: 不含渐变文字、不含玻璃卡片、不含"hero metric"、不含卡片栅格堆叠
- 证据先于叙事: 每个数字后面有"查看依据"入口
- 不可回答 ≠ 不可展示: Alphabet 案例展示"已确认的部分事实"和"为什么不估值", 不展示任何目标价或回报区间
- 反证与未知并列保留: 不被摘要省略
