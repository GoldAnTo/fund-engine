# FundClaw 持续公司研究实施计划

> 使用 systematic-debugging、test-driven-development 与 subagent-driven-development 推进；已在专用 worktree 中工作，保留未提交改动，不自动提交或合并。

**目标：** 修复持续刷新，将长期公司档案、事件与资料研究、监控、研究版本接入现有真实四角色工作台。

**架构：** 私有 CompanyStudy 应用边界管理活动与已采用版本；活动 worker 复用 ResearchGateway 创建真实研究；监控产生幂等增量活动。旧研究、证据冻结和专业角色服务保持权威。

**技术：** FastAPI / SQLAlchemy / Alembic / PostgreSQL，React / TypeScript / Vite，现有 Docker 同源代理。

## 1. 刷新根因与回归

- [x] 在 `useGatewayConversation.ts` 与 `useGatewayTeam.ts` 的测试中重现普通进度使已显示数据短暂变空、新增证据导致重挂载的问题。
- [x] 区分静默后台更新与授权收缩，保留授权撤销清除；普通3秒轮询不反复显示“刷新中”。
- [x] 运行相关前端测试，浏览器验证标签与输入状态持续保留。

## 2. 持久化与真实执行

- [x] 新增 `models/company_study.py`、`schemas/v1/company_study.py`、`services/company_study.py`、`api/v1/company_study.py`，以规格的精确契约实现档案、活动、关联、版本与监控。
- [x] 新增 0076 迁移，注册模型与不可变记录；写入幂等、乐观版本冲突及私有归属验证。
- [x] 新增公司研究 worker 与 CLI，通过持久租约、稳定请求键调用已有 Gateway；单次失败与恢复不会制造重复研究。
- [x] 监控按上海时区每日/每周20:00派发，在同一窗口只创建一次活动；暂停停止新派发。
- [x] 测试持久化、跨主体拒绝、幂等重放、不可变版本、worker 失败恢复与调度。

## 3. 工作台接入

- [x] 新增严格公司研究 DTO/传输层及 `CompanyStudyWorkspace.tsx`，新首页以公司研究为主，独立研究保留入口。
- [x] 公司列表、创建、研究活动表单、事件/资料分类、已有会话关联、监控编辑与版本采用接入真实 API，保持私有错误清除与提交栅栏。
- [x] 复用已确认三栏排版；从公司档案进入真实研究时保留公司上下文与返回入口，不重新造四角色组件。
- [x] 扩充 Vite 与生产同源代理精确允许的新路由；测试不放开 legacy API。

## 4. 验收与交付

- [x] 独立规格与质量复核，修正发现的问题。
- [x] 前端全测、类型检查、构建；后端目标测试与迁移，diff whitespace 检查。
- [x] 更新 Docker 公司 worker、数据库迁移与正式前端；验证所有服务健康。
- [x] 浏览器实测刷新修复及档案到研究路径，记录真实调用与未闭环项，更新验收报告。

## 实际验收

见 [完整验收记录](../../evaluation/2026-09-09-continuous-company-research.md)。前端330项通过，独立PostgreSQL84项通过；正式8服务健康，0076迁移完成。浏览器验证公司创建、关联原研究、返回四角色、窄屏及真实断网恢复。真实监控未启用，调度与暂停通过隔离测试；海外来源、文件摄取和32节点历史上限明确保留为后续项。
