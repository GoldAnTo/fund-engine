import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import type { GatewayConversationClient, GatewayConversationSummary, GatewaySession } from "@/gateway/contracts";
import type {
  CompanyStudyClient,
  CompanyStudy,
  StudyDetail,
  StudyKind,
  StudyMarket,
  StudyActivity,
  ConfigureStudyMonitor,
} from "@/gateway/companyStudy";
import { GatewayHttpError, GatewaySchemaError } from "@/gateway/HttpGatewayClient";
import { preparePendingSubmission, clearPendingSubmission, type PendingOperation } from "@/gateway/pendingIdempotency";
import "./CompanyStudyWorkspace.css";

type Client = GatewayConversationClient & CompanyStudyClient;
type Loaded = {
  session: GatewaySession;
  studies: CompanyStudy[];
  detail: StudyDetail | null;
  conversations: GatewayConversationSummary[];
};
const kindLabels: Record<StudyActivity["kind"], string> = {
  baseline: "公司研究基线",
  event: "事件调查",
  material: "资料研究",
  refresh: "资料更新",
  linked: "已关联研究",
};
const statusLabels: Record<StudyActivity["status"], string> = {
  queued: "等待执行",
  starting: "正在启动",
  running: "研究进行中",
  completed: "待采用",
  failed: "执行失败",
  blocked: "需要处理",
};
const marketLabels: Record<StudyMarket, string> = { CN: "中国内地", HK: "中国香港", US: "美国", other: "其他市场" };
function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function denied(error: unknown) {
  return error instanceof GatewayHttpError && [401, 403, 404].includes(error.status);
}

/** One scoped read at a time. Routine updates retain the visible dossier. */
function useStudies(client: Client, studyId?: string) {
  const scope = studyId ?? "index";
  const [stored, setStored] = useState<{ scope: string; client: Client; data: Loaded } | null>(null);
  const [error, setError] = useState("");
  const schedule = useRef<() => void>(() => undefined);
  const redacted = useRef(false),
    authorizationEpoch = useRef(0);
  const clear = useCallback((message?: string) => {
    redacted.current = true;
    authorizationEpoch.current += 1;
    setStored(null);
    setError(message ?? "研究访问已失效或档案不存在，已清除私有内容。");
  }, []);
  const refresh = useCallback(() => schedule.current(), []);
  useEffect(() => {
    let active = true,
      inFlight = false,
      queued = false,
      stopped = false;
    let timer: number | undefined;
    const controller = new AbortController();
    redacted.current = false;
    authorizationEpoch.current += 1;
    setError("");
    setStored(null);
    async function read() {
      if (!active || stopped || redacted.current || document.hidden) return;
      if (inFlight) {
        queued = true;
        return;
      }
      inFlight = true;
      window.clearTimeout(timer);
      const epoch = authorizationEpoch.current;
      try {
        const [session, studies, conversations, detail] = await Promise.all([
          client.getSession(controller.signal),
          client.listStudies(controller.signal),
          client.listConversations(controller.signal),
          studyId ? client.getStudy(studyId, controller.signal) : Promise.resolve(null),
        ]);
        if (!active || controller.signal.aborted || redacted.current || epoch !== authorizationEpoch.current) return;
        if (!session.subjectId) {
          stopped = true;
          setStored({ scope, client, data: { session, studies: [], detail: null, conversations: [] } });
          setError("本地服务尚未配置稳定研究主体，请检查本地服务的身份配置，然后更新列表。");
          return;
        }
        setStored({ scope, client, data: { session, studies, detail, conversations: conversations.conversations } });
        setError("");
      } catch (e) {
        if (!active || controller.signal.aborted) return;
        if (denied(e) || e instanceof GatewaySchemaError) {
          stopped = true;
          clear(
            e instanceof GatewayHttpError && [401, 403].includes(e.status)
              ? "本地研究身份暂不可用，请检查本地服务的身份配置。研究访问已失效，已清除私有内容。"
              : undefined,
          );
        } else setError("本地研究服务暂不可用，请检查服务是否启动及身份配置。已有内容会保留，可更新列表重试。");
      } finally {
        inFlight = false;
        if (active && !stopped) timer = window.setTimeout(read, queued ? 0 : 15000);
        queued = false;
      }
    }
    schedule.current = () => {
      stopped = false;
      redacted.current = false;
      authorizationEpoch.current += 1;
      void read();
    };
    const visible = () => {
      if (!document.hidden) void read();
    };
    document.addEventListener("visibilitychange", visible);
    void read();
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
      schedule.current = () => undefined;
    };
  }, [client, studyId, scope, clear]);
  return {
    data: !redacted.current && stored?.scope === scope && stored.client === client ? stored.data : null,
    error,
    refresh,
    clear,
  };
}

