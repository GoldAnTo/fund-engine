# 本地研究栈运维手册

本文用于启动和维护本地、非 Mock 的事件研究主链。统一入口是
`scripts/research-stack.sh`；脚本固定组合根目录下的
`docker-compose.yml` 与 `docker-compose.live.yml`，并使用
`.env.compose` 注入本地配置。

本地栈包含 PostgreSQL、Keycloak、一次性迁移任务、API、Research
Worker、Acquisition Worker、Scheduler 和前端。PostgreSQL 中的持久化记录是
恢复依据；容器内存、浏览器缓存和页面上一次显示的状态都不是恢复依据。

## 前置条件

- Docker Engine 与 Docker Compose v2 可用。
- 在仓库根目录执行本文命令。
- 本机端口没有与 `.env.compose` 中配置的端口冲突。
- 已取得实际要启用的 LLM 和资料提供方凭证。

先确认 Docker 可用：

```bash
docker version
docker compose version
```

## 配置 `.env.compose`

从受版本控制的示例创建本地配置：

```bash
cp .env.compose.example .env.compose
chmod 600 .env.compose
```

`.env.compose` 含本地密码和 provider 凭证，不得提交到 Git，不要粘贴到工单、
聊天记录或日志中。修改前先阅读 `.env.compose.example` 中的注释；变量名和可选值以
该示例为准。至少核对以下配置组：

| 配置组 | 用途 | 核对要点 |
|---|---|---|
| PostgreSQL | 业务账本与 Keycloak 数据库 | 本地密码不得复用于其他环境 |
| 端口 | 前端、API、Keycloak 的宿主机端口 | 认证相关端口固定为前端 `8080`、Keycloak `8081`；API 端口可配置 |
| OIDC/Keycloak | issuer、audience、JWKS、tenant claim、前端 client 与回调 | issuer 的协议、主机、端口和 realm 必须精确匹配 |
| 本地验收用户 | 两个浏览器验收账号 | 仅使用示例明确标记的本地账号和密码 |
| LLM | `LLM_API_KEY`、base URL、model 等 | 必须能访问真实、兼容的 provider |
| Acquisition | `ACQUISITION_ENABLED_ADAPTERS` 及相应 provider 凭证 | 只填写程序支持且确实获授权的 adapter |
| Gildata | `GILDATA_TOKEN` | 启用 Gildata 或需要基金披露调度时必须提供 |

不要把 `APP_ENV` 设置成 `test`，不要启用前端 Mock adapter，也不要把测试 token
作为浏览器 bearer token 注入。这个栈的目的就是运行真实 HTTP、真实 OIDC、真实
worker 和真实持久化主链。

### 本地 OIDC 用户不是生产账号

Compose 导入的 Keycloak realm、client、租户声明、验收用户和密码仅用于本机验收。
它们是公开可预期的开发配置，不具备生产安全性。不得把 realm export、示例密码或
本地 Keycloak 数据卷部署到共享、预发布或生产环境。

生产环境必须使用独立身份提供方、独立 client、受管回调地址和 secret manager；
Case 授权仍由应用内的 CaseGrant 控制，Keycloak 登录成功不等于自动拥有所有 Case。

## 启动

```bash
./scripts/research-stack.sh up
```

`up` 会先校验 `.env.compose` 中的必需值，再用两个 Compose 文件构建或启动服务，
等待迁移和 readiness，并输出可访问 URL 与服务状态。任何必需 provider 配置缺失或
无效时，启动必须失败关闭（fail closed），不会自动切换到 Mock、旧结果或空实现。

首次启动需要拉取镜像、构建应用并导入 Keycloak realm，耗时会明显长于后续启动。
启动完成后再执行：

```bash
./scripts/research-stack.sh status
```

预期结果：

- `migrate` 已成功退出，退出码为 `0`；它不是常驻服务。
- `postgres`、`keycloak-db`、`keycloak`、`api`、`research-worker`、
  `acquisition-worker`、`scheduler` 和 `frontend` 均为运行且健康。
- 各 worker 与 scheduler 有各自的新鲜心跳，不应由其他服务的心跳代替。

需要查看原始 Compose 状态时，可运行：

```bash
docker compose \
  --env-file .env.compose \
  -f docker-compose.yml \
  -f docker-compose.live.yml \
  ps
```

## 访问地址

`up` 最后打印的地址是权威值；API 端口被 `.env.compose` 覆盖时，不要继续使用旧书签。
默认会提供以下入口：

