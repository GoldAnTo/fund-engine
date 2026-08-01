import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  ThemeClaim,
  ThemeFund,
  ThemeHypothesis,
  ThemeHypothesisLink,
  ThemeStock,
  ThemeWorkbenchView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const HYPOTHESIS_VARIANT: Record<ThemeHypothesis["status"], string> = {
  validated: "is-validated",
  contested: "is-contested",
  unverified: "is-unverified",
};

const HYPOTHESIS_LABEL: Record<ThemeHypothesis["status"], string> = {
  validated: "已佐证",
  contested: "有矛盾",
  unverified: "未确认",
};

const SENTIMENT_LABEL: Record<ThemeClaim["sentiment"], string> = {
  positive: "正",
  negative: "反",
  neutral: "中性",
};

const SENTIMENT_VARIANT: Record<ThemeClaim["sentiment"], "support" | "contradict" | "draft"> = {
  positive: "support",
  negative: "contradict",
  neutral: "draft",
};

export function ThemeWorkbenchScreen() {
  const params = useParams<{ themeId?: string }>();
  const themeId = params.themeId ?? "ai-compute";
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<ThemeWorkbenchView | null>(null);
  const [sentimentFilter, setSentimentFilter] = useState<
    "all" | ThemeClaim["sentiment"]
  >("all");
  const [selectedHypothesisId, setSelectedHypothesisId] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getThemeWorkbenchView(themeId)
      .then((v) => {
        if (!cancelled) {
          setView(v);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [themeId]);

  const filteredClaims = useMemo<ThemeClaim[]>(() => {
    if (!view) return [];
    return view.claims.filter((c) => {
      if (sentimentFilter !== "all" && c.sentiment !== sentimentFilter)
        return false;
      if (
        selectedHypothesisId &&
        !c.hypothesisIds.includes(selectedHypothesisId)
      ) {
        return false;
      }
      return true;
    });
  }, [view, sentimentFilter, selectedHypothesisId]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="theme-workbench-loading">
        <p>正在加载主题工作台…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="theme-workbench-error">
        <div className="form-error">
          主题工作台加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div
      className="prototype-screen theme-workbench"
      data-testid="theme-workbench-screen"
    >
      <PageHeader
        title={view.name}
        eyebrow={`${view.industry} · 主题驱动`}
        lede={`认知假设：${view.hypothesis}`}
        meta={
          <dl className="theme-meta-grid">
            <MetaCell label="证据截止" value={view.cutoff} />
            <MetaCell label="快照" value={view.snapshotId} mono />
            <MetaCell label="状态" value={view.statusLabel} />
            <MetaCell
              label="矛盾对"
              value={`${view.conflictCount} ⚠`}
              warn={view.conflictCount > 0}
            />
          </dl>
        }
      />

      <div className="theme-workbench__columns">
        {/* 左：认知假设验证（设计文档 §6 左列） */}
        <section className="theme-col theme-col--hypothesis">
          <div className="theme-col__head">
            <p className="section-kicker">认知假设验证</p>
            <h2>命题树</h2>
          </div>
          <ul className="hypothesis-tree">
            {view.hypothesisLinks.map((link) => (
              <HypothesisNode
                key={link.hypothesis.id}
                link={link}
                selectedId={selectedHypothesisId}
                onSelect={setSelectedHypothesisId}
              />
            ))}
          </ul>

          <div className="theme-col__head theme-col__head--sub">
            <p className="section-kicker">过滤器</p>
          </div>
          <div className="sentiment-filter">
            {(["all", "positive", "negative", "neutral"] as const).map((s) => (
              <button
                key={s}
                type="button"
                className={`filter-chip${sentimentFilter === s ? " is-active" : ""}`}
                onClick={() => setSentimentFilter(s)}
              >
                {s === "all" ? "全部" : SENTIMENT_LABEL[s]}
              </button>
            ))}
          </div>
        </section>

        {/* 中：证据流（设计文档 §6 中列） */}
        <section className="theme-col theme-col--claims">
          <div className="theme-col__head">
            <p className="section-kicker">证据流 · 可溯源</p>
            <h2>{filteredClaims.length} 条证据</h2>
            <span className="muted">{view.conflictCount} 对矛盾</span>
          </div>
          <ul className="claim-stream">
            {filteredClaims.map((claim) => (
              <ClaimRow
                key={claim.id}
                claim={claim}
                allClaims={view.claims}
              />
            ))}
            {filteredClaims.length === 0 && (
              <li className="claim-stream__empty">
                没有符合当前过滤条件的证据。
              </li>
            )}
          </ul>
        </section>

        {/* 右：穿透结果（设计文档 §6 右列） */}
        <section className="theme-col theme-col--penetration">
          <div className="theme-col__head">
            <p className="section-kicker">穿透结果</p>
            <h2>关联标的 · 估值</h2>
          </div>

          <section className="penetration-section">
            <h3>关联股票</h3>
            <ul className="stock-list">
              {view.stocks.map((stock) => (
                <StockRow key={stock.code} stock={stock} />
              ))}
            </ul>
          </section>

          <section className="penetration-section">
            <h3>命中基金 · Top {view.funds.length}</h3>
            <ul className="fund-list">
              {view.funds.map((fund) => (
                <FundRow key={fund.code} fund={fund} />
              ))}
            </ul>
          </section>

          <section className="penetration-section">
            <h3>产业链</h3>
            <ul className="chain-list">
              {view.chain.map((c) => (
                <li key={c.code} data-side={c.side}>
                  <span className="chain-tag">
                    {c.side === "upstream"
                      ? "上游"
                      : c.side === "downstream"
                        ? "下游"
                        : "竞争"}
                  </span>
                  <strong>{c.name}</strong>
                  <code>{c.code}</code>
                </li>
              ))}
            </ul>
          </section>
        </section>
      </div>
    </div>
  );
}

function MetaCell({
  label,
  value,
  mono,
  warn,
}: {
  label: string;
  value: string;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={`meta-cell${warn ? " meta-cell--warn" : ""}`}>
      <span>{label}</span>
      <strong style={mono ? { fontFamily: "ui-monospace, monospace" } : undefined}>
        {value}
      </strong>
    </div>
  );
}

function HypothesisNode({
  link,
  selectedId,
  onSelect,
}: {
  link: ThemeHypothesisLink;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const { hypothesis, claims } = link;
  const isActive = selectedId === hypothesis.id;
  return (
    <li
      className={`hypothesis-node ${HYPOTHESIS_VARIANT[hypothesis.status]}${isActive ? " is-active" : ""}`}
    >
      <button
        type="button"
        className="hypothesis-node__head"
        onClick={() => onSelect(isActive ? "" : hypothesis.id)}
        aria-expanded={isActive}
      >
        <span
          className={`checkbox${hypothesis.status === "validated" ? " is-checked" : ""}`}
          aria-hidden
        >
          {hypothesis.status === "validated" ? "✓" : " "}
        </span>
        <span className="hypothesis-node__label">{hypothesis.label}</span>
        <span className="hypothesis-node__badge">
          {HYPOTHESIS_LABEL[hypothesis.status]}
        </span>
      </button>
      <div className="hypothesis-node__counts">
        <span className="count-support">
          {hypothesis.supportCount} 佐证
        </span>
        <span
          className={
            hypothesis.contradictCount > 0 ? "count-contradict" : "muted"
          }
        >
          {hypothesis.contradictCount} 反证
        </span>
      </div>
      {isActive && claims.length > 0 && (
        <ul className="hypothesis-node__claims">
          {claims.map((c) => (
            <li key={c.id}>
              <StatusBadge variant={SENTIMENT_VARIANT[c.sentiment]}>
                {SENTIMENT_LABEL[c.sentiment]}
              </StatusBadge>
              {c.content}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function ClaimRow({
  claim,
  allClaims,
}: {
  claim: ThemeClaim;
  allClaims: ThemeClaim[];
}) {
  const conflictClaims = (claim.conflictsWith ?? [])
    .map((id) => allClaims.find((c) => c.id === id))
    .filter((c): c is ThemeClaim => Boolean(c));
  return (
    <li className={`claim-row sentiment-${claim.sentiment}`}>
      <div className="claim-row__head">
        <span className={`pill ${SENTIMENT_VARIANT[claim.sentiment]}`}>
          {SENTIMENT_LABEL[claim.sentiment]}
        </span>
        <strong>{claim.content}</strong>
        <span className="claim-row__conf">
          conf{" "}
          <strong
            style={{
              fontFamily: "ui-monospace, monospace",
              color: claim.confidence >= 0.85
                ? "var(--support)"
                : claim.confidence >= 0.7
                  ? "var(--warning)"
                  : "var(--contradict)",
            }}
          >
            {claim.confidence > 0 ? claim.confidence.toFixed(2) : "—"}
          </strong>
        </span>
      </div>
      <div className="claim-row__meta">
        <span className="muted">
          {claim.documentType} · {claim.sourceLabel} · {claim.publishedAt}
        </span>
        {claim.isAiProposed && (
          <StatusBadge variant="ai">AI 提议</StatusBadge>
        )}
        <button type="button" className="link-button">
          ↗ 溯源
        </button>
      </div>
      <blockquote className="claim-row__snippet">
        “{claim.snippet}”
        <small> · {claim.span}</small>
      </blockquote>
      {conflictClaims.length > 0 && (
        <div className="claim-row__conflict">
          <strong>⚠ 与以下证据冲突：</strong>
          <ul>
            {conflictClaims.map((c) => (
              <li key={c.id}>
                <StatusBadge variant={SENTIMENT_VARIANT[c.sentiment]}>
                  {SENTIMENT_LABEL[c.sentiment]}
                </StatusBadge>
                {c.content}
                <small className="muted">（{c.sourceLabel}）</small>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function StockRow({ stock }: { stock: ThemeStock }) {
  const valuationColor =
    stock.pe > 0 && stock.pe < 30
      ? "var(--support)"
      : stock.pe > 0 && stock.pe < 80
        ? "var(--warning)"
        : "var(--contradict)";
  return (
    <li className="stock-row">
      <div>
        <strong>{stock.name}</strong>
        <small>
          <code>{stock.code}</code> · {stock.industry}
        </small>
      </div>
      <dl>
        <div>
          <dt>PE</dt>
          <dd style={{ color: valuationColor }}>
            {stock.pe > 0 ? stock.pe.toFixed(1) : "—"}
          </dd>
        </div>
        <div>
          <dt>PB</dt>
          <dd>{stock.pb > 0 ? stock.pb.toFixed(1) : "—"}</dd>
        </div>
        <div>
          <dt>ROE</dt>
          <dd>{stock.roe !== 0 ? `${stock.roe.toFixed(1)}%` : "—"}</dd>
        </div>
        <div>
          <dt>暴露度</dt>
          <dd>{(stock.exposure * 100).toFixed(0)}%</dd>
        </div>
      </dl>
    </li>
  );
}

function FundRow({ fund }: { fund: ThemeFund }) {
  return (
    <li className="fund-row">
      <div>
        <strong>{fund.name}</strong>
        <small>
          <code>{fund.code}</code> · 规模 {fund.scale}
        </small>
      </div>
      <div className="fund-row__exposure">
        <strong style={{ fontFamily: "ui-monospace, monospace" }}>
          {(fund.themeExposure * 100).toFixed(1)}%
        </strong>
        <small>主题暴露</small>
      </div>
      <ul className="fund-row__holdings">
        {fund.topHoldings.map((h) => (
          <li key={h.code}>
            <code>{h.code}</code> {h.name} ·{" "}
            <strong>{(h.weight * 100).toFixed(1)}%</strong>
          </li>
        ))}
      </ul>
    </li>
  );
}