function useStudySubmission(
  session: GatewaySession | null,
  studyId: string | undefined,
  onSaved: () => void,
  onDenied: () => void,
) {
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const fence = useRef(false),
    active = useRef(true),
    controller = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      controller.current?.abort();
    };
  }, []);
  async function submit<T>(
    operation: PendingOperation,
    body: object,
    send: (key: string, signal: AbortSignal, expectedRevision?: number) => Promise<T>,
  ): Promise<T | undefined> {
    if (fence.current || !session?.subjectId) return;
    fence.current = true;
    setBusy(true);
    setNotice("");
    const current = new AbortController();
    controller.current = current;
    let storageKey: string | undefined;
    try {
      const { expected_revision, ...intent } = body as Record<string, unknown>;
      const prepared = await preparePendingSubmission({
        principal: session,
        scope: { type: "company", studyId: studyId ?? "new" },
        operation,
        body: operation === "company.revision" ? intent : body,
        expectedRevision: operation === "company.revision" ? (expected_revision as number) : undefined,
        signal: current.signal,
      });
      storageKey = prepared.storageKey;
      if (prepared.notice && active.current) setNotice(prepared.notice);
      const result = await send(prepared.idempotencyKey, current.signal, prepared.expectedRevision ?? undefined);
      if (!active.current || current.signal.aborted) return;
      clearPendingSubmission(prepared.storageKey);
      setNotice("已保存。");
      onSaved();
      return result;
    } catch (e) {
      if (!active.current || current.signal.aborted) return;
      if (denied(e)) {
        onDenied();
        setNotice("访问已失效，未继续显示私有内容。");
      } else if (e instanceof GatewayHttpError && e.status === 422) {
        if (storageKey) clearPendingSubmission(storageKey);
        setNotice("当前内容或研究状态不满足要求。请检查输入；采用版本需要本轮研究及四角色全部完成。");
      } else if (
        e instanceof GatewayHttpError &&
        e.status === 409 &&
        operation === "company.revision" &&
        e.code === "company_study_revision_changed"
      ) {
        if (storageKey) clearPendingSubmission(storageKey);
        setNotice("版本已变化，本次采用未保存。请更新列表并核对当前版本，再重新提交。");
      } else if (e instanceof GatewayHttpError && e.status === 409)
        setNotice("版本已变化或同一请求仍在处理。请核对最新记录；重试会沿用原提交标识。");
      else setNotice("暂未确认是否保存成功。请先核对记录；重新提交相同内容将沿用原提交标识。");
    } finally {
      fence.current = false;
      if (active.current) setBusy(false);
    }
  }
  return { busy, notice, submit };
}

export function CompanyStudyWorkspace({ client, studyId }: { client: Client; studyId?: string }) {
  const read = useStudies(client, studyId);
  const clientScope = useRef({ client, revision: 0 });
  if (clientScope.current.client !== client)
    clientScope.current = { client, revision: clientScope.current.revision + 1 };
  const session = read.data?.session;
  const pageScope = JSON.stringify([
    studyId ?? "new",
    clientScope.current.revision,
    session?.tenantId,
    session?.subjectId,
    [...(session?.roles ?? [])].sort(),
  ]);
  return <StudyPage key={pageScope} client={client} studyId={studyId} {...read} />;
}

