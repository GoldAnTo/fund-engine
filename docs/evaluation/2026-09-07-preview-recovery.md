# 63297 预览恢复与工作范围校正

用户指出 http://localhost:63297/ 无法打开，并要求核对当前修复对象。

## 已确认事实

- 63297 原服务属于同一项目的独立工作区 `.worktrees/fundclaw-gateway-p0-plan`，展示 FundClaw 多角色研究工作台原型。
- 原页面为该工作区 `.superpowers/brainstorm/60117-1788574241/content/team-workspace-v2.html`，标题 `FundClaw · 多角色研究工作台`。
- 原服务的 `state/server-stopped` 记录 `reason=idle timeout`，时间为北京时间 2026-09-05 11:31:51.680。原 brainstorm 服务在空闲 30 分钟后自动退出。这不是本轮浏览器测试清理服务的证据。
- 此前审计与修复实际发生在 `/Users/xiongjiali/code/fund-engine` 主工作区，最近涉及市场研究入口、主张审核、LLM 用量与数据库索引；并未在上述原型页面上完成同等功能。之前没有充分对齐用户查看的原型与实施目标。

## 本次恢复

- 原地址继续使用 63297，仅绑定 127.0.0.1。
- 在原会话 `state/persistent-preview.py` 增加独立静态服务，根地址直接提供原 `team-workspace-v2.html`，不修改原型页面内容。
- 服务没有空闲退出计时器，脱离当前命令会话运行。重启电脑后仍需重新启动；本次没有安装开机服务。
- 日志和 PID 分别保存在同目录 `persistent-preview.log`、`persistent-preview.pid`。恢复时 PID 为 22090，后续操作前必须重新核实 PID 归属。
- 重启命令：使用项目 Python 执行上述 `persistent-preview.py`；如需后台持续运行，保持与本次相同的独立进程启动方式。

## 验证与后续边界

- 根页面 HTTP 200，响应内容与原文件完全相同，SHA-256 为 `47a880233e89c0d5041c1187c1911024bbd1eeb7d8b511870022cfd279867331`。
- 真实浏览器确认四角色显示、财务筛选、财务角色关联证据查看正常。
- 此地址仍为明确标注的交互原型：使用样例数据，不调用真实 AI，不代表真实后端连接已完成。
- 当前先恢复预览并说明范围偏差。后续实施需要以用户指向的 FundClaw 多角色页面为界面基准，重新映射现有真实 API 与流程；不能把继续恢复旧市场页面等同于完成这个原型。
