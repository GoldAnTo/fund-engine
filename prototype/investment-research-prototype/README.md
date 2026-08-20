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
- `?screen=setup&security=300750.SZ`：确认宁德时代与深交所证券的对应关系并建立研究。
- `?screen=setup&security=GOOGL&industry=cloud-infrastructure`：从云计算基础设施行业入口选择代表公司并带入行业语境。
- `?screen=workbench&security=GOOGL&variant=A`：判断优先工作台。
- `?screen=workbench&security=GOOGL&variant=B`：模型优先工作台。
- `?screen=workbench&security=GOOGL&variant=C`：PM 备忘录工作台。

研究问题、期限、个人假设和担忧通过 URL 参数在三个工作台方案间传递。验证记录按钮只展开本次原型会话中的草稿，不连接持久化存储。

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

逐张检查六张截图后，桌面搜索与设置页的主次关系清楚，A/B/C 保持三种不同阅读构图。工作台方案切换器位于正常文档流，不遮挡研究内容；研究对象、问题、期限和证据截止在移动端先于判断与深层推理。因素、指标与模型表格在窄屏转为带字段标签的纵向行，不依赖固定宽度或横向滚动。每次截图写入前显式回到页面顶部，并检查页面级溢出、层级、裁切、密度和文案。