function StudyPage({
  client,
  studyId,
  data,
  error,
  refresh,
  clear,
}: {
  client: Client;
  studyId?: string;
  data: Loaded | null;
  error: string;
  refresh: () => void;
  clear: () => void;
}) {
  const navigate = useNavigate();
  const [navOpen, setNavOpen] = useState(false),
    [tab, setTab] = useState<"overview" | "activity" | "monitor">("overview");
  const [kind, setKind] = useState<StudyKind>("baseline"),
    [text, setText] = useState("");
  const [linkId, setLinkId] = useState(""),
    [revisionActivity, setRevisionActivity] = useState(""),
    [note, setNote] = useState("");
  const [name, setName] = useState(""),
    [symbol, setSymbol] = useState(""),
    [market, setMarket] = useState<StudyMarket>("CN"),
    [focus, setFocus] = useState("");
  const [monitorEditing, setMonitorEditing] = useState(false),
    [monitorDraft, setMonitorDraft] = useState<ConfigureStudyMonitor>({
      status: "active",
      frequency: "daily",
      focus: "",
    });
  const mutation = useStudySubmission(data?.session ?? null, studyId, refresh, clear);
  const detail = data?.detail,
    study = detail?.study;
  const newestRevision = detail?.revisions.slice().sort((a, b) => b.version - a.version)[0];
  const openActivity = (next: StudyKind) => {
    setKind(next);
    setTab("activity");
  };
  const submitActivity = async (e: FormEvent) => {
    e.preventDefault();
    if (!studyId || !text.trim()) return;
    const result = await mutation.submit("company.activity", { kind, text }, (key, signal) =>
      client.createStudyActivity(studyId, { kind, text }, key, signal),
    );
    if (result) setText("");
  };
  const activities = detail?.activities.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)) ?? [];
  const completed = activities.filter((a) => a.status === "completed");
  const monitor = detail?.monitor;
  useEffect(() => {
    if (monitor?.status) setMonitorDraft((draft) => ({ ...draft, status: monitor.status }));
  }, [monitor?.status]);
  const toggleMonitor = async () => {
    if (!monitor || !studyId) return;
    const body: ConfigureStudyMonitor = {
      status: monitor.status === "active" ? "paused" : "active",
      frequency: monitor.frequency,
      focus: monitor.focus,
    };
    // Preserve the explicit pause/resume intent even if its response is lost.
    // The visible server status changes only when a fresh read confirms it.
    setMonitorDraft((draft) => ({ ...draft, status: body.status }));
    const saved = await mutation.submit("company.monitor", body, (key, signal) =>
      client.configureStudyMonitor(studyId, body, key, signal),
    );
    if (saved) setMonitorDraft((draft) => ({ ...draft, status: saved.status }));
  };
  const researchLink = (activity: StudyActivity) =>
    `/companies/${studyId}/research/${activity.conversation_id}?run=${activity.run_spec_id}`;
  const startMonitorEdit = () => {
    setMonitorDraft(
      monitor
        ? { status: monitor.status, frequency: monitor.frequency, focus: monitor.focus }
        : { status: "active", frequency: "daily", focus: study?.focus ?? "" },
    );
    setMonitorEditing(true);
  };
  return (
    <div className="company-desk">
      <a className="skip-link" href="#company-content">
        跳到公司研究
      </a>
      <header className="workbench-topbar">
        <Link className="workbench-brand" to="/" aria-label="FundClaw 首页">
          <span>FundClaw</span>
          <i aria-hidden="true" />
          <small>持续公司研究</small>
        </Link>
        <span className="company-identity">
          {data
            ? data.session.subjectId
              ? `研究员：${data.session.subjectId}`
              : "未配置研究主体"
            : error
              ? "本地身份未就绪"
              : "私有研究空间"}
        </span>
      </header>
      <button
        className="company-nav-toggle"
        type="button"
        aria-expanded={navOpen}
        aria-controls="company-navigation"
        onClick={() => setNavOpen((v) => !v)}
      >
        公司研究列表
      </button>
      <nav id="company-navigation" className="company-navigation" data-open={navOpen} aria-label="公司研究列表">
        <header>
          <h2>我的公司研究</h2>
          <Link to="/" onClick={() => setNavOpen(false)}>
            ＋ 新建
          </Link>
        </header>
        <ul>
          {data?.studies.map((item) => (
            <li key={item.id}>
              <NavLink to={`/companies/${item.id}`} onClick={() => setNavOpen(false)}>
                <strong>{item.name}</strong>
                <small>
                  {item.symbol ? `${item.symbol} · ` : ""}
                  {marketLabels[item.market]} · {item.revision ? `版本 ${item.revision}` : "尚未采用版本"}
                </small>
              </NavLink>
            </li>
          ))}
        </ul>
        {data && !data.studies.length ? <p>建立公司档案，集中管理事件、资料和后续研究。</p> : null}
        <footer>
          <Link to="/research/new">独立研究入口 ↗</Link>
          <p>
            研究结果持续积累
            <br />
            事件与资料回到公司主线
          </p>
        </footer>
      </nav>
      <main id="company-content" className="company-content" tabIndex={-1}>
        <header className="company-title">
          <div>
            <p>{study ? "持续公司研究" : "研究案头"}</p>
            <h1>{study?.name ?? "从一家公司，持续研究下去"}</h1>
            {study ? (
              <small>
                {marketLabels[study.market]}
                {study.symbol ? ` · ${study.symbol}` : ""} · 档案标识由研究员提供
              </small>
            ) : null}
          </div>
          <button type="button" onClick={refresh}>
            更新列表
          </button>
        </header>
        {error ? (
          <p className="company-notice" role="alert">
            {error}
          </p>
        ) : null}
        {mutation.notice ? (
          <p className="company-notice" role="status">
            {mutation.notice}
          </p>
        ) : null}
        {!data && !error ? (
          <p role="status" className="company-padded">
            正在读取公司研究…
          </p>
        ) : null}
        {data?.session.subjectId && !studyId ? (
          <div className="company-columns">
            <section className="company-primary company-padded">
              <p className="company-intro">
                把公司研究保存在一个长期档案中。新财报、新事件和补充资料在这里形成研究更新，保留每次判断的依据。
              </p>
              <form
                className="company-form"
                onSubmit={async (e) => {
                  e.preventDefault();
                  const body = { name, symbol: symbol.trim() || null, market, focus };
                  const saved = await mutation.submit("company.create", body, (key, signal) =>
                    client.createStudy(body, key, signal),
                  );
                  if (saved) navigate(`/companies/${saved.id}`);
                }}
              >
                <fieldset disabled={mutation.busy}>
                  <label>
                    公司名称
                    <input required maxLength={256} value={name} onChange={(e) => setName(e.target.value)} />
                  </label>
                  <div className="company-form-row">
                    <label>
                      市场
                      <select value={market} onChange={(e) => setMarket(e.target.value as StudyMarket)}>
                        {Object.entries(marketLabels).map(([v, label]) => (
                          <option key={v} value={v}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      证券代码（可选）
                      <input maxLength={64} value={symbol} onChange={(e) => setSymbol(e.target.value)} />
                    </label>
                  </div>
                  <label>
                    长期研究重点
                    <textarea
                      required
                      maxLength={4000}
                      rows={4}
                      value={focus}
                      placeholder="例如：商业模式、收入质量、资本开支与现金流、关键反证"
                      onChange={(e) => setFocus(e.target.value)}
                    />
                  </label>
                  <p className="company-muted">创建档案后，再选择建立研究基线、关联已有研究或补充材料。</p>
                  <button className="company-primary-button" type="submit">
                    创建公司档案
                  </button>
                </fieldset>
              </form>
            </section>
            <aside className="company-context">
              <h2>持续研究的工作方式</h2>
              <ol className="company-process">
                <li>
                  <strong>建立研究基线</strong>
                  <p>明确公司、重点与资料范围。</p>
                </li>
                <li>
                  <strong>接入事件和新资料</strong>
                  <p>查看新信息如何影响原有判断。</p>
                </li>
                <li>
                  <strong>复核并采用版本</strong>
                  <p>四角色研究结果与证据保留可追溯历史。</p>
                </li>
                <li>
                  <strong>监控后续变化</strong>
                  <p>配置资料更新任务，持续跟进关键问题。</p>
                </li>
              </ol>
            </aside>
          </div>
        ) : null}
        {data && study && detail ? (
          <>
            <div className="company-tabs" role="group" aria-label="公司研究视图">
              {(
                [
                  ["overview", "研究概览"],
                  ["activity", "事件与资料"],
                  ["monitor", "监控与版本"],
                ] as const
              ).map(([value, label]) => (
                <button key={value} type="button" aria-pressed={tab === value} onClick={() => setTab(value)}>
                  {label}
                </button>
              ))}
            </div>
            <div className="company-columns">
              <div className="company-primary company-padded">
                {tab === "overview" ? (
                  <>
                    <section className="company-section">
                      <h2>长期研究重点</h2>
                      <p className="company-focus">{study.focus}</p>
                      <div className="company-actions">
                        <button type="button" onClick={() => openActivity("baseline")}>
                          建立研究基线
                        </button>
                        <button type="button" onClick={() => openActivity("event")}>
                          调查新事件
                        </button>
                        <button type="button" onClick={() => openActivity("material")}>
                          研究补充资料
                        </button>
                        <button type="button" onClick={() => openActivity("refresh")}>
                          更新公司资料
                        </button>
                      </div>
                    </section>
                    <section className="company-section">
                      <h2>当前采用版本</h2>
                      {newestRevision ? (
                        <>
                          <p>
                            版本 {newestRevision.version} · {formatDate(newestRevision.created_at)}
                          </p>
                          <p>{newestRevision.note}</p>
                          <Link
                            to={`/companies/${studyId}/research/${newestRevision.conversation_id}?run=${newestRevision.run_spec_id}&teamRevision=${newestRevision.team_revision}`}
                          >
                            查看采用的研究与证据 ↗
                          </Link>
                        </>
                      ) : (
                        <p className="company-muted">
                          尚未采用研究版本。研究完成后，在“监控与版本”中核对并采用；已有结论不会被后台更新自动覆盖。
                        </p>
                      )}
                    </section>
                    <h2 className="company-list-heading">最近研究活动</h2>
                    <ActivityList
                      activities={activities.slice(0, 5)}
                      link={researchLink}
                      onRetry={(a) =>
                        void mutation.submit("company.retry", { activity_id: a.id }, (key, signal) =>
                          client.retryStudyActivity(studyId!, a.id, key, signal),
                        )
                      }
                      busy={mutation.busy}
                    />
                  </>
                ) : null}
                {tab === "activity" ? (
                  <>
                    <form className="company-form company-section" onSubmit={submitActivity}>
                      <fieldset disabled={mutation.busy}>
                        <h2>发起研究活动</h2>
                        <label>
                          活动类型
                          <select value={kind} onChange={(e) => setKind(e.target.value as StudyKind)}>
                            {(["baseline", "event", "material", "refresh"] as const).map((k) => (
                              <option key={k} value={k}>
                                {kindLabels[k]}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          本次研究内容
                          <textarea
                            required
                            rows={5}
                            maxLength={12000}
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                            placeholder={
                              kind === "material"
                                ? "粘贴待核验的资料，注明来源、发布日期和需要研究的问题。"
                                : kind === "event"
                                  ? "描述新事件，说明需要核验的事实及其对公司原有判断的影响。"
                                  : "说明本次需要分析或更新的经营、财务、策略与风险问题。"
                            }
                          />
                        </label>
                        <p className="company-muted">
                          任务保存后由后台执行，可离开页面；实际资料范围与证据准入结果在研究工作台查看。
                        </p>
                        <button className="company-primary-button" type="submit" disabled={!text.trim()}>
                          提交研究任务
                        </button>
                      </fieldset>
                    </form>
                    <details className="company-section">
                      <summary>关联已有研究</summary>
                      <form
                        className="company-form"
                        onSubmit={async (e) => {
                          e.preventDefault();
                          if (!linkId) return;
                          const saved = await mutation.submit(
                            "company.link",
                            { conversation_id: linkId },
                            (key, signal) =>
                              client.linkStudyConversation(studyId!, { conversation_id: linkId }, key, signal),
                          );
                          if (saved) setLinkId("");
                        }}
                      >
                        <fieldset disabled={mutation.busy}>
                          <label>
                            已保存研究
                            <select required value={linkId} onChange={(e) => setLinkId(e.target.value)}>
                              <option value="">选择一项可访问的研究</option>
                              {data.conversations
                                .filter((c) => !activities.some((a) => a.conversation_id === c.conversationId))
                                .map((c) => (
                                  <option value={c.conversationId} key={c.conversationId}>
                                    {c.title ?? "未命名研究"}
                                  </option>
                                ))}
                            </select>
                          </label>
                          <button type="submit" disabled={!linkId}>
                            关联到公司档案
                          </button>
                        </fieldset>
                      </form>
                    </details>
                    <h2 className="company-list-heading">事件、资料与研究记录</h2>
                    <ActivityList
                      activities={activities}
                      link={researchLink}
                      busy={mutation.busy}
                      onRetry={(a) =>
                        void mutation.submit("company.retry", { activity_id: a.id }, (key, signal) =>
                          client.retryStudyActivity(studyId!, a.id, key, signal),
                        )
                      }
                    />
                  </>
                ) : null}
                {tab === "monitor" ? (
                  <>
                    <section className="company-section">
                      <h2>持续监控</h2>
                      <p>{monitor ? (monitor.status === "active" ? "监控已开启" : "监控已暂停") : "尚未开启监控"}</p>
                      {monitor ? (
                        <>
                          <p>{monitor.focus}</p>
                          <p className="company-muted">
                            {monitor.frequency === "daily" ? "每日 20:00" : "每周一 20:00"} · 北京时间 · 配置版本{" "}
                            {monitor.version}
                            {monitor.next_due_at ? ` · 下次 ${formatDate(monitor.next_due_at)}` : ""}
                          </p>
                        </>
                      ) : null}
                      <p className="company-muted">
                        由服务端按配置发起资料更新，产生待复核的研究活动，不会自动替换当前采用版本。
                      </p>
                      <button type="button" onClick={startMonitorEdit}>
                        {monitor ? "调整监控" : "配置监控"}
                      </button>
                      {monitor ? (
                        <button type="button" disabled={mutation.busy} onClick={() => void toggleMonitor()}>
                          {monitor.status === "active" ? "暂停监控" : "恢复监控"}
                        </button>
                      ) : null}
                      {monitorEditing ? (
                        <form
                          className="company-form"
                          onSubmit={async (e) => {
                            e.preventDefault();
                            const saved = await mutation.submit("company.monitor", monitorDraft, (key, signal) =>
                              client.configureStudyMonitor(studyId!, monitorDraft, key, signal),
                            );
                            if (saved) setMonitorEditing(false);
                          }}
                        >
                          <fieldset disabled={mutation.busy}>
                            <label>
                              检查频率
                              <select
                                value={monitorDraft.frequency}
                                onChange={(e) =>
                                  setMonitorDraft((v) => ({ ...v, frequency: e.target.value as "daily" | "weekly" }))
                                }
                              >
                                <option value="daily">每日 20:00</option>
                                <option value="weekly">每周一 20:00</option>
                              </select>
                            </label>
                            <label>
                              监控重点
                              <textarea
                                required
                                rows={3}
                                maxLength={4000}
                                value={monitorDraft.focus}
                                onChange={(e) => setMonitorDraft((v) => ({ ...v, focus: e.target.value }))}
                              />
                            </label>
                            <button className="company-primary-button" type="submit">
                              {monitor ? "保存监控配置" : "保存并开启监控"}
                            </button>
                            <button type="button" onClick={() => setMonitorEditing(false)}>
                              收起
                            </button>
                          </fieldset>
                        </form>
                      ) : null}
                    </section>
                    <section className="company-section">
                      <h2>采用新的研究版本</h2>
                      <p className="company-muted">
                        选择原生研究和四角色都已完成的活动，记录采用理由。已有人工复核结果保持原样。
                      </p>
                      <form
                        className="company-form"
                        onSubmit={async (e) => {
                          e.preventDefault();
                          const body = { activity_id: revisionActivity, expected_revision: study.revision, note };
                          const saved = await mutation.submit(
                            "company.revision",
                            body,
                            (key, signal, originalRevision) =>
                              client.adoptStudyRevision(
                                studyId!,
                                { ...body, expected_revision: originalRevision ?? body.expected_revision },
                                key,
                                signal,
                              ),
                          );
                          if (saved) {
                            setNote("");
                            setRevisionActivity("");
                          }
                        }}
                      >
                        <fieldset disabled={mutation.busy || !completed.length}>
                          <label>
                            完成的研究活动
                            <select
                              required
                              value={revisionActivity}
                              onChange={(e) => setRevisionActivity(e.target.value)}
                            >
                              <option value="">{completed.length ? "选择研究活动" : "暂无已完成的活动"}</option>
                              {completed.map((a) => (
                                <option key={a.id} value={a.id}>
                                  {a.title}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            采用理由
                            <textarea
                              required
                              maxLength={4000}
                              rows={3}
                              value={note}
                              onChange={(e) => setNote(e.target.value)}
                            />
                          </label>
                          <button type="submit" disabled={!revisionActivity || !note.trim()}>
                            采用为当前版本
                          </button>
                        </fieldset>
                      </form>
                    </section>
                    <h2 className="company-list-heading">不可变版本记录</h2>
                    {detail.revisions.length ? (
                      <ol className="company-revisions">
                        {detail.revisions
                          .slice()
                          .sort((a, b) => b.version - a.version)
                          .map((r) => (
                            <li key={r.id}>
                              <strong>版本 {r.version}</strong>
                              <time>{formatDate(r.created_at)}</time>
                              <p>{r.note}</p>
                              <Link
                                to={`/companies/${studyId}/research/${r.conversation_id}?run=${r.run_spec_id}&teamRevision=${r.team_revision}`}
                              >
                                查看当时的研究 ↗
                              </Link>
                            </li>
                          ))}
                      </ol>
                    ) : (
                      <p className="company-muted">采用版本后，将在这里保留当时的研究和理由。</p>
                    )}
                  </>
                ) : null}
              </div>
              <aside className="company-context">
                <h2>研究脉络</h2>
                <p>
                  {activities.length} 项研究活动 · {detail.revisions.length} 个已采用版本
                </p>
                <dl>
                  <dt>当前版本</dt>
                  <dd>{study.revision ? `版本 ${study.revision}` : "尚未采用"}</dd>
                  <dt>监控</dt>
                  <dd>{monitor?.status === "active" ? "按配置运行" : monitor ? "已暂停" : "未开启"}</dd>
                </dl>
                <h3>四角色协作</h3>
                <p>产业、财务、策略与 AI 质控围绕本次问题工作；任务、来源与原文在研究工作台联动。</p>
                <h3>资料边界</h3>
                <p>档案标识不代替已验证的公司身份。资料不足、主体不匹配或来源不支持时，保留缺口，不生成替代事实。</p>
                {study.market !== "CN" ? (
                  <p className="company-coverage">
                    海外市场来源覆盖仍需逐案核验。当前默认采集适配器主要面向境内资料，可能无法获得足够证据。
                  </p>
                ) : null}
              </aside>
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}

function ActivityList({
  activities,
  link,
  onRetry,
  busy,
}: {
  activities: StudyActivity[];
  link: (a: StudyActivity) => string;
  onRetry: (a: StudyActivity) => void;
  busy: boolean;
}) {
  return activities.length ? (
    <ol className="company-activities">
      {activities.map((a) => (
        <li key={a.id}>
          <div className="company-activity-meta">
            <span>{kindLabels[a.kind]}</span>
            <time>{formatDate(a.created_at)}</time>
            <span data-status={a.status}>{statusLabels[a.status]}</span>
          </div>
          <h3>{a.title}</h3>
          {a.text !== a.title ? <p>{a.text}</p> : null}
          <div className="company-actions">
            {a.conversation_id ? <Link to={link(a)}>进入四角色研究 ↗</Link> : null}
            {(a.status === "failed" || a.status === "blocked") && !a.conversation_id ? (
              <button type="button" disabled={busy} onClick={() => onRetry(a)}>
                重试启动
              </button>
            ) : null}
          </div>
          {a.status === "failed" ? (
            <p className="company-muted">
              {a.conversation_id ? "研究执行失败，请进入工作台核对失败阶段。" : "启动未完成，已保留输入与原提交标识。"}
            </p>
          ) : null}
          {a.status === "blocked" ? (
            <p className="company-muted">研究存在缺口或未完成任务，请进入工作台查看实际状态。</p>
          ) : null}
        </li>
      ))}
    </ol>
  ) : (
    <p className="company-empty">这里还没有研究活动。先建立基线，或把已有研究关联进来。</p>
  );
}

export function CompanyResearchContext({ studyId, children }: { studyId: string; children: ReactNode }) {
  return (
    <div className="company-research-route">
      <div className="company-return">
        <Link to={`/companies/${studyId}`}>← 返回公司研究档案</Link>
        <span>本次研究的事件、资料与版本保留在公司主线中</span>
      </div>
      {children}
    </div>
  );
}
