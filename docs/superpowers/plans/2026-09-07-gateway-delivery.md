# Gateway Delivery Implementation Plan

**Goal:** 修复9项已归因回归并交付当前Gateway的可运行本机部署入口。

**Architecture:** 当前Gateway与legacy应用使用不同Compose项目和数据库。Node静态服务器仅代理精确Gateway路径，身份保留在服务端，SSE不缓冲。此交付为受信任本机的单主体工作台，不是公网SSO。

**Tech Stack:** Python/pytest、Node24原生HTTP与node:test、Docker Compose/PostgreSQL16、现有React/Vite构建。

## 支持矩阵

| 入口 | 支持 | 边界 |
| --- | --- | --- |
| Vite dev | 现有回环开发代理 | 不扩展到公网 |
| Gateway容器 | 静态产物、单主体同源身份、HTTP/SSE代理 | Compose只发布127.0.0.1端口；新的隔离数据库；仅Gateway API |
| legacy one-click | 原服务资产保留，旧UI验收迁移 | 不将app.main当Gateway；不复活已退休页面 |
| 共享/公网 | 不在本次范围 | 需独立登录/SSO及逐用户身份代理 |

## 文件与顺序

- [x] 重现两项initializer cutoff失败及深JSON文案失败；按既有治理截止设计恢复`boundary.cutoff_at`；CLI测试断言有限安全拒绝而非解释器递归阈值。
- [x] 新增`frontend/server/gatewayServer.mjs`和`gatewayServer.test.mjs`：先写真实HTTP测试，覆盖身份覆盖、精确白名单、Origin/Host/cross-site拒绝、失败脱敏、静态回退和长SSE立即交付。
- [x] 新增`frontend/Dockerfile`与`.dockerignore`：明确复制构建输入，不带环境文件，运行时non-root；新增`docker-compose.gateway.yml`，Gateway入口、独立DB、迁移/worker/来源配置一致，凭据仅runtime。
- [x] 迁移6项资产测试到当前可执行server test、容器输入边界、现有UI、OpenAPI显式临时输出，保留后端schema约束。
- [x] 跑隔离pytest相关文件、node:test、Compose配置检查；有环境能力时仅build镜像，不起真实研究或公开部署；准确记录未验证边界。

## 验证

所有Python测试使用`env -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test`。HTTP测试只使用随机回环端口与合成上游，不连接既有服务或模型。根任务负责统一前端依赖与构建版本，本子任务不改package/src。


## 实施结果与限制

完成上述文件；额外增加`backend/.dockerignore`、`.env.gateway.example`和`scripts/gateway-runtime.sh`。脚本只支持validate/build/up/status/down，独立私有环境文件，up明确两份professional-worker，down不删除卷。专业worker实际模块由根任务交付，本子任务没有写stub或启动它。

1. cutoff/deep JSON原三个失败已先重现。initializer仅将preview的cutoff恢复为解析得到的治理截止，未放宽幂等冲突检查。深JSON程序原本正确返回2；只把测试移至解析器或schema有限安全拒绝契约，检查stdout为空并精确限定两种错误，避免依赖Python递归阈值。
2. 6项退休资产测试已迁移：容器身份/构建环境排除、当前构建和容器入口、真实HTTP/SSE代理、显式OpenAPI输出不创建或更改默认产物、当前Gateway合同与评估UI无投资字段。后端原schema约束保留。历史live浏览器opt-in测试仍是原有skip，没有新增skip来掩盖6项失败。
3. 最终相关回归：**155 passed, 1 skipped, 2 warnings，22.90秒**。命令（backend目录；Node24加入PATH）：

```sh
env -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test .venv/bin/python -m pytest -q tests/test_one_click_runtime_assets.py tests/test_one_click_stability.py tests/test_verify_live_event_ui.py tests/test_verify_live_company_research_ui.py tests/underwriting/test_openapi_dump.py tests/underwriting/test_company_research_initializer.py
```

4. **Node24真实HTTP 10/10通过**：静态SPA/health，服务端覆盖身份与幂等key，Host/Origin/cross-site拒绝，旧/编码/非法方法路径拒绝，缺配置关闭，SSE未结束即交付且保留恢复游标/断开上游，重定向/传输故障安全失败，危险配置，team精确GET/POST集合，超长请求提前拒绝。测试只使用临时回环上游。
5. 实际`docker build -t fundclaw-gateway-delivery-audit:local ./frontend`通过；镜像构建内Node24执行npm ci、tsc、Vite6.4.3，npm报告audit0。此结果仅是构建当时依赖和前端文件状态，后续主任务UI变化需重新构建。
6. 在该runtime镜像执行相同**10/10 HTTP测试**通过，`--network none`且仅只读挂载测试文件，容器结束自动删除。额外镜像运行检查non-root、`/app`仅包含dist/server、index存在且未内嵌GATEWAY_PROXY_TOKEN环境，通过。没有给镜像构建或这些测试传入真实凭据。
7. 使用所有必填项的合成占位值，`docker compose -f docker-compose.gateway.yml config --quiet`通过；`bash -n scripts/gateway-runtime.sh`、help及`git diff --check`通过。没有启动Compose stack、现有服务或数据库，没有公开部署、模型调用或volume操作。

两条warning为既有AnyIO别名弃用和SourceLocatorV1 schema字段遮蔽。155项不是全后端用例数量，也不与先前154项重复相加。Node容器构建/代理测试不证明整栈API+worker已启动、专业任务已执行或新迁移已运行；这些由根任务在集成后另验。当前固定主体本机模式不等同多用户登录；不要部署到公网或共享机器，也不要从旧one-click载入业务卷。
