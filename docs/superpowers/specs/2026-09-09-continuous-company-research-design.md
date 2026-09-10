# FundClaw 持续公司研究主线

用户于 2026-09-09 明确要求继续实施：优先完整承载持续公司研究，将事件调查、资料采集和监控接入主线，并修复页面反复刷新。沿用已确认的多角色三栏原型。此前一次性研究与旧公司研究规格中排除事件/增量更新的阶段限制，由本次明确请求更新。

## 方案与边界

采用持久化的私有公司研究档案组织已有 Gateway 研究能力：一个公司档案包含研究重点、研究活动、已采用版本及监控配置。已有 underwriting 公司实体、证据和模型服务仍是专业领域底座；本次新增的是用户私有工作组织与持续执行边界，不复制或冒充已认证的公司身份。用户填写的名称、市场和代码明确为档案标识，不自动升级为来源事实。

不采用仅给会话改名的方案，因为它缺少长期身份、版本与监控；不重写已有研究和证据引擎，因为授权、冻结、LLM 预算和四角色能力需要继续复用。

## 用户流程

公司研究列表 → 创建档案（公司名称、市场、可选代码、研究重点） → 建立研究基线 → 在同一档案追加事件调查、资料研究或资料更新 → 四角色研究与证据读取 → 明确采用某个完成的研究版本 → 继续追踪变化。

档案提供“研究概览 / 事件与资料 / 监控与版本”入口。研究活动打开现有三栏工作台；标题与导航保留所属公司的上下文，可返回长期档案。旧的独立研究入口与链接仍可使用，并可由用户关联到公司档案。

监控由应用 worker 持续运行，不依赖页面停留或浏览器计时器。默认不开启；用户配置每日/每周检查及重点后，按 Asia/Shanghai 的日历时点创建有唯一调度键的资料更新活动。无变化或无可用证据仍诚实显示，不自动生成新事实、不覆盖已采用版本。暂停后不再派发新活动，已派发任务保持其实际状态。

## 契约

新接口前缀 `/api/v1/company-studies`，仅挂载到隔离 Gateway，复用 require_gateway_actor 与现有私有授权。所有写入要求 Idempotency-Key；用户身份只能取认证主体，不接受 body 中的 actor/tenant。

`Study`: id, name, symbol (nullable), market (`CN/HK/US/other`), focus, revision (已采用版本号，初始0), created_at, updated_at。

`Activity`: id, study_id, kind (`baseline/event/material/refresh/linked`), title, text, status (`queued/starting/running/completed/failed/blocked`), conversation_id(nullable), run_spec_id(nullable), error_code(nullable), created_at, updated_at。

`Revision`: id, version, activity_id, conversation_id, run_spec_id, team_revision, note, created_at。采用版本必须绑定实际完成的原生研究与四专业角色产出，不表示修改了既有人工质控结论；精确旧团队版本必须保留。

`Monitor`: version, status (`active/paused`), frequency (`daily/weekly`), focus, next_due_at(nullable)。未配置为 null。配置版本追加保存；调度活动不可重复，有限预算沿用底层执行策略。

读：GET 集合返回 `{studies:Study[]}`；GET `/{id}` 返回 `{study, activities, revisions, monitor}`。

写：POST 集合 `{name,symbol,market,focus}` 返回 Study；POST `/{id}/activities` `{kind,text}` 返回 Activity；POST `/{id}/links` `{conversation_id}` 返回 Activity；POST `/{id}/revisions` `{activity_id,expected_revision,note}` 返回 Revision；POST `/{id}/monitor` `{status,frequency,focus}` 返回 Monitor；POST `/{id}/activities/{activity_id}/retry` `{}` 返回 Activity。接口返回 snake_case，前端执行严格 DTO 校验。

## 执行与安全

活动提交持久化排队并快速返回；独立公司研究 worker 认领活动并调用现有 ResearchGateway.send_message，形成真实会话、冻结范围、原生采集与四角色任务。稳定活动键跨恢复保持一致，租约阻止重复 worker 提交；失败保留输入并允许显式重试。已有会话关联必须验证相同主体与租户权限；不可凭可猜测 ID 聚合其他用户材料。

公司档案的名称和别名不绕过证据主体准入。当前海外来源覆盖与 Google/Alphabet 主体别名问题必须继续明确为未闭环，不能用公司档案标签掩盖。

## 页面刷新修复

普通后台读取静默进行；状态变化只更新受影响数据，不清空整页、不重置标签、展开状态、滚动或未提交文本。新增证据不当作授权撤销。权限收缩、身份失效和无法验证的授权变动仍先清除私有内容。手动刷新与错误状态有明确反馈。

## 验收

验证跨主体拒绝、创建与重试幂等、worker 恢复、监控同时间窗只派发一次、暂停与时区、旧版本不可变；验证通过浏览器创建档案、添加事件/资料、关联现有研究、配置与暂停监控、返回四角色工作台。后台轮询期间标签和草稿不丢失。只宣称有证据支持的真实执行结果，模拟 provider 测试单独标记。
