# FundClaw 研究工作台

这是一个 React + TypeScript 的 FundClaw 研究工作台，连接同源 Gateway API，展示已授权的私有研究快照与安全执行事件。

## 启动

```bash
npm ci
# 服务端环境中的 GATEWAY_PROXY_TOKEN 必须对应后端签发的稳定研究主体。
GATEWAY_API_BASE=http://127.0.0.1:8018 npm run dev
```

## 验证

```bash
npm test
npm run build
```

默认页为新研究入口，研究对话使用 `/research/:conversationId`。浏览器通过同源 `/api/v1` 请求 Gateway；开发环境由 Vite 代理该路径。

页面打开、刷新时自动确认研究身份，不需要填写令牌。开发服务器从服务器环境或未提交的 `frontend/.env.local` 读取 `GATEWAY_PROXY_TOKEN`，同源代理为请求附上该身份；浏览器不持有 Bearer token。**不要使用 `VITE_` 前缀保存任何凭据。** 缺少配置时页面显示服务/身份提示及重试按钮。

本地自动身份仅用于单人、回环地址工作台：开发服务器和 Gateway 都只能监听本机。代理拒绝跨站请求、不可信 Host、非 Gateway 接口及浏览器自选身份。本机 OS 用户和可访问该本地服务的进程属于同一信任边界，不适合作为共享电脑上的多用户身份验证。生产构建及 `npm run preview` 不提供此代理；多人部署需要正常登录/SSO 和受控同源认证代理，不能把本地开发服务器暴露到公网或局域网。详见 [本地启动说明](../docs/fundclaw-gateway-local.md)。

Gateway 执行阶段不代表产业、财务、策略或质控角色任务；研究内容中部的四张专业角色卡仅保留既有展示框架，并明确标记为未绑定。
