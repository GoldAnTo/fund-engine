# 22bc46c8 浅度健康 review 报告

> 检查对象:commit `22bc46c8 chore: commit accumulated backend and frontend changes`
> 检查时间:2026-09-03 (脚本执行时间)
> 检查范围:本 commit 新增 / 修改的 224 个文件中的 202 个代码文件 (`.py` 121 + `.tsx` 48 + `.ts` 33 + `.mjs` 4)
> 排除:`.json`、`.css`、`.html`、配置示例等 22 个非代码资源文件

## 工具

- [review-22bc46c8.mjs](./review-22bc46c8.mjs): 占位符扫描 + 行数统计 + Python AST 语法解析
- `tsc --noEmit -p frontend/tsconfig.json`: 完整前端类型检查
- `python3 -c "import app"`: backend import smoke test

## 结果汇总

| 检查项 | 结果 | 备注 |
| --- | --- | --- |
| **占位符扫描** (TODO / FIXME / XXX / HACK / `raise NotImplementedError` / `pass # stub` / `... # todo`) | **0 hit** | 202 文件中没有任何 placeholder 标记 |
| **Python AST 语法解析** (commit 内 121 个 .py) | **0 error** | 全部通过 `py_compile` |
| **TypeScript 编译** (整个 frontend 项目) | **0 error** | `tsc --noEmit` exit 0 |
| **Backend import smoke** (`python3 -c "import app"`) | **0 error** | backend 模块全部 import 成功 |
| **小文件** (≤ 30 行) | **0 是 stub** | 抽查的 6 个最小文件全部是合理的常量 / 简单 wrapper / 单 case 测试 / alembic migration |
| **总代码行数** | **87020 行** | 202 文件 |

> 注:整个仓库 Python 范围有 2 个 syntax error,均在 `.reference/vcra/backend/tests/test_*.py`(BOM 头问题),与本 commit 无关。

## 抽查的小文件

| 文件 | 行数 | 性质 | 评价 |
| --- | --- | --- | --- |
| `backend/app/services/monitor_frequencies.py` | 7 | 常量字典 | ✓ 完整 |
| `frontend/src/app/CompanyResearchApp.tsx` | 8 | React Router 壳组件 | ✓ 完整 |
| `frontend/src/eventMarket/MarketActor.tsx` | 8 | Context + hook | ✓ 完整 |
| `backend/tests/test_ai_output_scope.py` | 11 | 参数化测试 | ✓ 完整 |
| `frontend/src/app/ResearchArchiveApp.tsx` | 14 | router + fallback 路由 | ✓ 完整 |
| `frontend/src/tests/researchPresentation.test.ts` | 15 | 测试 | ✓ 完整 |
| `frontend/src/app/CompanyResearchRoutes.tsx` | 17 | 路由表 | ✓ 完整 |
| `frontend/src/data/underwritingResearchApi.test.ts` | 17 | 测试 | ✓ 完整 |
| `frontend/src/tests/researchSubmission.test.ts` | 18 | 测试 | ✓ 完整 |
| `backend/alembic/versions/0071_ai_run_usage.py` | 19 | alembic migration (含 downgrade) | ✓ 完整 |

## 评估

按上述指标,22bc46c8 的代码部分**看起来是完整、可运行、健康的**:

- 0 个 placeholder / stub 标记 — 同事写完后没有遗留 TODO
- 0 个 Python / TypeScript 编译错误 — 所有改动与既有项目兼容
- 0 个 import 错误 — 真实可用,不缺依赖

## 不能保证的事

浅度 review **没有验证**:

1. **运行时行为**: `tsc --noEmit` 只查类型,不验证 React 组件是否真的渲染 / API 调用是否真返回数据
2. **测试覆盖率**: backend 49 个新增 test 文件是否真的覆盖了变更逻辑,需要实际跑 pytest
3. **CI 流水线**: GitHub Actions 4 个必需 status check 在 push 时被绕过(`force-push`),未实际验证
4. **业务规则正确性**: AI usage tracking / scope validation / output schema 等是否符合产品需求,需要产品 / 业务同事 review
5. **跨文件 / 跨服务契约**: 修改后端 schema 后,前端 API client 是否一致 — 类型对齐通过 tsc 验证,但字段语义未必

## 后续建议

| 优先级 | 行动 | 原因 |
| --- | --- | --- |
| 高 | 拉一次 CI 让 GitHub Actions 实际跑 | 跳过 status check 后,真实流水线状态未知 |
| 高 | 跑 backend pytest 看新 49 个 test 是否通过 | audit 自承认有 6 个 fail / 41 skip 未修 |
| 中 | 让 `luhuiling` 同事 self-review 这次 commit 的代码 | 作者自己最清楚每个文件的完成度 |
| 中 | 把 `.reference/vcra/` 的 BOM 问题修了 | 仓库卫生 |
| 低 | docs/evaluation/walkthrough/ 的 jsonl 是 reproduce 产物,建议加 `.gitignore` | 数据大、易变、含敏感数据 |
