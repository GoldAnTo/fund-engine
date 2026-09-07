# FundClaw 研究工作台

默认连接真实事件研究 API，支持列表、创建与详情。`/?client=mock` 显式打开演示；真实接口失败会显示错误，不会回退为示例研究。

## 本地运行

先启动后端，在仓库根目录执行 `nvm use`，再进入 frontend：

```bash
npm ci
cp .env.local.example .env.local
# 在 .env.local 填写后端地址及宿主发放的 RESEARCH_BEARER_TOKEN
npm run dev
```

令牌只由本机 Vite 代理读取，不使用 `VITE_BEARER_TOKEN`。代理绑定回环地址，代表配置的研究空间；生产部署由 Nginx 或宿主认证网关提供同源 `/api`。不要把开发代理公开为多用户认证服务。

## 验证

```bash
npm test
npm run build
npx playwright install chromium
npm run e2e        # 显式演示，桌面/移动端交互
npm run e2e:live   # 隔离 SQLite + 真实 Uvicorn/Vite HTTP 创建与刷新
npm run test:live-company-research-support # 验收进程隔离、失败和清理
npm run verify:live-company-research       # 公司审核、冻结回放、导出哈希校验
```

真实浏览器验收需要 `backend/.venv` 已安装后端依赖。测试使用临时数据库和固定测试身份，不读取本地 provider 凭据，不启动付费研究。`e2e:live` 覆盖事件创建、审核、取消以及公司和归档导航；`verify:live-company-research` 单独覆盖公司证据审核、判断确认、冻结、历史回放和 Markdown 下载内容哈希。公司流程使用受控 Alphabet 样例，不代表任意公司的外部资料抓取已验收。

按上述顺序运行真实验收，避免同时在同一宿主启动其他验收栈：支持测试会比较进程快照，并检查失败后的清理。CI 在前端、后端或 `.nvmrc` 改动时触发。前端工作流执行页面与事件验收，后端的 `company-research-live` 任务先执行支持测试，再通过 pytest 包装器启动公司完整验收；每一步失败都会阻止任务通过，两个工作流不重复执行公司完整验收。

公司研究入口为 `/research`，研究档案入口为 `/underwriting/research`。二者从当前工作台导航进入，均通过同源 `/api` 代理读取服务端数据。

完整范围与剩余项见 [实施跟踪](../docs/superpowers/plans/2026-09-06-roadmap-execution.md)。

数据库并发验证由后端 CI 的 `postgres` 任务执行：启动临时 PostgreSQL 16，迁移到
Alembic head，再运行 `pytest tests -m pg_only`。这些测试会清理测试库中的账本数据，
仅使用隔离测试数据库；不同用例的独立连接提交由测试夹具统一清理，迁移专属 schema
的触发器检查限定在当前 schema。JUnit 结果作为 CI artifact 保存。

部署代理上传验证：`backend/.venv/bin/python backend/scripts/verify_upload_proxy.py`
（从仓库根目录运行，需要 Docker）。它使用当前 Dockerfile 的 Nginx 镜像和临时容器，
验证正常文件及 multipart 余量可以通过、超过 21 MiB 的完整请求在转发前被拒绝，
包含没有 Content-Length 的分块传输。应用继续单独执行每文件 20 MiB 限制。
