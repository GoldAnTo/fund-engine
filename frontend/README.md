# 前端基础工程

旧公司研究、事件研究、市场映射、研究档案、演示页面和静态原型已删除。当前入口仅显示“前端页面已清理，新原型待设计。”，没有研究按钮、旧路由组件或自动 API 调用。新原型尚未设计。

## 保留内容

- Vite、TypeScript、React 依赖和构建配置，供后续重建使用。
- `src/data/`、`src/contracts/`、`openapi.json` 和 `src/eventMarket/researchOsApi.ts`：真实 API 客户端与契约。
- 不依赖页面的进度守卫、数据格式化和提交幂等逻辑及其测试。
- Docker / Nginx 同源 API 代理与上传限制。

## 运行与验证

在仓库根目录运行 `nvm use`，然后进入 `frontend`：

```sh
npm ci
npm run dev
npm run build
npm test
npx playwright install chromium
npm run e2e
```

浏览器冒烟检查桌面与移动尺寸下所有历史入口仅展示清理状态，且不会发起研究 API 请求；不再验收已删除页面的业务交互。真实后端 API 仍可独立验证：

```sh
backend/.venv/bin/python backend/scripts/verify_live_event_api.py
backend/.venv/bin/python backend/scripts/verify_upload_proxy.py
```

这两个命令从仓库根目录运行，上传代理检查需要 Docker。

本机代理配置继续支持 `.env.local.example`。`RESEARCH_BEARER_TOKEN` 仅在服务端代理读取，不使用 `VITE_*` 暴露到浏览器。已保存的旧实现见仓库历史与重设计前快照。
