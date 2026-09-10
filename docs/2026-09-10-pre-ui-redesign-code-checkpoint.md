# 2026-09-10 前端重设计前代码保存清单

> 这是清理前的保存快照。后续已完成[旧前端清理](2026-09-10-complete-frontend-cleanup.md)及[公司研究主线整合](2026-09-10-company-mainline-integration.md)；以下分支和状态用于历史追溯。

本次按用户要求先保存现有代码，尚未开始新前端设计或删除页面。

## 保存范围

- 主目录已有文档和审查脚本已提交为 `bc69b465`。
- 当前公司研究交付分支保留在 `codex/company-research-prototype-delivery`，提交 `109372702913495f9725f9c6bb5d11686763c323`。
- 逐一记录了 35 个工作区。已在公开主线历史中的版本直接引用原提交；其余当前源代码、文档和未提交改动保存到下列独立快照。
- 快照以已公开主线作为父提交，不合并工作流、不移动其他工作区的 HEAD、不改动其索引或工作文件。
- 快照是代码保存点，不表示不同分支已经集成或通过统一验收。

## 新增快照

| 来源 | 保存分支 | 提交 |
|---|---|---|
| backend-live-read | [codex/pre-ui-redesign-20260910/backend-live-read](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/backend-live-read) | `0d317c4596acd6e6723fe9ef8f248de9710e5d64` |
| acquisition-platform-design | [codex/pre-ui-redesign-20260910/acquisition-platform-design](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/acquisition-platform-design) | `d05dcf4c498559859be75aede9c03036093552e6` |
| ai-company-research-mainline-plan | [codex/pre-ui-redesign-20260910/ai-company-research-mainline-plan](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/ai-company-research-mainline-plan) | `abfee22477ce9a68343d040b07903d3d73e61874` |
| event-research-mainline | [codex/pre-ui-redesign-20260910/event-research-mainline](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/event-research-mainline) | `c025ce80d26d7b9f5c9ec3e0cfb59a6449d83dd4` |
| evidence-only-monitor-scope | [codex/pre-ui-redesign-20260910/evidence-only-monitor-scope](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/evidence-only-monitor-scope) | `1867d2ed1037ffe60414d8262b0d9e53766249f6` |
| fundclaw-gateway-p0-plan | [codex/pre-ui-redesign-20260910/fundclaw-gateway-p0-plan](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/fundclaw-gateway-p0-plan) | `f35d4c13e58203899dcab5b14a3970aef09666bc` |
| gildata-ai-extraction-hardening | [codex/pre-ui-redesign-20260910/gildata-ai-extraction-hardening](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/gildata-ai-extraction-hardening) | `bb18dfafed26669acc9f0a23ade442a10a11c812` |
| gildata-formal-evidence | [codex/pre-ui-redesign-20260910/gildata-formal-evidence](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/gildata-formal-evidence) | `62f63ebd2e1fd9f6ea94182cddf246f58289814a` |
| report-forecast-verdict | [codex/pre-ui-redesign-20260910/report-forecast-verdict](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/report-forecast-verdict) | `c03e6f5d6498b99322320a8b491c2925a866bd2c` |
| trusted-report-intake-recovery | [codex/pre-ui-redesign-20260910/trusted-report-intake-recovery](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/trusted-report-intake-recovery) | `6c1b4574224bd90f50725cf52746f09108d81ddf` |
| visible-auto-research | [codex/pre-ui-redesign-20260910/visible-auto-research](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/visible-auto-research) | `978cb5fba81e85b3a01671e6cce931576dc725b8` |
| design-explorations | [codex/pre-ui-redesign-20260910/design-explorations](https://github.com/GoldAnTo/fund-engine/tree/codex/pre-ui-redesign-20260910/design-explorations) | `e33debd6537ef51b75cb10ad5fe97c4ef4de0aa3` |

## 全部工作区覆盖

| 原分支 / 工作区 | 原 HEAD | 保存位置 |
|---|---|---|
| main | `bc69b465f8acdd6cc4d4d7a0762674ce16752518` | `main` / `bc69b465f8acdd6cc4d4d7a0762674ce16752518` |
| codex/backend-live-read | `a62d681616012457043a8f73070113780693cdad` | `codex/pre-ui-redesign-20260910/backend-live-read` / `0d317c4596acd6e6723fe9ef8f248de9710e5d64` |
| codex/backend-live-read-mainline | `42363e9f739142201efe5c74298aadc32f8f5599` | `main` / `42363e9f739142201efe5c74298aadc32f8f5599` |
| codex/cambricon-complete-case | `177db332c4345e2067a5f785a688801404357df4` | `main` / `177db332c4345e2067a5f785a688801404357df4` |
| codex/research-prototype-redesign | `13bf7a8c213e0557172c9e3136b1aeda0d9cb5ae` | `main` / `13bf7a8c213e0557172c9e3136b1aeda0d9cb5ae` |
| codex/acquisition-platform-design | `3793a18375c2dbebf7e0f6a33b32f114eb007db4` | `codex/pre-ui-redesign-20260910/acquisition-platform-design` / `d05dcf4c498559859be75aede9c03036093552e6` |
| codex/active-client-contract | `68d6d12af28914428e4954bb2dc3f62ca5c57bfe` | `main` / `68d6d12af28914428e4954bb2dc3f62ca5c57bfe` |
| codex/ai-company-research-mainline-plan | `eee00cb7912ad9ddb22604f1f6f4f2c52b09edfb` | `codex/pre-ui-redesign-20260910/ai-company-research-mainline-plan` / `abfee22477ce9a68343d040b07903d3d73e61874` |
| codex/company-research-prototype-delivery | `109372702913495f9725f9c6bb5d11686763c323` | `codex/company-research-prototype-delivery` / `109372702913495f9725f9c6bb5d11686763c323` |
| codex/event-impact-penetration | `439d82dd28249641a876c310318935c6a882c325` | `main` / `439d82dd28249641a876c310318935c6a882c325` |
| codex/event-research-mainline | `73045e3bc4aae5828c7c802af850e9ef2fdabffa` | `codex/pre-ui-redesign-20260910/event-research-mainline` / `c025ce80d26d7b9f5c9ec3e0cfb59a6449d83dd4` |
| codex/event-research-workbench | `a5fef6ca650916adbe8de9df6aa2ec47a473d4eb` | `main` / `a5fef6ca650916adbe8de9df6aa2ec47a473d4eb` |
| codex/evidence-only-monitor-scope | `a3aae523b8ea39a0b324f596fd8bc22bee4c400c` | `codex/pre-ui-redesign-20260910/evidence-only-monitor-scope` / `1867d2ed1037ffe60414d8262b0d9e53766249f6` |
| codex/fundclaw-gateway-p0-plan | `3d4889cfe94917a1892158ccf615457adc30c76c` | `codex/pre-ui-redesign-20260910/fundclaw-gateway-p0-plan` / `f35d4c13e58203899dcab5b14a3970aef09666bc` |
| codex/gildata-ai-extraction-hardening | `5e01ac6cc6fbe015968a4d0e26b1ce78f44fb00f` | `codex/pre-ui-redesign-20260910/gildata-ai-extraction-hardening` / `bb18dfafed26669acc9f0a23ade442a10a11c812` |
| codex/gildata-formal-evidence | `477d7fc5eeb208f4c930e3f74b1487b1e50fe2be` | `codex/pre-ui-redesign-20260910/gildata-formal-evidence` / `62f63ebd2e1fd9f6ea94182cddf246f58289814a` |
| codex/high-fidelity-prototype-restoration | `1cb6500b2a69062b21b30fc340259b637599ed0c` | `main` / `1cb6500b2a69062b21b30fc340259b637599ed0c` |
| codex/historical-run-replay | `fba71360bb35ba1af3b005d7e087d377e338e9ee` | `main` / `fba71360bb35ba1af3b005d7e087d377e338e9ee` |
| codex/industrial-foxconn-real-acceptance | `fb8b7cde983293ce8152df28ee5a3e33658c24e0` | `main` / `fb8b7cde983293ce8152df28ee5a3e33658c24e0` |
| codex/legacy-admission-ui | `a6864209c46fc48e8cf67b95d4ec3062ca69dac4` | `main` / `a6864209c46fc48e8cf67b95d4ec3062ca69dac4` |
| codex/legacy-case-admission | `dce942253111d9fe4e4ef9435beae03ddda05478` | `main` / `dce942253111d9fe4e4ef9435beae03ddda05478` |
| detached/main-clean-audit | `3732b1795ea90009f11acd896c02d7141faa4bc2` | `main` / `3732b1795ea90009f11acd896c02d7141faa4bc2` |
| codex/multi-run-transparency | `1d5817f878fb4ff94d0a54ac4cad8d91daac7017` | `main` / `1d5817f878fb4ff94d0a54ac4cad8d91daac7017` |
| codex/reliable-event-evidence-review | `cca5bb30593196d0abf6776519f4d8e08ae12ce2` | `main` / `cca5bb30593196d0abf6776519f4d8e08ae12ce2` |
| codex/report-forecast-verdict | `8349096c81b6b296564f16f2cfbbbf36770293b8` | `codex/pre-ui-redesign-20260910/report-forecast-verdict` / `c03e6f5d6498b99322320a8b491c2925a866bd2c` |
| codex/retired-prototype-cleanup | `e51e09c399c8ca4eb7909caaf20297117926c493` | `main` / `e51e09c399c8ca4eb7909caaf20297117926c493` |
| codex/source-category-successor | `f3a2668cdeba2b4ce561f2532ee238fab23a814a` | `main` / `f3a2668cdeba2b4ce561f2532ee238fab23a814a` |
| codex/tenant-case-read-surfaces | `6114392b75b746fafe39d4299f59ac7f7c8920af` | `main` / `6114392b75b746fafe39d4299f59ac7f7c8920af` |
| codex/tenant-fund-exposure | `d08a69d9d7d119c208189b3d27584176fa245424` | `main` / `d08a69d9d7d119c208189b3d27584176fa245424` |
| codex/tenant-market-expression | `80f636692093723f931fae65a732e0a905c3566a` | `main` / `80f636692093723f931fae65a732e0a905c3566a` |
| codex/tenant-research-protocol | `544b1609be570249a2e83f532d50381ef059c4a2` | `main` / `544b1609be570249a2e83f532d50381ef059c4a2` |
| codex/tenant-scoped-search | `295d936a45a36300cf0e4f3ca8f01304915a913f` | `main` / `295d936a45a36300cf0e4f3ca8f01304915a913f` |
| codex/tenant-side-pages | `e1a253c0012a8a71096686f4026a15df17461425` | `main` / `e1a253c0012a8a71096686f4026a15df17461425` |
| codex/trusted-report-intake-recovery | `297aa11a0d5c28b36e74af58630152932f271dfb` | `codex/pre-ui-redesign-20260910/trusted-report-intake-recovery` / `6c1b4574224bd90f50725cf52746f09108d81ddf` |
| codex/visible-auto-research | `babc2f76563c9102cbc66adb9022635b6a77cb4d` | `codex/pre-ui-redesign-20260910/visible-auto-research` / `978cb5fba81e85b3a01671e6cce931576dc725b8` |

## 本地完整恢复与公开快照的边界

另有完整 Git bundle、工作区改动压缩包和来源清单保存在本机备份目录。原始索引与 HEAD 的校验记录证明本次快照没有改动其他工作区。

以下内容保留在本地，不作为新增公开代码快照上传：运行产生的 walkthrough JSON/JSONL、受许可数据原件、本地截图、构建生成的 Vite 配置文件，以及原本忽略的凭据、数据库和运行输出。六份新增 Gateway 审计/计划文档中的本机用户路径在公开副本中已归一化；本地副本保留原文。

此前已存在的三个新建研究 HTML 草图和设计说明存入 `design-explorations` 快照的 `docs/archive/research-entry-20260910/`，属于旧设计存档。

## 恢复方式

在有远端权限的环境中，先运行 `git fetch origin`，然后从需要的快照分支创建工作分支。例如：

```sh
git switch -c restore-company origin/codex/company-research-prototype-delivery
```

需要某个未集成分支的工作状态时，从对应的 `origin/codex/pre-ui-redesign-20260910/...` 创建工作分支。各快照独立保存，请按所需业务选择，不要将它们理解为一套已合并实现。