| 入口 | 地址形式 | 用途 |
|---|---|---|
| 前端 | `http://localhost:8080/` | 创建事件、上传资料、确认研究范围并观察流程 |
| API | `http://localhost:8000/` | API；业务调用优先经前端同源 `/api/` |
| API 进程健康 | `http://localhost:8000/health` | 只确认 API HTTP 进程响应，不代表整栈 ready |
| Keycloak | `http://localhost:8081/` | 本地登录和 realm 管理入口 |
| OIDC realm | `http://localhost:8081/realms/fund-engine` | 本地 issuer；必须与浏览器和 API 配置一致 |

整栈 readiness 没有用一个公开 HTTP 地址代替所有依赖检查；使用
`./scripts/research-stack.sh status` 查看数据库、迁移、OIDC、各服务探针与心跳。

不要混用 `localhost` 与 `127.0.0.1`。OIDC issuer、redirect URI、silent callback
和 post-logout redirect 都按完整 origin 匹配；即使它们指向同一台机器，主机名不同
也可能导致登录失败。为了让 realm import 与前端构建保持一致，本地栈不支持通过
`.env.compose` 改写前端 `8080` 或 Keycloak `8081` 端口。

## 日常命令

### 查看状态

```bash
./scripts/research-stack.sh status
```

状态输出用于回答“服务是否能继续处理任务”，不能单独证明某个 Case 已完成。

### 查看日志

```bash
./scripts/research-stack.sh logs
```

保持命令运行以跟随日志，使用 `Ctrl-C` 仅退出日志查看，不会停止服务。排查单个流程
时，优先按 `case_id`、`research_run_id`、`acquisition_job_id`、`request_id` 或
`trace_id` 关联 API、worker 和 scheduler 记录。日志中不应出现 token、cookie、
Authorization header、文档正文或 provider 密钥；发现后应立即停止共享日志并轮换
对应凭证。

### 重启 API

```bash
./scripts/research-stack.sh restart-api
```

API 重启不会重建数据库或重置 Case。重启后等待 readiness 恢复，再刷新原 Case URL。
如果页面暂时无法读取运行详情，应显示“暂不可确认”或恢复中，而不是退回初始状态。

### 重启 Research Worker

```bash
./scripts/research-stack.sh restart-research-worker
```

重启期间心跳会短暂中断。Scheduler 应从 PostgreSQL 中的租约和持久化检查点触发有界
恢复；不要通过修改数据库状态、重复确认范围或重新新建 Case 来“推动”流程。

### 重启 Acquisition Worker

```bash
./scripts/research-stack.sh restart-acquisition-worker
```

正在执行的获取任务可能先显示 stale/recovering，随后由租约恢复继续。恢复应沿用原
query plan 和已冻结资料，并通过来源身份、内容哈希与出版物身份去重；不要因为页面
短暂未更新而重复提交同一获取动作。

### 重启 Scheduler

```bash
./scripts/research-stack.sh restart-scheduler
```

Scheduler 重启只恢复调度进程，不重建 Case、研究运行或监测规则。命令会等待该服务的
真实心跳重新健康后才返回；若超时，会打印该服务的状态和最近日志并以失败退出。

### 一次性执行真实浏览器主链与重启恢复

```bash
./scripts/run-live-acceptance.sh
```

该命令使用唯一 `fund-engine-live-*` Compose project 启动干净的临时栈，运行真实
Keycloak 登录、Case 权限、资料检索/冻结/去重/准入/引用链和四类服务重启验收，最后
收集 Compose 状态、镜像、日志、Playwright trace/video 与脱敏证明。浏览器用例只能
通过受限本机控制端点请求重启四个明确服务，不能执行 shell 或访问数据库。

验收覆盖文件只在 API 与 scheduler 容器中启用
`LIVE_ACCEPTANCE_FAST_SCHEDULER=true`，从而允许专用频率
`acceptance_every_5_minutes`。基础部署和未显式启用该开关的公开 API 会拒绝这个频率，
生产调度器也不会执行它；该设置不能复制到正式环境。

`artifacts/live-acceptance/` 默认权限受 `umask 077` 保护并被 Git 忽略。JSON 证明会
主动排除 token、cookie 与 provider 密钥；但 Compose 原始日志、trace 和 video 仍可能
包含 Case 标题、公开来源 URL、运行 ID 或页面内容，必须按内部诊断材料保存，不能直接
上传到公开工单或提交到仓库。对外共享前应另行审查和脱敏。

