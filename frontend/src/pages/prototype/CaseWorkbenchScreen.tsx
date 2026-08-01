import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { CaseWorkbenchView } from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const TABS = [
  "研究摘要",
  "关键图表",
  "核心观点",
  "风险与假设",
  "相关公司",
  "研究日志",
];

const RELATION_LABEL: Record<string, string> = {
  support: "支持",
  contradict: "反驳",
  gap: "缺口",
};

export function CaseWorkbenchScreen() {
  const params = useParams<{ caseId?: string }>();
  const caseId = params.caseId ?? "RC-AIC-2025-01";
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<CaseWorkbenchView | null>(null);
  const [tab, setTab] = useState(0);
  const [selectedCaseId, setSelectedCaseId] = useState<string>(caseId);

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getCaseWorkbenchView(selectedCaseId)
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
  }, [selectedCaseId]);

  const caseList = useMemo(() => {
    return [
      { id: selectedCaseId, title: view?.case.title ?? "城市 NOA 商业化落地路径" },
      { id: "RC-ALT-2024-12", title: "L4 Robotaxi 本体架构拆解" },
      { id: "RC-ALT-2024-11", title: "高算力芯片供应链高频演变" },
      { id: "RC-ALT-2024-10", title: "激光雷达车规级测算" },
      { id: "RC-ALT-2024-09", title: "L3 法规模地对车业影响" },
      { id: "RC-ALT-2024-08", title: "智能驾驶数据闭环价值分析" },
      { id: "RC-ALT-2024-07", title: "高精地图商业化进展" },
    ];
  }, [view, selectedCaseId]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="case-workbench-loading">
        <p>正在加载研究案例…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="case-workbench-error">
        <div className="form-error">
          研究案例加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen case-workbench-screen" data-testid="case-workbench-screen">
      <div className="case-workbench-layout">
        <aside className="case-list" aria-label="行业案例列表">
          <div className="case-list__head">
            <span>行业案例</span>
            <button type="button" className="link-button">＋</button>
          </div>
          <input
            type="search"
            placeholder="搜索案例标题或关键词"
            className="case-list__search"
          />
          <div className="case-list__filters">
            <button type="button" className="filter-pill is-active">全部 32</button>
            <button type="button" className="filter-pill">我创建的 8</button>
            <button type="button" className="filter-pill">已草稿 5</button>
          </div>
          <ul className="case-list__items">
            {caseList.map((c) => (
              <li
                key={c.id}
                className={`case-list__item${c.id === selectedCaseId ? " is-active" : ""}`}
                onClick={() => setSelectedCaseId(c.id)}
                role="button"
                tabIndex={0}
              >
                <strong>{c.title}</strong>
                <small>
                  智能驾驶 ·{" "}
                  {c.id === selectedCaseId
                    ? "2024-05-20"
                    : "2024-04-15"}
                </small>
              </li>
            ))}
          </ul>
        </aside>

        <main className="case-main">
          <nav className="breadcrumb" aria-label="面包屑">
            <Link to="/workspace">行业研究</Link>
            <span>/</span>
            <span>汽车产业链</span>
            <span>/</span>
            <span>智能驾驶</span>
            <span>/</span>
            <span>行业案例</span>
            <span>/</span>
            <strong>{view.case.title}</strong>
          </nav>

          <header className="case-main__head">
            <div>
              <div className="eyebrow">智能驾驶</div>
              <h1>{view.case.title}</h1>
              <div className="case-meta-row">
                <small>作者：张子仪</small>
                <small>创建时间：{view.case.researchPeriod}</small>
                <small>最后更新：2024-05-20</small>
                <span className="state-badge ai">AI 撰写</span>
              </div>
            </div>
            <div className="case-main__actions">
              <button type="button" className="prototype-button">↩ 返回</button>
              <button type="button" className="prototype-button">☆ 收藏</button>
              <button type="button" className="prototype-button">↗ 导出</button>
              <button type="button" className="prototype-button primary">↓ 分享</button>
            </div>
          </header>

          <nav className="prototype-stepper" aria-label="案例档案选项卡">
            {view.tabs.map((t, i) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(i)}
                data-step-state={i === tab ? "current" : "upcoming"}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span>{TABS[i] ?? t}</span>
                <strong>{t}</strong>
              </button>
            ))}
          </nav>

          <section className="prototype-paper">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">当前判断</p>
                <h2>正式结论</h2>
              </div>
              <span className="state-badge warning">未人工复核</span>
            </div>
            <p style={{ fontSize: 14, fontWeight: 600 }}>
              2025 年是城市 NOA 商业化落地的关键拐点。头部车企通过轻地图方案 +
              规模化数据闭环实现全国可用，并在 2026 年进入规模化交付期。
            </p>
            <ul className="case-bullets">
              <li>政策层面逐步明确合规路径，试点城市扩容推动路权开放；</li>
              <li>技术层面纯视觉 + 端到端模型成为主流方案，主流算法已经走过纯视觉阶段；</li>
              <li>商业层面头部玩家通过车型矩阵与订阅模式降低渗透门槛，用户接受度快速提升。</li>
            </ul>
            <details>
              <summary>展开链路</summary>
              <div className="case-chain-row">
                <span>政策与路权</span>
                <span>→</span>
                <span>技术方案收敛</span>
                <span>→</span>
                <span>成本结构优化</span>
                <span>→</span>
                <span>产业与商业模式</span>
                <span>→</span>
                <span>规模化落地</span>
              </div>
            </details>
          </section>

          <section>
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">支持证据 · 12</p>
                <h2>支撑结论的来源</h2>
              </div>
              <button type="button" className="link-button">仅相关 ▾</button>
              <button type="button" className="link-button">全部来源 ▾</button>
            </div>
            <ul className="case-evidence-list">
              <li>
                <span className="state-badge reviewed">已审核</span>
                <div>
                  <strong>工信部：开展智能网联汽车准入试点</strong>
                  <small>支持3.3 · 来源：工信部 2025-04-12</small>
                </div>
                <span className="confidence">0.92</span>
              </li>
              <li>
                <span className="state-badge reviewed">已审核</span>
                <div>
                  <strong>北京新增智能网联汽车开放道路</strong>
                  <small>0.87 · 来源：北京交管局 2025-04-08</small>
                </div>
                <span className="confidence">0.87</span>
              </li>
              <li>
                <span className="state-badge reviewed">已审核</span>
                <div>
                  <strong>小鹏 XOS 5.2.0 全国推送城市 NOA</strong>
                  <small>0.85 · 来源：小鹏汽车 2025-04-30</small>
                </div>
                <span className="confidence">0.85</span>
              </li>
            </ul>
            <button type="button" className="link-button">查看全部支持证据 (12)</button>
          </section>

          <section>
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">反证 · 5</p>
                <h2>可能反驳当前判断</h2>
              </div>
              <button type="button" className="link-button">仅相关 ▾</button>
              <button type="button" className="link-button">全部来源 ▾</button>
            </div>
            <ul className="case-evidence-list case-evidence-list--counter">
              <li>
                <span className="state-badge contradict">反驳</span>
                <div>
                  <strong>特斯拉 FSD 入华未获批</strong>
                  <small>0.78 · 来源：路透社 2025-05-10</small>
                </div>
                <span className="confidence">0.78</span>
              </li>
              <li>
                <span className="state-badge contradict">反驳</span>
                <div>
                  <strong>用户对 NOA 接管频次存疑虑</strong>
                  <small>0.73 · 来源：汽车之家 2025-05-05</small>
                </div>
                <span className="confidence">0.73</span>
              </li>
              <li>
                <span className="state-badge contradict">反驳</span>
                <div>
                  <strong>高精地图审批流程仍较慢</strong>
                  <small>0.71 · 来源：高德地图 2025-04-25</small>
                </div>
                <span className="confidence">0.71</span>
              </li>
            </ul>
            <button type="button" className="link-button">查看全部反证 (5)</button>
          </section>
        </main>

        <aside className="case-pin" aria-label="原文定位面板">
          <header>
            <strong>原文摘要（已定位）</strong>
            <span className="state-badge warning">未人工复核</span>
          </header>
          <div className="case-pin__source">
            <span className="state-badge reviewed">未人工 · 定位</span>
            <strong>工信部 · 装备工业一司</strong>
          </div>
          <p className="case-pin__excerpt">
            支持 L3 级及以上自动驾驶功能的智能网联汽车产品开展准入试点，在指定区域和条件下上路通行，推动自动驾驶技术产业化应用，提升智能网联汽车产业性能和安全水平。
          </p>
          <p className="case-pin__source-line">—— 工信部 · 装备工业一司</p>

          <section className="case-pin__position">
            <h3>定位信息</h3>
            <dl>
              <div><dt>章节</dt><dd>三、工作要求</dd></div>
              <div><dt>段落</dt><dd>第2段</dd></div>
              <div><dt>位置</dt><dd>1426 – 1056 / 2678</dd></div>
            </dl>
            <button type="button" className="link-button">↗ 在原文中查看</button>
          </section>

          <section className="case-pin__evidence">
            <h3>估值信息</h3>
            <dl>
              <div><dt>证据 ID</dt><dd>EV-20240515-0007</dd></div>
              <div><dt>证据类型</dt><dd>政策指导</dd></div>
              <div><dt>相关性</dt><dd><span className="state-badge support">0.92</span></dd></div>
              <div><dt>加入时间</dt><dd>2025-06-15 11:02</dd></div>
              <div><dt>加入者</dt><dd>张子仪</dd></div>
              <div><dt>备注</dt><dd>关键政策拐点</dd></div>
            </dl>
          </section>

          <section className="case-pin__review">
            <h3>复核状态</h3>
            <span className="state-badge warning">未人工复核</span>
            <p>此证据尚未经过人工复核。</p>
            <button type="button" className="link-button">发起审核</button>
          </section>
        </aside>
      </div>
    </div>
  );
}