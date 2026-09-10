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
      let targetCaseId = caseId;
      // 1) 若 caseId 缺失或不真实存在，自动选中第一个可访问的研究案例
      if (!targetCaseId || targetCaseId === "ai-compute") {
        try {
          const list = await researchClient.listCaseSummaries();
          if (list.length > 0) {
            targetCaseId = list[0].id;
            setCaseId(targetCaseId);
          }
        } catch {
          // ignore — 仍尝试原 caseId
        }
      }
      const v = await researchClient.getConclusionView(
        targetCaseId,
        { cutoff: cutoff ?? undefined },
      );
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

  // 三段简短结论气泡（按 thesis 顺序截取 status_label + falsifier 摘要）
  // 第一段使用 header.conclusionStatus 决定的正式判断标题（原图「正式反驳结论」）
  const formalTitle = (() => {
    if (header.conclusionStatus === "supported") return "正式支持结论";
    if (header.conclusionStatus === "contradicted") return "正式反驳结论";
    if (header.conclusionStatus === "insufficient_evidence")
      return "正式待证据结论";
    return "正式结论待定";
  })();
  const conclusionBullets = view.keyFactors.slice(0, 3).map((f, idx) => ({
    id: f.factorId,
    title: idx === 0 ? formalTitle : `${f.roleLabel} · ${f.thesisTitle}`,
    subtitle: f.directEvidence.slice(0, 60),
    snapshot: `数据快照 · ${f.factorLabel.slice(0, 30)}`,
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
          <div className="conclusion-actions">
            <input
              type="search"
              placeholder="搜索结论、因素、证据..."
              className="conclusion-search"
              aria-label="搜索结论、因素、证据"
            />
            <span className="conclusion-cutoff-badge" data-testid="cutoff-badge">
              ⌚ 历史截止点 {formatDate(header.evidenceCutoff)}
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
          {conclusionBullets.map((b, idx) => {
            const variant =
              idx === 0
                ? header.conclusionStatus === "contradicted"
                  ? "contradict"
                  : header.conclusionStatus === "supported"
                    ? "default"
                    : "warning"
                : (STATUS_LABEL_TO_VARIANT[b.title.split(" · ")[0]] ?? "default");
            return (
              <PaperCard
                key={b.id}
                variant={variant}
                padding="compact"
                data-testid={`conclusion-bullet-${b.id}`}
                className={`conclusion-bullet${
                  idx === 0 ? " conclusion-bullet--formal" : ""
                }`}
              >
                <strong
                  className={
                    idx === 0 ? "conclusion-bullet__title" : undefined
                  }
                >
                  {b.title}
                </strong>
                <span className="conclusion-bullet__subtitle">{b.subtitle}</span>
                <span className="conclusion-bullet__snapshot muted">
                  {b.snapshot}
                </span>
              </PaperCard>
            );
          })}
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
                  <span className="key-factor__id">{f.factorId}</span>
                  <span className="key-factor__title">{f.factorLabel}</span>
                  <StatusBadge
                    variant={STATUS_LABEL_TO_VARIANT[f.statusLabel] ?? "default"}
                  >
                    {f.statusLabel}
                  </StatusBadge>
                </div>
                <div className="key-factor__body">
                  <p>
                    <span className="muted">形成为需求入口：</span>
                    {f.mechanism}
                  </p>
                  <p>
                    <span className="muted">数据快照：</span>
                    {f.directEvidence}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </PaperCard>

        {/* 左下：所选因素解释（与设计原型 11 一致） */}
        {selectedFactor && (
          <PaperCard
            kicker="所选因素解释"
            title={gapExplanation.factorLabel}
            padding="compact"
            data-testid="gap-card"
          >
            <dl className="gap-explain">
              <div>
                <dt>为什么</dt>
                <dd>{gapExplanation.why}</dd>
              </div>
              <div>
                <dt>适用边界</dt>
                <dd>{gapExplanation.applicableScope}</dd>
              </div>
              <div>
                <dt>数据模式</dt>
                <dd>{gapExplanation.dataPattern}</dd>
              </div>
              <div>
                <dt>假设</dt>
                <dd>{gapExplanation.categoryAlt}：{gapExplanation.rationale}</dd>
              </div>
              <div>
                <dt>正向判断</dt>
                <dd>
                  <StatusBadge
                    variant={
                      STATUS_LABEL_TO_VARIANT[
                        selectedFactor.statusLabel as keyof typeof STATUS_LABEL_TO_VARIANT
                      ] ?? "default"
                    }
                  >
                    {selectedFactor.statusLabel}
                  </StatusBadge>
                </dd>
              </div>
            </dl>
          </PaperCard>
        )}

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
                    {/* 列：取每个 factor 作为一个列（按 thesis 顺序，去重） */}
                    {Array.from(
                      new Set(view.keyFactors.map((f) => f.factorId)),
                    ).map((fid) => {
                      const f = view.keyFactors.find(
                        (kf) => kf.factorId === fid,
                      );
                      return (
                        <th key={fid} title={fid}>
                          {f?.factorLabel ?? fid}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {comparison.columns.slice(1).map((dimension) => {
                    return (
                      <tr key={dimension}>
                        <th scope="row" className="comparison-table__dim">
                          {dimension}
                        </th>
                        {Array.from(
                          new Set(view.keyFactors.map((f) => f.factorId)),
                        ).map((fid) => {
                          const f = view.keyFactors.find(
                            (kf) => kf.factorId === fid,
                          );
                          // 在 comparison.rows 中找到属于此 factor 且匹配此 dimension 的 cell
                          const row = comparison.rows.find(
                            (r) => r.factorId === fid,
                          );
                          const text = row
                            ? row.cells.find((c) => c.columnLabel === dimension)
                                ?.text ?? "—"
                            : "—";
                          if (dimension === "评审角色" && f) {
                            return (
                              <td key={fid}>
                                <StatusBadge
                                  variant={
                                    STATUS_LABEL_TO_VARIANT[
                                      f.roleLabel as keyof typeof STATUS_LABEL_TO_VARIANT
                                    ] ?? "default"
                                  }
                                >
                                  {f.roleLabel}
                                </StatusBadge>
                              </td>
                            );
                          }
                          return (
                            <td
                              key={fid}
                              onClick={() => setSelectedFactorId(fid)}
                              className={
                                fid === selectedFactorId
                                  ? "comparison-cell--selected"
                                  : undefined
                              }
                            >
                              {text}
                            </td>
                          );
                        })}
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

        {/* ── 右栏：解释与重现 + 复现清单 ── */}
        <div className="conclusion-right">
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
          </PaperCard>

          {/* 底部双行警告（与设计原型 11 一致） */}
          <div
            className="conclusion-frozen-warning"
            data-testid="frozen-warning"
            role="note"
          >
            <strong>使用冻结输入重新运算</strong>
            <p>
              同一版本呈现相同输入集合；AI 复跑只生成新草案，不能覆盖原结论。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}