无论成功或失败，脚本都会关闭临时栈并只删除该唯一临时 project 的卷，不会触碰日常
开发栈的数据卷。若缺少真实 provider 凭据、OIDC 用户、服务心跳或外部资料访问，验收
会硬失败并保留诊断，不会跳过、改用 Mock 或伪造通过。

### 停止但保留数据

```bash
./scripts/research-stack.sh down
```

该命令停止并移除容器和网络，但保留命名数据卷。下次 `up` 会使用原 PostgreSQL 与
Keycloak 数据，适合日常关停。

### 停止并删除本地数据

```bash
./scripts/research-stack.sh down --volumes
```

这是破坏性操作，会删除此 Compose 项目的 PostgreSQL 和 Keycloak 等命名卷；本地
Case、运行历史、冻结资料、授权和登录数据将无法通过下一次 `up` 恢复。仅在明确需要
全新环境且已确认没有要保留的本地研究数据时使用。不要手工扩大删除范围，也不要对
其他 Compose project 执行同类命令。

## 如何理解健康状态和心跳

以下三层状态必须分开看：

1. **容器状态**：进程是否正在运行，以及容器 healthcheck 是否通过。
2. **运行时心跳**：Research Worker、Acquisition Worker 和 Scheduler 的指定实例
   是否在允许时间窗内报告 `loop` 模式和健康运行状态。
3. **Case 持久化状态**：该 Case 最后成功提交的阶段、检查点、事件和恢复状态。

API `live` 只代表进程活着；`ready` 才代表 API 的关键依赖可用。worker 容器处于
`running` 也不代表能接单，必须同时看相应服务自己的新鲜心跳。`stale` 表示在规定
时间内没有可信续报；`unavailable` 表示实时记录无法读取；两者都不等于任务失败、
停止或完成。

长任务执行期间应持续续写当前时间的心跳。如果容器仍在运行但心跳过期，应按故障
处理，而不是延长对旧页面状态的信任。多个实例必须使用唯一实例 ID，且状态查询必须
按服务区分，不能用 Scheduler 或 Acquisition Worker 的心跳冒充 Research Worker。

### 运行状态接口的边界

- `GET /api/v1/event-research/{case_id}/runtime-status` 面向拥有该 Case 查看权限的用户，
  只返回当前阶段必需服务的脱敏健康状态、直接读取的耐久检查点和恢复语义。
- `GET /api/v1/runtime-status` 仅允许租户管理员访问，用于查看数据库、迁移、各服务
  心跳及脱敏后的配置校验结果；它不会返回实例 ID、主机名、URL 或凭证值。
- API 配置由 API 进程本地校验；worker 与 scheduler 配置由各自进程写入心跳。多个
  新鲜实例的结果会合并，配置不一致时整项降级，不能由一个健康实例掩盖其他实例。

这两个接口都只回答“负责推进的运行服务现在是否可信”。它们不得生成或改写 Case
阶段，也不得用推断出的页面状态冒充耐久检查点。反过来，Case 已保存的阶段也不能
证明 worker 仍在运行。

### 不误用旧 Case 状态

当统一运行记录或心跳无法读取时：

- 页面可以展示最后一个**持久化检查点**，但必须明确标为历史事实。
- 页面必须把实时状态显示为 `stale`、`unavailable` 或 `recovering`，不得把旧的
  “研究中”“已完成”或基金披露状态当成当前运行详情。
- 操作员先查 `status` 和 `logs`，恢复对应服务，再使用页面的“重新读取状态”。
- 不要直接更新数据库，不要清空 Case，不要重复创建运行来掩盖未知状态。

如果恢复后仍无新事件，应沿同一个 `case_id` 和原运行 ID 查日志与租约恢复记录。

## Provider 配置与 fail-closed 规则

运行探针和运行状态接口只做本地配置校验和依赖检查，不会为健康检查实际抓取外部
资料。真正的搜索、下载与 LLM 调用发生在任务执行时，因此“配置健康”只表示变量
存在且格式可解析，不等于 provider 凭证有效、接口当前可访问，也不等于当前没有
限流或网络故障。

以下情况都必须阻止相应服务 ready，或让任务形成可诊断失败，不能回退到 Mock：

