# 只读研报 Wiki 嵌入

嵌入页面仅使用 `VITE_EMBED_RESEARCH_API_URL`，且必须是与嵌入静态页面不同源的绝对 HTTPS/HTTP API 地址。例如：

```html
<iframe
  src="https://embed.example/reports/CASE/wiki#token=READ_ONLY_TOKEN"
  title="只读研报关系图谱"
  referrerpolicy="no-referrer"
></iframe>
```

令牌只放在 URL fragment（`#token=…`），前端读取后立即清除 fragment，并通过 `X-Embed-Token` 请求嵌入 API。不要把令牌放入 query string、日志或普通内部 API 客户端。

`grant.allowed_origins` 必须填写 **embed 前端页面自身的 origin**（例如 `https://embed.example`），而不是承载 iframe 的父站 origin。浏览器跨源请求会携带这个 origin，后端据此执行 embed grant CORS 校验。

部署静态 embed host 时，还必须在部署层为 embed 页面设置 CSP `frame-ancestors`，明确限制可嵌入它的外部父站，例如：

```http
Content-Security-Policy: frame-ancestors https://portal.example https://research.example
```

该页面是只读投影：不提供写操作、普通研究 API 回退或内部对象标识。若 `VITE_EMBED_RESEARCH_API_URL` 缺失、相对或与 embed 页面同源，页面显示“嵌入API未配置为独立来源”并且不会发出请求。
