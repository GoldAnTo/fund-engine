# 独立投资研究原型

这是一个独立、一次性的前端原型，只使用模拟数据。它用于验证“搜索主体 → 设置研究问题 → 维护研究判断”这三个屏幕的产品结构，不连接真实数据源，也不构成投资建议。

## 本地运行

```bash
cd prototype/investment-research-prototype && python3 -m http.server 8010
```

打开 <http://localhost:8010/?screen=search>。

## 路由

- `?screen=search`：搜索公司、证券或行业。
- `?screen=setup&security=GOOGL`：确认证券并设置研究问题；`security=GOOG` 可查看 Class C。
- `?screen=workbench&security=GOOGL&variant=A`：判断优先工作台。
- `?screen=workbench&security=GOOGL&variant=B`：模型优先工作台。
- `?screen=workbench&security=GOOGL&variant=C`：PM 备忘录工作台。

## 验证

从仓库根目录使用 `.nvmrc` 中的 Node 版本，然后进入原型目录：

```bash
nvm use
cd prototype/investment-research-prototype
node --test contract.test.mjs
node capture.mjs
```

`capture.mjs` 也可直接用当前 `node` 运行；低于 Node 20 时会自动寻找已安装的兼容版本并重新执行。截图输出到 `output/`。

## Visual QA

逐张检查六张截图后，桌面搜索与设置页的主次关系清楚，A/B/C 保持了三种不同的阅读构图；表格密度较高但没有页面级裁切，局部表格可独立横向滚动。首轮移动截图里，重复的研究问题位于公司身份与当前判断之间，推迟了核心推理；B 方案截图还保留了上一页面的滚动位置。最终修正重组了 A 方案的实际标记与辅助技术阅读顺序，把次要问题与价格/证据台账放在完整推理流之后，再用桌面网格恢复宽屏构图；每次截图也会在写入前显式回到页面顶部。复查重点包括层级、裁切、密度、文案、移动顺序，以及避免等宽卡片仪表盘式外观。