- 缺少、空白或格式无效的 LLM 配置；
- `ACQUISITION_ENABLED_ADAPTERS` 含未知、重复或不受支持的 adapter；
- 启用了 Gildata 但缺少 `GILDATA_TOKEN`；
- OIDC issuer、audience、JWKS URL 或 tenant claim 缺失或无效；
- 数据库迁移版本与应用期望 head 不一致。

修复 `.env.compose` 后，仅重启受到影响的服务；若多个镜像需要同一新配置，可再次
执行 `up` 让 Compose 收敛。不要把真实 provider 或生产 credential 写入镜像、
Compose 文件、realm export 或源码；realm 内的固定值只允许用于公开的本地验收身份。

### Realm 文件变更不会自动覆盖已有 Realm

Keycloak 的 `--import-realm` 只在目标 realm 尚不存在时导入。已经保留
`keycloak-data` 卷的情况下，修改 `infra/keycloak/realm-export.json` 后再次执行
`up` 会跳过导入，不会自动更新回调地址、角色、client 或本地密码。

日常 provider、API 或 worker 配置变更不需要重建 Keycloak。只有明确要验证新的本地
realm 定义，并且确认本地登录数据可以丢弃时，才执行 `down --volumes` 后重新 `up`；
该操作同时删除业务 PostgreSQL 数据，因此应先确认没有要保留的 Case。需要保留业务
数据时，应通过 Keycloak 管理面或单独、受控的 realm 迁移处理，不能为了刷新 realm
直接删除全部数据卷。

Realm 中预置的 confidential service clients 是本地身份资源，不会把 secret 注入
不需要调用 API 的数据库 worker。当前 worker 的审计身份由服务端代码固定生成，
不是客户端 payload 或浏览器环境变量；client roles 不能被解释成人工 Case 权限。

## 故障排查

### `up` 在配置校验阶段退出

查看脚本报告的缺失或无效变量名，对照 `.env.compose.example` 修正
`.env.compose`。脚本不应打印变量值。不要临时启用 Mock 绕过校验。

### `migrate` 失败，应用服务没有 ready

运行 `logs`，先定位 `migrate` 的 Alembic 错误。应用服务等待迁移成功是预期行为；
不要手工改 `alembic_version`，也不要让 API 绕过迁移启动。修复原因后重新执行
`up`。

### 前端可打开，但登录循环或回调失败

核对浏览器实际 origin、Keycloak issuer、public client ID 和三个回调地址，尤其检查
端口以及 `localhost`/`127.0.0.1` 是否混用。再检查 Keycloak 与 API readiness。
不要向 session storage 手工注入 token。

### API `live` 正常但 `ready` 失败

这通常表示 PostgreSQL、迁移或 OIDC/JWKS 依赖未就绪。查看 API、Keycloak 和
PostgreSQL 日志，修复依赖后执行 `restart-api`。`live` 成功不能作为继续业务操作的
依据。

### worker 或 scheduler 显示 stale/unavailable

先用 `status` 确认具体服务，再用 `logs` 查对应实例最后一次心跳和任务 ID。确认
provider 配置、数据库连接和迁移 head 后，使用对应 worker 重启命令。Scheduler 没有
专用脚本命令时，应先保留日志并通过既定 Compose 控制面处置，不要直接改租约表。

服务恢复后应出现该服务自己的新鲜心跳；其他服务健康不能替代它。Case 页面仍只能
从新运行记录确认当前阶段。

### 资料获取失败或一直重试

按 `acquisition_job_id` 查看 adapter、阶段、错误分类、重试次数和隔离原因。依次核对
provider 授权、限流、网络、来源白名单与资料格式。搜索摘要不是冻结证据；只有完成
fetch、冻结、去重和自动准入门禁的资料才能进入研究。

### 重启后页面看起来没有继续

不要重新建 Case。确认 API ready、两个 worker 与 Scheduler 心跳新鲜，再沿原 Case
URL 重新读取状态。日志应显示从持久化检查点恢复，并保留原 orchestration、run、
query plan 和已冻结资料身份。若实时记录仍不可读，保持“暂不可确认”，收集 `status`
和相关 ID 的日志后继续排查，绝不能用旧 Case 状态代替答案。

### 端口冲突或打开了错误页面

以当前 `up` 输出为准，检查 `.env.compose` 配置和 `docker compose ... ps` 的端口
映射。浏览器中关闭指向旧 Vite 开发服务器或其他 Compose project 的标签页，再打开
当前前端 URL。
