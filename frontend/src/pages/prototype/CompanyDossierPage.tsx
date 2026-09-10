import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  CompanyDossierView,
  CompanyFundHolderView,
  CompanyThemeRoleView,
  CompanyThesisJudgment,
  CompanyValuationView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const TYPE_LABEL: Record<string, string> = {
  listed: "已上市",
  unlisted: "未上市",
  otc: "柜台",
};

const AI_VARIANT: Record<string, "ai" | "support" | "contradict" | "warning"> = {
  supported: "support",
  contradicted: "contradict",
  insufficient_evidence: "warning",
  pending: "ai",
};

const AI_LABEL: Record<string, string> = {
  supported: "支持",
  contradicted: "反证",
  insufficient_evidence: "证据不足",
  pending: "未评估",
};

const REVIEW_VARIANT: Record<string, "reviewed" | "warning" | "ai" | "support" | "contradict" | "draft"> = {
  confirmed: "reviewed",
  modified: "warning",
  rejected: "contradict",
};

const REVIEW_LABEL: Record<string, string> = {
  confirmed: "已确认",
  modified: "已修正",
  rejected: "已驳回",
};

export function CompanyDossierPage() {
  const params = useParams<{ companyId?: string }>();
  const companyId = params.companyId ?? "";
  const [searchParams, setSearchParams] = useSearchParams();
  const cutoff = searchParams.get("cutoff") ?? undefined;
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<CompanyDossierView | null>(null);
  const [focusThesisId, setFocusThesisId] = useState<string | null>(
    searchParams.get("thesis_id"),
  );

  useEffect(() => {
    if (!companyId) {
      setState({ kind: "error", message: "缺少公司 ID" });
      return;
    }
    let cancelled = false;
    setState({ kind: "loading" });
    researchClient
      .getCompanyDossier(companyId, cutoff ? { cutoff } : undefined)
      .then((d) => {
        if (!cancelled) {
          setView(d);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, cutoff]);

  // 验证 ThemeRole 是否需要按 cutoff 隐藏（mock 已落定；保留 selector 以兼容未来历史回放）
  const visibleRoles = useMemo<CompanyThemeRoleView[]>(() => {
    if (!view) return [];
    if (!cutoff) return view.themeRoles;
    return view.themeRoles.filter((r) => {
      if (r.applicableFrom && r.applicableFrom > cutoff.slice(0, 10))
        return false;
      if (r.applicableTo && r.applicableTo <= cutoff.slice(0, 10))
        return false;
      return true;
    });
  }, [view, cutoff]);

  function setCutoff(value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("cutoff", value);
    else next.delete("cutoff");
    setSearchParams(next, { replace: true });
  }

  function setFocusThesis(id: string | null) {
    setFocusThesisId(id);
    const next = new URLSearchParams(searchParams);
    if (id) next.set("thesis_id", id);
    else next.delete("thesis_id");
    setSearchParams(next, { replace: true });
  }

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="company-dossier-loading">
        <p>正在加载公司档案…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="company-dossier-error">
        <div className="form-error">
          公司档案加载失败：{state.message ?? "未知错误"}
        </div>
        <p>
          <Link to="/companies">← 返回公司列表</Link>
        </p>
      </div>
    );
  }

  const emptyCompany =
    visibleRoles.length === 0 &&
    view.relatedTheses.length === 0 &&
    view.valuations.length === 0 &&
    view.fundHolders.length === 0;

  return (
    <div
      className="prototype-screen company-dossier-screen"
      data-testid="company-dossier-screen"
    >
      <PageHeader
        title={`${view.company.name} · 公司档案`}
        eyebrow="公司研究 · 逆向视图"
        lede="公司层不产生任何独立结论；以下每个状态都能回链到案例层的 Thesis / AIAssessment / ReviewDecision / SourceStatement / SourceSpan。"
        meta={
          <dl className="theme-meta-grid">
            <MetaCell label="代码" value={view.company.code} />
            <MetaCell label="类型" value={TYPE_LABEL[view.company.type] ?? view.company.type} />
            <MetaCell
              label="股票"
              value={`${view.stocks.length} 只`}
            />
            <MetaCell
              label="主题角色"
              value={`${visibleRoles.length} 条${
                visibleRoles.length !== view.themeRoles.length
                  ? ` · 已过滤 ${view.themeRoles.length - visibleRoles.length}`
                  : ""
              }`}
            />
            <MetaCell
              label="证据截止"
              value={view.cutoff.slice(0, 10)}
              mono
            />
            <MetaCell
              label="历史回放"
              value={view.isHistorical ? "是" : "否"}
              warn={view.isHistorical}
            />
          </dl>
        }
        actions={
          <>
            <Link className="prototype-button" to="/companies">
              ← 公司列表
            </Link>
            {cutoff ? (
              <button
                type="button"
                className="prototype-button"
                onClick={() => setCutoff(null)}
              >
                回到当下
              </button>
            ) : (
              <button
                type="button"
                className="prototype-button"
                onClick={() => setCutoff("2025-06-30T00:00:00+08:00")}
              >
                切到 2025-06-30 回放
              </button>
            )}
          </>
        }
      />

      {emptyCompany && (
        <PaperCard data-testid="company-dossier-empty">
          <p className="muted">
            该公司当前没有可回链到研究案例的角色、命题、估值或持仓记录。
            可在 <Link to="/themes">主题列表</Link> 创建或挂接角色，或在{" "}
            <Link to="/plan">研究计划</Link> 中分配命题。
          </p>
        </PaperCard>
      )}

      <div className="company-dossier__columns">
        {/* 左：身份 + 股票 */}
        <section className="company-dossier__side">
          <PaperCard>
            <p className="section-kicker">身份</p>
            <h2>{view.company.name}</h2>
            <p className="muted">代码 {view.company.code}</p>
            <p className="muted" style={{ fontSize: 12 }}>
              {view.company.createdAt
                ? `账本建立 ${view.company.createdAt.slice(0, 10)}`
                : "账本建立时间未记录"}
            </p>
          </PaperCard>

          <PaperCard>
            <p className="section-kicker">股票 ({view.stocks.length})</p>
            {view.stocks.length === 0 ? (
              <p className="muted">未挂接股票</p>
            ) : (
              <ul style={{ paddingLeft: 16, fontSize: 13 }}>
                {view.stocks.map((s) => (
                  <li key={s.id}>
                    {s.code} <span className="muted">· {s.market}</span>{" "}
                    <span className="muted">{s.name}</span>
                  </li>
                ))}
              </ul>
            )}
          </PaperCard>
        </section>

        {/* 中：主题角色 + 关联命题 + 估值 + 持仓 */}
        <section className="company-dossier__main">
          <PaperCard data-testid="company-dossier-roles">
            <p className="section-kicker">
              跨案例主题角色 ({visibleRoles.length})
            </p>
            {visibleRoles.length === 0 ? (
              <p className="muted">无适用期间内的主题角色</p>
            ) : (
              <ul className="theme-role-list">
                {visibleRoles.map((r) => (
                  <li key={r.id}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <strong>{r.role}</strong>
                      {r.caseId && (
                        <span className="muted" style={{ fontSize: 12 }}>
                          · {r.caseTitle ?? r.caseId}
                        </span>
                      )}
                    </div>
                    <p style={{ fontSize: 12, margin: "4px 0" }}>
                      适用{" "}
                      {r.applicableFrom ?? "—"} 至 {r.applicableTo ?? "至今"}
                    </p>
                    {r.statementText && (
                      <p
                        style={{ fontSize: 12, color: "var(--ink-soft)" }}
                        data-testid={`company-role-statement-${r.id}`}
                      >
                        “{r.statementText}”
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </PaperCard>

          <PaperCard data-testid="company-dossier-theses">
            <p className="section-kicker">
              关联命题及判断 ({view.relatedTheses.length})
            </p>
            {view.relatedTheses.length === 0 ? (
              <p className="muted">该公司未挂接任何命题</p>
            ) : (
              <ul className="company-thesis-list">
                {view.relatedTheses.map((t) => (
                  <ThesisJudgmentRow
                    key={t.thesisId}
                    thesis={t}
                    focused={focusThesisId === t.thesisId}
                    onFocus={() => setFocusThesis(t.thesisId)}
                  />
                ))}
              </ul>
            )}
          </PaperCard>

          <PaperCard data-testid="company-dossier-valuations">
            <p className="section-kicker">
              估值快照 ({view.valuations.length})
            </p>
            {view.valuations.length === 0 ? (
              <p className="muted">无估值快照记录</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>指标</th>
                    <th>数值</th>
                    <th>截止日</th>
                    <th>口径</th>
                  </tr>
                </thead>
                <tbody>
                  {view.valuations.map((v, i) => (
                    <ValuationRow
                      key={`${v.stockId}-${v.metricName}-${v.asOfDate}-${i}`}
                      valuation={v}
                    />
                  ))}
                </tbody>
              </table>
            )}
            {view.valuations.length > 0 && (
              <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
                注：as_of_date 为行情 / 报告截止日；数据源与口径按
                `source` / `definition` 原样返回，页面仅展示不重新计算。
              </p>
            )}
          </PaperCard>

          <PaperCard data-testid="company-dossier-holders">
            <p className="section-kicker">
              基金披露持仓 ({view.fundHolders.length})
            </p>
            {view.fundHolders.length === 0 ? (
              <p className="muted">无基金披露持仓</p>
            ) : (
              <table className="prototype-table">
                <thead>
                  <tr>
                    <th>基金</th>
                    <th>股票</th>
                    <th>权重</th>
                    <th>报告期</th>
                    <th>披露日</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {view.fundHolders.map((h, i) => (
                    <FundHolderRow
                      key={`${h.fundId}-${h.stockId}-${h.reportPeriod}-${i}`}
                      holder={h}
                    />
                  ))}
                </tbody>
              </table>
            )}
            {view.fundHolders.length > 0 && (
              <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
                注：基金披露持仓为报告期 + 披露日双重时间锚定；
                权重口径为数据源原始定义（占流通 A 股 / 占净值并存），不强制统一。
              </p>
            )}
          </PaperCard>
        </section>
      </div>
    </div>
  );
}

function ThesisJudgmentRow({
  thesis,
  focused,
  onFocus,
}: {
  thesis: CompanyThesisJudgment;
  focused: boolean;
  onFocus: () => void;
}) {
  return (
    <li
      className={focused ? "is-focused" : ""}
      data-testid={`company-thesis-row-${thesis.thesisId}`}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <strong>{thesis.title ?? thesis.statement.slice(0, 28)}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          · {thesis.caseTitle ?? thesis.caseId}
        </span>
        <button
          type="button"
          className="prototype-link"
          onClick={onFocus}
          data-testid={`company-thesis-focus-${thesis.thesisId}`}
        >
          {focused ? "取消聚焦" : "聚焦"}
        </button>
      </div>
      <p style={{ fontSize: 12, margin: "4px 0" }}>{thesis.statement}</p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <StatusBadge
          variant={
            thesis.aiConclusion
              ? AI_VARIANT[thesis.aiConclusion] ?? "ai"
              : "ai"
          }
        >
          AI · {thesis.aiConclusion ? AI_LABEL[thesis.aiConclusion] : "未评估"}
          {thesis.aiProvisional ? " · 草案" : ""}
        </StatusBadge>
        {thesis.reviewOutcome ? (
          <StatusBadge
            variant={REVIEW_VARIANT[thesis.reviewOutcome] ?? "reviewed"}
          >
            人工 · {REVIEW_LABEL[thesis.reviewOutcome] ?? thesis.reviewOutcome}
            {thesis.reviewConclusion
              ? ` · ${thesis.reviewConclusion}`
              : ""}
          </StatusBadge>
        ) : (
          <StatusBadge variant="ai">人工 · 待复核</StatusBadge>
        )}
        {thesis.reviewer && (
          <span className="muted" style={{ fontSize: 11 }}>
            复核人 {thesis.reviewer}
          </span>
        )}
        {thesis.assessedAt && (
          <span className="muted" style={{ fontSize: 11 }}>
            评估 {thesis.assessedAt.slice(0, 10)}
          </span>
        )}
      </div>
      {focused && thesis.reviewReason && (
        <p
          style={{ fontSize: 12, marginTop: 6, color: "var(--ink-soft)" }}
          data-testid={`company-thesis-reason-${thesis.thesisId}`}
        >
          人工复核理由：{thesis.reviewReason}
        </p>
      )}
    </li>
  );
}

function ValuationRow({ valuation }: { valuation: CompanyValuationView }) {
  return (
    <tr>
      <td>{valuation.stockCode}</td>
      <td>{valuation.metricName}</td>
      <td>{valuation.metricValue.toLocaleString()}</td>
      <td>{valuation.asOfDate}</td>
      <td>
        <span className="muted" style={{ fontSize: 11 }}>
          {valuation.source} · {valuation.definition}
        </span>
      </td>
    </tr>
  );
}

function FundHolderRow({ holder }: { holder: CompanyFundHolderView }) {
  return (
    <tr>
      <td>
        {holder.fundName}
        <br />
        <span className="muted" style={{ fontSize: 11 }}>
          {holder.fundCode}
        </span>
      </td>
      <td>{holder.stockCode}</td>
      <td>{holder.weight.toFixed(2)}%</td>
      <td>{holder.reportPeriod}</td>
      <td>
        {holder.publishedAt ? holder.publishedAt.slice(0, 10) : "—"}
        <br />
        <span className="muted" style={{ fontSize: 11 }}>
          采集 {holder.acquiredAt ? holder.acquiredAt.slice(0, 10) : "—"}
        </span>
      </td>
      <td>
        <span className="muted" style={{ fontSize: 11 }}>
          {holder.source}
        </span>
      </td>
    </tr>
  );
}

function MetaCell({
  label,
  value,
  mono = false,
  warn = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <div className="theme-meta-grid__cell">
      <dt>{label}</dt>
      <dd
        style={{
          fontFamily: mono ? "var(--font-mono, monospace)" : undefined,
          color: warn ? "var(--warning)" : undefined,
        }}
      >
        {value}
      </dd>
    </div>
  );
}
