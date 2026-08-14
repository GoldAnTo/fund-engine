# Acquisition sources 运维手册

本手册用于人工验证 governed acquisition 的 Gildata、上交所和深交所
adapter 契约。smoke 命令只做显式的 `search` 与有限 `fetch`，不调用
AutoResearchService，不运行旧 research worker，也不把 fixture 或 mock 当作
live 结果。

## 来源、凭证与边界

| source | 凭证 | 官方入口 | adapter 边界 |
| --- | --- | --- | --- |
| `gildata` | 必须设置 `GILDATA_TOKEN`；不得写入命令行、报告或日志 | 许可数据服务，无公开替代入口 | 仅 `GildataResearchSource`；缺凭证直接失败 |
| `sse` | 无 | [上海证券交易所最新公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/) | 仅 adapter descriptor 中的 SSE 精确 host |
| `szse` | 无 | [深圳证券交易所上市公司公告](https://www.szse.cn/disclosure/listed/notice/index.html) | 仅 adapter descriptor 中的 SZSE 精确 host |

两个交易所页面均在 2026-08-13 从官方域名验证。命令使用仓库既有 HTTP
配置：禁用环境代理和自动重定向，设置显式连接、读取、写入与连接池超时，
只允许 policy 中的 HTTPS 精确 host，并限制响应大小和重定向次数。

当前启用的来源策略为 `b-scope-v2`。SSE 搜索结果中的主站 canonical URL
保持不变并始终先尝试；只有该请求返回结构化的 `response_type` 或
`content_encoding` 失败时，SSE adapter 才会尝试官方繁体中文镜像
`big5.sse.com.cn`。若主站响应因不支持的内容编码被拒绝，其编码 bytes 绝不被
读取、解压或冻结。镜像 URL 只能由已验证的
主站 URL 机械转换为
`https://big5.sse.com.cn/site/cht/www.sse.com.cn/<相同 path>`，不得采用来源返回
的替代 URL。每次主站或镜像响应都必须在处理响应前先约束并验证最终 URL；网络、
状态码、重定向、长度、大小或空响应失败均不得启用镜像。报告保留 canonical URL
的 SHA-256，并记录实际 `final_url` 的 SHA-256，不记录 URL 明文。无论主站还是
镜像，PDF MIME、`%PDF-` header、响应字节与大小限制、重定向边界以及
canonical/final URL 的精确身份检查均保持严格，不因 fallback 放宽。此契约的
策略版本、精确 host、机械 path 映射与验证规则核验日期为 2026-08-14；live
acceptance 状态以对应报告为准，不得把单独镜像探测当作 smoke 成功。

Gildata token 应由运行环境秘密管理器注入。运行前只检查变量是否存在，不要
执行 `echo $GILDATA_TOKEN`，也不要把带 token 的 URL 放进故障单。Gildata
缺凭证时命令非零退出并写 `configuration` 错误；绝不退回 fixture。

## 命令

从 `backend/` 目录运行。`--security-code` 与 `--name` 至少提供一个；SSE/SZSE
当前 adapter 要求 query 中有且只有一个六位证券代码。日期可使用 `--days N`，
或同时使用 `--start YYYY-MM-DD --end YYYY-MM-DD`。`--days 2` 按
Asia/Shanghai 来源日历包含今天和前一天：在 2026-08-13 对应
`2026-08-12` 至 `2026-08-13`。

```bash
.venv/bin/python -m app.scripts.smoke_acquisition_sources \
  --source sse --security-code 600000 \
  --start 2025-08-14 --end 2026-08-14 \
  --output ../docs/evaluation/reports/acquisition-sse.json

.venv/bin/python -m app.scripts.smoke_acquisition_sources \
  --source szse --security-code 000001 \
  --start 2025-08-14 --end 2026-08-14 \
  --output ../docs/evaluation/reports/acquisition-szse.json

.venv/bin/python -m app.scripts.smoke_acquisition_sources \
  --source gildata --security-code 600000 --days 2 \
  --output ../docs/evaluation/reports/acquisition-gildata.json
```

Gildata 命令应直接继承秘密管理器注入的环境变量，不要在命令行中内联 token。

`--dry-run` 只构造 adapter、验证配置和 descriptor，然后关闭 adapter；它不
调用网络，不表示 live 成功，报告固定为 `status=dry_run`、
`live_success=false`。

```bash
.venv/bin/python -m app.scripts.smoke_acquisition_sources \
  --source sse --security-code 600000 --days 2 --dry-run \
  --output /tmp/acquisition-sse-dry-run.json
```

本 phase 没有从该诊断命令安全写入 Case 的持久化入口。
`--persist-case-id` 因此固定 fail closed，报告
`persistence_unsupported`；禁止绕过服务层直接执行 SQL。

## 请求速率与报告

遵守交易所页面公布的使用条款及任何最新限流提示。不要并发运行同一来源的
smoke。adapter 自身限制搜索页数和响应字节数；smoke 最多 fetch 三个按稳定
顺序返回的 reference，fetch 间隔 0.5 秒。若某次 fetch 因 HTTP 429、超时、
网络错误或其他 retryable `SourceUnavailable` 失败，smoke 立即停止后续 fetch，
不再 sleep，并把未尝试的 accepted reference 计入 `not_fetched_count`；非 retryable
的单项失败仍按顺序继续有限 fetch。保留真实失败报告，按官方要求延后重试。
不要用提高并发、扩大 page limit 或改 User-Agent 的方式绕过限制。

输出文件必须位于已存在、无符号链接的目录。允许用同一命令明确覆盖同名普通
文件；写入使用同目录临时文件、`fsync` 和原子 `replace`。符号链接、目录和
危险父路径被拒绝。

报告可记录：

- schema `acquisition-source-smoke/v3`，以及 `execution` 对象中的固定 generator、
  `mode`、`network` 和 `worktree_clean_at_start`；只有模块的 CLI 入口可记录
  `cli_live`/`live` 或 `cli_dry_run`/`none`，所有对公共 `run()` 的进程内调用均记录
  `in_process_injected`/`injected`，无论注入的是 adapter、时钟、Git resolver 还是
  sleeper；Git commit 与 worktree 状态固定从脚本源码所属仓库解析，状态只能是
  boolean，解析失败为 `null`；
- UTC timestamp、Git commit、adapter key/version 和完整 descriptor；
- query SHA-256 与日期窗口，不记录 name 或完整 query；
- 返回/接受/拒绝/fetch 数量、安全 stable id 和 external version；
- canonical/final URL 的 SHA-256，不记录 URL 明文；
- MIME、byte SHA-256、byte size 与通过安全字符检查的 provider request id；
- `stage`、安全 `category` 和 `retryable`。

报告和 stdout 不得包含原始 bytes、文档标题/正文、HTTP headers、token、cookie、
带凭证 URL、exception message 或 stack trace。stdout 只打印计数、安全 stable
id 和报告路径。

## 状态、退出码与处置

| 状态 | 退出 | 含义 |
| --- | --- | --- |
| `dry_run` | 0 | 配置和 descriptor 可构造；没有联网，也不是 live 成功 |
| `succeeded` | 0 | live search 成功且所有选中的 reference fetch 成功；search 返回 0 也属于成功 |
| `partial` | 非 0 | search 有可用结果且至少一个 fetch 成功，但另有 provider/rejection/fetch 错误 |
| `failed` | 非 0 | 配置、验证、search、全部 fetch、close 或安全边界失败 |

安全错误分类及处置：

- `configuration`：缺 Gildata 凭证或 adapter 无法构造；修复运行环境，不要换 mock。
- `invalid_arguments`：证券标识或日期范围无效；修正输入后重跑。
- `persistence_unsupported`：本 phase 明确不支持持久化；不要直接写数据库。
- `provider_unavailable`：超时、限流或来源暂时不可用；根据 `retryable` 延后重试。
- `provider_protocol`：状态码、MIME、重定向、响应大小或 schema 不符合契约；按 schema drift 处理。
- `adapter_validation`：reference、URL 或 adapter 返回值越过 descriptor；停止使用该结果。
- `provider_item_rejected`：provider 行缺字段、身份冲突、超出 cutoff 或时间精度不足；检查安全 reason 和数量。
- `internal`：未分类编程错误；只用 commit、stage、category 和本地受控复现定位，不复制异常正文。

`returned=0` 只在 search 请求和解析均成功时可算 `succeeded`。任何网络、provider
或解析失败均必须非零退出并生成 `failed`/`partial` 报告；不得替换成 fixture、
mock、缓存正文或手工伪造的成功报告。命令总会尝试关闭 adapter。

## Policy change 与 schema drift

来源条款、官方域名、API 路径、请求参数、限流规则或许可条件变化时：

1. 立即暂停受影响来源的定时/批量 acquisition；保留最后一份安全失败报告。
2. 核对官方公告页和 provider 合同，不从搜索引擎或第三方镜像扩展信任边界。
3. 在 source policy、descriptor、HTTP transport 和 adapter 测试中先写失败测试；
   安全审查通过后才能调整精确 host、协议或 limit。
4. 重新运行 adapter 单测、完整 acquisition slice、相邻回归和真实 smoke。
5. 记录变更日期、依据、adapter version、报告 hash 和批准者；旧报告保持不可变。

出现字段缺失、类型变化、分页异常、日期格式变化、PDF MIME 不符、未知重定向或
stable identity 冲突时，按 schema drift 处理。不要宽松解析、猜测字段、放宽
host、吞掉 rejection，或从测试 fixture 补齐 live 响应。先保存安全分类和计数，
用最小、脱敏的字段级样本更新 contract test，再修改 adapter。
