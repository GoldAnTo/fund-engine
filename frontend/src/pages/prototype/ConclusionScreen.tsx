/**
 * 结论与关键因素页 (屏幕 11 · 设计原型11)
 *
 * 视觉对应 prototype/设计原型11-结论与关键因素.png：
 *   - 顶部「结论与关键因素」+ 截止/快照/审核元信息 + 3 个简短结论气泡
 *   - 左中右三栏：
 *       左：关键因素列表（含评审维度/角色/复盘链路）
 *       中：竞争性因素比较表 + 支持/反驳/缺口 + 结论形成路径
 *       右：解释与重现 + 复现清单
 *
 * 数据来源：researchClient.getConclusionView(caseId, { cutoff })
 *   - HTTP 模式：GET /api/v1/research-cases/{id}/conclusion
 *   - Mock 模式：mockResearchAdapter.buildConclusionView
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PaperCard } from "../../components/prototype/PaperCard";
import { PageHeader } from "../../components/prototype/PageHeader";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  ConclusionKeyFactor,
  ConclusionView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const STATUS_LABEL_TO_VARIANT: Record<
  string,
  "default" | "warning" | "contradict" | "ai"
> = {
  已复现: "default",
  已被反驳: "contradict",
  证据不足: "warning",
  待人工: "ai",
  待证据: "warning",
  待传递: "warning",
};

const ROLE_LABEL_TO_VARIANT: Record<
  string,
  "default" | "warning" | "contradict" | "ai"
> = {
  已复现: "default",
  待人工: "ai",
  待证据: "warning",
  待传递: "warning",
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

function findCaseIdFromQuery(params: URLSearchParams): string | null {
  return params.get("case");
}

export function ConclusionScreen() {
  const params = useParams<{ caseId?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const fallbackCaseId = params.caseId ?? "ai-compute";
  const initialCaseId = findCaseIdFromQuery(searchParams) ?? fallbackCaseId;
  const [caseId, setCaseId] = useState<string>(initialCaseId);
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<ConclusionView | null>(null);
  const [selectedFactorId, setSelectedFactorId] = useState<string | null>(null);
  const [cutoff, setCutoff] = useState<string | null>(
    searchParams.get("cutoff"),
  );

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const v = await researchClient.getConclusionView(caseId, { cutoff: cutoff ?? undefined });
      setView(v);
      const firstFactor = v.keyFactors[0]?.factorId ?? null;
      setSelectedFactorId(firstFactor);
      setState({ kind: "ready" });
    } catch (e) {
      setState({
        kind: "error",
        message: e instanceof Error ? e.message : "加载失败",
      });
    }
  }, [caseId, cutoff]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedFactor: ConclusionKeyFactor | null = useMemo(() => {
    if (!view) return null;
    return (
      view.keyFactors.find((f) => f.factorId === selectedFactorId) ??
      view.keyFactors[0] ??
      null
    );
  }, [view, selectedFactorId]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen conclusion-screen">
        <div className="conclusion-loading">载入「结论与关键因素」…</div>
      </div>
    );
  }

  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen conclusion-screen">
        <div className="conclusion-error" role="alert">
          {state.message ?? "未能读取结论页数据"}
          <button
            type="button"
            className="link-button"
            onClick={() => void load()}
            style={{ marginLeft: 12 }}
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  const { header, comparison, sourceGroups, reproductionManifest, causalPath, gapExplanation } = view;

  // 三段简短结论气泡（取每个 KeyFactor 的 status_label 作为结论片段）
  const conclusionBullets = view.keyFactors.slice(0, 3).map((f) => ({
    id: f.factorId,
    text: f.statusLabel + " · " + (f.falsifier ?? "—"),
  }));

  return (
    <div
      className="prototype-screen conclusion-screen"
      data-testid="conclusion-screen"
      data-case-id={caseId}
    >
      <PageHeader
        breadcrumbs={[
          { label: "行业研究", to: "/themes" },
          { label: "AI 算力链" },
          { label: "结论与关键因素" },
        ]}
        title="结论与关键因素"
        lede="结论优先，底图回到因素，因果边、支持与反驳，正反判断只由冻结输入与人工复核产生。"
        actions={
          <div className="conclusion-cutoff-control">
            <label>
              证据截止
              <input
                type="date"
                value={(cutoff ?? header.evidenceCutoff).slice(0, 10)}
                onChange={(e) => {
                  const next = e.target.value;
                  setCutoff(next ? `${next}T00:00:00+08:00` : null);
                  if (next) searchParams.set("cutoff", next);
                  else searchParams.delete("cutoff");
                  setSearchParams(searchParams, { replace: true });
                }}
              />
            </label>
            <span className="conclusion-cutoff-hint">
              历史截止 {formatDate(header.evidenceCutoff)} ·{" "}
              {header.reviewState === "reviewed" ? "已人工复核" : "AI 临时标记"}
            </span>
          </div>
        }
      />

      {/* 顶部 Header：研究案例 ID + 结论版本 + 截止/审核/版本 */}
      <PaperCard
        kicker="RESEARCH CASE · FORMAL JUDGMENT"
        title={
          <span>
            结论与关键因素 ·{" "}
            <span className="muted">{header.caseTitle}</span>
          </span>
        }
        actions={
          <div className="conclusion-meta">
            <span>
              证据截止
              <strong>{formatDate(header.evidenceCutoff)}</strong>
            </span>
            <span>
              结论版本
              <strong>{header.snapshotId ?? "—"}</strong>
            </span>
            <span>
              {header.aiProvisional ? "AI 临时标记" : "人工复核"}
              <strong>{header.reviewer ?? "—"}</strong>
            </span>
          </div>
        }
      >
        <p className="conclusion-summary">{header.conclusionText}</p>
        <div className="conclusion-bullets">
          {conclusionBullets.map((b) => (
            <PaperCard
              key={b.id}
              variant={
                STATUS_LABEL_TO_VARIANT[
                  b.text.split(" · ")[0] as keyof typeof STATUS_LABEL_TO_VARIANT
                ] ?? "default"
              }
              padding="compact"
              data-testid={`conclusion-bullet-${b.id}`}
            >
              <strong>{b.id}</strong>
              <span>{b.text}</span>
            </PaperCard>
          ))}
        </div>
      </PaperCard>

      {/* 主体三栏 */}
      <div className="conclusion-grid">
        {/* ── 左栏：关键因素 ─────────────────────────── */}
        <PaperCard
          kicker="关键因素"
          title="评审维度 / 角色 / 复盘链路"
          padding="compact"
        >
          <ul className="key-factor-list">
            {view.keyFactors.map((f) => (
              <li
                key={f.factorId}
                className={
                  f.factorId === selectedFactorId
                    ? "key-factor key-factor--selected"
                    : "key-factor"
                }
                onClick={() => setSelectedFactorId(f.factorId)}
                data-testid={`key-factor-${f.factorId}`}
              >
                <div className="key-factor__head">
                  <strong className="key-factor__id">{f.factorId}</strong>
                  <span className="key-factor__title">{f.factorLabel}</span>
                  <StatusBadge
                    variant={STATUS_LABEL_TO_VARIANT[f.statusLabel] ?? "default"}
                  >
                    {f.statusLabel}
                  </StatusBadge>
                  <StatusBadge
                    variant={ROLE_LABEL_TO_VARIANT[f.roleLabel] ?? "default"}
                  >
                    {f.roleLabel}
                  </StatusBadge>
                </div>
                <div className="key-factor__body">
                  <p>
                    <span className="muted">机制：</span>
                    {f.mechanism}
                  </p>
                  <p>
                    <span className="muted">直接证据：</span>
                    {f.directEvidence}
                  </p>
                  <p>
                    <span className="muted">替代解释：</span>
                    {f.alternatives}
                  </p>
                  <p>
                    <span className="muted">时序：</span>
                    <code className="conclusion-mono">{f.timeOrder}</code>
                  </p>
                  {f.scopeWarning && (
                    <p className="key-factor__warning">
                      ⚠ 范围警示：{f.scopeWarning}
                    </p>
                  )}
                  <p className="key-factor__falsifier">
                    证伪：{f.falsifier}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </PaperCard>

        {/* ── 中栏：竞争性因素比较 + 支持/反驳/缺口 + 结论形成路径 ── */}
        <div className="conclusion-center">
          <PaperCard
            kicker="竞争性因素比较"
            title="评审维度 × 直接证据 × 替代解释"
            padding="compact"
            data-testid="comparison-card"
          >
            <div className="comparison-table-wrap">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>评审维度</th>
                    {comparison.columns.slice(1).map((col) => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {comparison.rows.map((row) => {
                    const cellMap = Object.fromEntries(
                      row.cells.map((c) => [c.columnId, c.text]),
                    );
                    return (
                      <tr
                        key={row.factorId}
                        onClick={() => setSelectedFactorId(row.factorId)}
                        className={
                          row.factorId === selectedFactorId
                            ? "comparison-row comparison-row--selected"
                            : "comparison-row"
                        }
                      >
                        <td>
                          <strong>{row.factorLabel}</strong>
                          <br />
                          <code className="muted">{row.factorId}</code>
                        </td>
                        <td>{cellMap["direct_evidence"]}</td>
                        <td>{cellMap["backing_evidence"]}</td>
                        <td>{cellMap["scope_warning"]}</td>
                        <td>{cellMap["alternative"]}</td>
                        <td>{cellMap["impact_object"]}</td>
                        <td>
                          <StatusBadge
                            variant={
                              STATUS_LABEL_TO_VARIANT[
                                cellMap["reviewer_role"] as keyof typeof STATUS_LABEL_TO_VARIANT
                              ] ?? "default"
                            }
                          >
                            {cellMap["reviewer_role"]}
                          </StatusBadge>
                        </td>
                        <td>{cellMap["gate_result"]}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="muted" style={{ fontSize: "0.85em" }}>
              全部数据来自冻结账本；每条 evidence 单击行可在右侧复现清单中跳转到对应片段。
            </p>
          </PaperCard>

          {/* 支持、反驳与缺口 */}
          <PaperCard
            kicker="支持、反驳与缺口"
            title="全部链接按 (role × 审核状态) 分组"
            padding="compact"
            data-testid="source-groups-card"
          >
            {sourceGroups.map((g) => (
              <section key={g.sectionLabel} className="source-group">
                <h4>
                  {g.sectionLabel} <span className="muted">· {g.relations.length} 条</span>
                </h4>
                <ul>
                  {g.relations.map((r, idx) => (
                    <li key={`${g.sectionLabel}-${idx}`}>
                      <strong>{r.documentTitle}</strong>
                      <span className="muted"> · {r.publisher ?? "—"}</span>
                      <p>"{r.citation}"</p>
                      <code className="muted">{r.locator}</code>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </PaperCard>

          {/* 结论形成路径 */}
          {causalPath.length > 0 && (
            <PaperCard
              kicker="结论形成路径"
              title="因果步骤"
              padding="compact"
              data-testid="causal-path-card"
            >
              <ol className="causal-path">
                {causalPath.map((s) => (
                  <li key={s.sequence}>
                    <span className="causal-path__seq">{s.sequence}</span>
                    <span>{s.description}</span>
                  </li>
                ))}
              </ol>
            </PaperCard>
          )}
        </div>

        {/* ── 右栏：所选因素解释 + 解释与重现 + 复现清单 ── */}
        <div className="conclusion-right">
          {selectedFactor && (
            <PaperCard
              kicker="所选因素解释"
              title={gapExplanation.factorLabel}
              padding="compact"
              data-testid="gap-card"
            >
              <p>
                <span className="muted">为什么：</span>
                {gapExplanation.why}
              </p>
              <p>
                <span className="muted">{gapExplanation.category}：</span>
                {gapExplanation.applicableScope}
              </p>
              <p>
                <span className="muted">数据模式：</span>
                {gapExplanation.dataPattern}
              </p>
              <p>
                <span className="muted">{gapExplanation.categoryAlt}：</span>
                {gapExplanation.rationale}
              </p>
            </PaperCard>
          )}

          <PaperCard
            kicker="解释与重现"
            title={reproductionManifest.currentSelectionLabel}
            padding="compact"
            actions={
              <Link
                to={`/relationships/${caseId}`}
                className="link-button"
                aria-label="进入证据图谱"
              >
                进入图谱 ↗
              </Link>
            }
            data-testid="manifest-card"
          >
            <dl className="manifest">
              <div>
                <dt>当前选中</dt>
                <dd>
                  <code>{reproductionManifest.currentSelectionState}</code>
                </dd>
              </div>
              <div>
                <dt>正式判断</dt>
                <dd>{reproductionManifest.formalJudgment}</dd>
              </div>
              <div>
                <dt>研究快照</dt>
                <dd>
                  <code>{reproductionManifest.researchSnapshot}</code>
                </dd>
              </div>
              <div>
                <dt>文档版本</dt>
                <dd>
                  <code>{reproductionManifest.documentVersion}</code>
                </dd>
              </div>
              <div>
                <dt>发布记录</dt>
                <dd>
                  <code>{reproductionManifest.publisherRecord}</code>
                </dd>
              </div>
              <div>
                <dt>可用性</dt>
                <dd>{reproductionManifest.availableAt}</dd>
              </div>
              <div>
                <dt>复现者</dt>
                <dd>{reproductionManifest.reproducer}</dd>
              </div>
              <div>
                <dt>对比协议</dt>
                <dd>
                  <code>{reproductionManifest.factorCompareVersion}</code>
                </dd>
              </div>
            </dl>
          </PaperCard>

          <PaperCard
            kicker="复现清单"
            title="可重建的依赖"
            padding="compact"
            data-testid="recheck-card"
          >
            <code className="conclusion-mono-block">
              {reproductionManifest.recheckManifest}
            </code>
            <p className="muted" style={{ marginTop: 12 }}>
              使用冻结输入重新运算；AI 复跑只回填新草案，不能覆盖原结论。
            </p>
          </PaperCard>
        </div>
      </div>
    </div>
  );
}