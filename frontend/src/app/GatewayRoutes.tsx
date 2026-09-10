import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactElement,
} from "react";
import {
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import { GatewayHttpError } from "@/gateway/HttpGatewayClient";
import type {
  GatewayConversationClient,
  GatewayConversationList,
  GatewayRejectedIntentReceipt,
  GatewaySession,
} from "@/gateway/contracts";
import { clearPendingSubmission, hasPendingSubmissions, preparePendingSubmission } from "@/gateway/pendingIdempotency";
import { useGatewayConversation } from "@/gateway/useGatewayConversation";
import { GatewayConversationPanel } from "@/workbench/GatewayConversationPanel";
import { ProfessionalTeamWorkspace } from "@/workbench/ProfessionalTeamWorkspace";
import { hasCompanyStudyClient } from "@/gateway/companyStudy";
import { CompanyStudyWorkspace, CompanyResearchContext } from "@/workbench/CompanyStudyWorkspace";

const unsupportedNotice = "该请求暂不受 Gateway P0 支持，未启动新的研究任务。";
const maxGatewayInputLength = 20_000;

type BootstrapState =
  | { kind: "loading" }
  | {
    kind: "ready";
    session: GatewaySession;
    conversations: GatewayConversationList;
  }
  | { kind: "auth_required" }
  | { kind: "unavailable" }
  | { kind: "access_revoked" };

export type GatewayRoutesProps = {
  client: GatewayConversationClient;
};

function isRejectedIntent(
  receipt: Awaited<ReturnType<GatewayConversationClient["startConversation"]>>,
): receipt is GatewayRejectedIntentReceipt {
  return "receiptKind" in receipt;
}

function isUnauthorized(error: unknown): boolean {
  return (
    error instanceof GatewayHttpError &&
    (error.status === 401 || error.status === 403)
  );
}

function noticeFromLocationState(value: unknown): string {
  if (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).gatewayNotice === unsupportedNotice
  ) {
    return unsupportedNotice;
  }
  return "";
}

function StartResearchForm({
  client,
  onAuthorizationLost,
  onConversationChanged,
  session,
}: {
  client: GatewayConversationClient;
  onAuthorizationLost: () => void;
  onConversationChanged: () => void;
  session: GatewaySession;
}): ReactElement {
  const navigate = useNavigate();
  const requestController = useRef<AbortController | null>(null);
  const requestToken = useRef(1);
  const requestEffectMounted = useRef(false);
  const submitting = useRef(false);
  const [draft, setDraft] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const sessionKey = JSON.stringify({ tenantId: session.tenantId, subjectId: session.subjectId, roles: [...session.roles].sort() });

  useEffect(() => {
    if (requestEffectMounted.current) {
      requestController.current?.abort();
      requestController.current = null;
      submitting.current = false;
      setIsSubmitting(false);
    }
    requestEffectMounted.current = true;
    const token = requestToken.current;
    return () => {
      if (requestToken.current === token) requestToken.current += 1;
      requestController.current?.abort();
      requestController.current = null;
      submitting.current = false;
    };
  }, [sessionKey]);

  useEffect(() => {
    let live = true;
    void hasPendingSubmissions({ principal: session, scope: { type: "start" }, operations: ["start"] })
      .then((check) => { if (live && check.notice) setError(check.notice); });
    return () => { live = false; };
  }, [session]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const text = draft.trim();
    if (!text || submitting.current) return;
    if (text.length > maxGatewayInputLength) {
      setError("研究请求最多 20,000 字；当前内容已保留，请删减后提交。");
      return;
    }
    const controller = new AbortController();
    requestController.current?.abort();
    requestController.current = controller;
    const token = requestToken.current;
    const isCurrent = () => requestToken.current === token && requestController.current === controller && !controller.signal.aborted;
    submitting.current = true;
    setIsSubmitting(true);
    setError("");
    let preparedStorageKey = "";
    let preparedNotice = "";

    try {
      const prepared = await preparePendingSubmission({ principal: session, scope: { type: "start" }, operation: "start", body: { initialMessage: text }, signal: controller.signal });
      preparedStorageKey = prepared.storageKey;
      preparedNotice = prepared.notice;
      if (!isCurrent()) return;
      if (prepared.notice) setError(prepared.notice);
      const receipt = await client.startConversation({
        initialMessage: text,
        idempotencyKey: prepared.idempotencyKey,
        signal: controller.signal,
      });
      if (!isCurrent()) return;
      await clearPendingSubmission(prepared.storageKey);
      if (!isCurrent()) return;
      // A receipt confirms that the Gateway has made an authoritative
      // decision. Refresh the server-owned list rather than adding a local
      // optimistic conversation row.
      onConversationChanged();
      if (isRejectedIntent(receipt)) {
        navigate(`/research/${receipt.conversationId}`, {
          state: { gatewayNotice: unsupportedNotice },
        });
        return;
      }
      navigate(`/research/${receipt.conversationId}`);
    } catch (cause) {
      if (isCurrent()) {
        if (cause instanceof GatewayHttpError && [400, 401, 403, 404, 422].includes(cause.status)) {
          if (preparedStorageKey) await clearPendingSubmission(preparedStorageKey);
          if (!isCurrent()) return;
          if (isUnauthorized(cause)) {
            onAuthorizationLost();
            return;
          }
          setError("请求未通过服务端校验，未执行本次操作。请核对内容与字符限制后重新提交。");
          return;
        }
        // This deliberately covers a 503 as well: a network or gateway error
        // does not prove whether the idempotent request reached the server.
        setError(`无法确认本次提交是否已送达。待确认提交标识已保留；刷新不会自动重发。请核对已确认请求历史，若仍需重试，请重新输入相同请求。${preparedNotice ? ` ${preparedNotice}` : ""}`);
      }
    } finally {
      if (requestToken.current === token && requestController.current === controller) {
        submitting.current = false;
        requestController.current = null;
        setIsSubmitting(false);
      }
    }
  }

  return (
    <form className="gateway-start-form" onSubmit={submit}>
      <label htmlFor="gateway-initial-message">研究请求</label>
      <textarea
        id="gateway-initial-message"
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault(); event.currentTarget.form?.requestSubmit();
          }
        }}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="描述需要研究的问题、范围或待核验的变化…"
        rows={4}
        value={draft}
      />
      {error ? <p role="alert">{error}</p> : null}
      <button type="submit" disabled={!draft.trim() || isSubmitting}>
        {isSubmitting ? "正在创建研究…" : "发起研究"}
      </button>
    </form>
  );
}

function BootstrapNotice({
  state,
  onRetry,
}: {
  state: BootstrapState;
  onRetry: () => void;
}): ReactElement | null {
  if (state.kind === "loading" || state.kind === "ready") return null;
  if (state.kind === "access_revoked") {
    return <p className="gateway-banner" role="alert">访问已撤销，已清除当前私有研究内容。</p>;
  }
  if (state.kind === "auth_required") {
    return (
      <div className="gateway-banner" role="alert">
        <p>本地研究身份暂不可用。请检查本地服务的身份配置后重试。</p>
        <button type="button" onClick={onRetry}>重新确认身份</button>
      </div>
    );
  }
  return (
    <div className="gateway-banner" role="alert">
      <p>暂时无法连接本地研究服务。请检查服务启动状态和身份配置后重试。</p>
      <button type="button" onClick={onRetry}>重新读取</button>
    </div>
  );
}

function GatewayShell({
  state,
  children,
}: {
  state: BootstrapState;
  children: ReactElement;
}): ReactElement {
  const location = useLocation();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const navigationToggle = useRef<HTMLButtonElement>(null);
  const closeNavigation = () => {
    if (navigationOpen) {
      setNavigationOpen(false);
      navigationToggle.current?.focus();
    }
  };
  const lastPath = useRef(location.pathname);
  useEffect(() => {
    if (lastPath.current !== location.pathname) {
      lastPath.current = location.pathname;
      if (navigationOpen) {
        setNavigationOpen(false);
        navigationToggle.current?.focus();
      }
    }
  }, [location.pathname, navigationOpen]);
  const session = state.kind === "ready" ? state.session : null;
  const conversations = state.kind === "ready" ? state.conversations.conversations : [];
  const redacted =
    state.kind === "access_revoked" || state.kind === "auth_required";
  const identityLabel = session
    ? session.subjectId ? `研究员：${session.subjectId}` : "未配置研究主体"
    : state.kind === "loading" ? "正在确认身份" : "本地身份未就绪";

  return (
    <div className="workbench gateway-workbench">
      <a className="skip-link" href="#workspace">跳到研究内容</a>
      <header className="workbench-topbar">
        <Link className="workbench-brand" to="/" aria-label="FundClaw 首页">
          <span>FundClaw</span><i aria-hidden="true" /><small>投资研究 Agent Gateway</small>
        </Link>
        <p className="gateway-topbar-copy">私有研究空间</p>
        <div className="workbench-utilities" aria-label="当前研究身份">
          <span className="workbench-profile">
            {identityLabel}
          </span>
        </div>
      </header>

      <button className="gateway-navigation-toggle" type="button" ref={navigationToggle}
        aria-expanded={navigationOpen} aria-controls="gateway-research-navigation"
        onClick={() => setNavigationOpen((open) => !open)}>研究列表</button>
      <nav className="workbench-sidebar" id="gateway-research-navigation" data-expanded={navigationOpen}
        aria-label="研究空间" onClick={(event) => {
          if ((event.target as HTMLElement).closest(".gateway-key-questions button")) setNavigationOpen(false);
        }} onKeyDown={(event) => {
          if (event.key === "Escape" && navigationOpen) {
            setNavigationOpen(false);
            navigationToggle.current?.focus();
          }
        }}>
        <header className="sidebar-heading">
          <h2>研究空间</h2>
          <Link className="gateway-new-link" to="/research/new" onClick={closeNavigation}>＋ 新建</Link>
        </header>
        <div className="sidebar-section-heading">
          <h3>我的研究</h3>
          <span>{redacted ? "—" : conversations.length}</span>
        </div>
        {redacted ? null : (
          <ul className="thread-list">
            {conversations.map((conversation) => (
              <li key={conversation.conversationId}>
                <NavLink to={`/research/${conversation.conversationId}`} onClick={closeNavigation}>
                  <strong>{conversation.title ?? "未命名研究对话"}</strong>
                  <small>已保存 · 四角色协作</small>
                </NavLink>
              </li>
            ))}
          </ul>
        )}
        <div id="gateway-key-questions">{location.pathname === "/" ? <section className="gateway-key-questions"><h3>关键问题（0）</h3><p>发起研究后，跟进各角色的证据缺口与待解决问题。</p></section> : null}</div>
        <p className="gateway-sidebar-footer">四位固定投研角色<br />研究判断与人工审核分别记录</p>
      </nav>

      <main className="workbench-main gateway-workbench-main" id="workspace" tabIndex={-1}>
        {children}
      </main>

    </div>
  );
}

function EmptyWorkspace({
  client,
  state,
  onRetry,
  onAuthorizationLost,
  onConversationChanged,
}: {
  client: GatewayConversationClient;
  state: BootstrapState;
  onRetry: () => void;
  onAuthorizationLost: () => void;
  onConversationChanged: () => void;
}): ReactElement {
  const ready = state.kind === "ready";
  const canStart = ready && state.session.subjectId !== null;

  return (
    <section className="gateway-empty-workspace gateway-empty-workspace--team gateway-conversation--team" aria-labelledby="gateway-start-heading">
      <header className="gateway-team-titlebar"><div className="gateway-team-titleline"><h1 id="gateway-start-heading">发起新的研究</h1><span className="gateway-status">未开始</span></div></header>
      {state.kind === "loading" ? <p className="gateway-banner" role="status">正在确认研究身份…</p> : null}
      <BootstrapNotice state={state} onRetry={onRetry} />
      {ready && state.session.subjectId === null ? <div className="gateway-banner" role="alert">
        <p>本地服务尚未配置稳定研究主体。请完成服务端身份配置后重试。</p>
        <button type="button" onClick={onRetry}>重新确认身份</button>
      </div> : null}
      <ProfessionalTeamWorkspace
        scope={<div className="gateway-team-scope" aria-label="研究范围"><div><strong>研究范围（待建立）</strong></div><div className="gateway-scope-chips"><span>主题与材料范围：等待解析</span></div></div>}
        composer={canStart ? <StartResearchForm client={client} onAuthorizationLost={onAuthorizationLost} onConversationChanged={onConversationChanged} session={state.session} /> : <p className="gateway-empty-copy">确认研究身份后，可在这里提交问题。</p>}
        evidence={<div className="gateway-empty-evidence"><h3>暂无本轮证据</h3><p>发起研究后，已准入的来源和原文会显示在这里。</p>{ready && state.conversations.conversations.length === 0 ? <p>还没有可访问的研究对话。</p> : null}</div>}
        nativeContent={<p>提交研究问题后，可查看资料采集与执行过程。</p>}
        messageHistory={<p>尚无已保存的研究请求。</p>}
      />
    </section>
  );
}

function RedactedWorkspace({
  state,
  onRetry,
}: {
  state: Extract<BootstrapState, { kind: "auth_required" | "access_revoked" }>;
  onRetry: () => void;
}): ReactElement {
  return (
    <section className="gateway-private-state" aria-label="研究访问状态">
      <BootstrapNotice state={state} onRetry={onRetry} />
    </section>
  );
}

function LiveWorkspace({
  client,
  conversationId,
  onAccessRevoked,
  onAuthorizationLost,
  onConversationChanged,
  session,
}: {
  client: GatewayConversationClient;
  conversationId: string;
  onAccessRevoked: () => void;
  onAuthorizationLost: () => void;
  onConversationChanged: () => void;
  session: GatewaySession;
}): ReactElement {
  const location = useLocation();
  const [notice, setNotice] = useState(() => noticeFromLocationState(location.state));
  const conversation = useGatewayConversation({
    client,
    conversationId,
    onAccessRevoked,
    onAuthorizationRequired: onAuthorizationLost,
  });

  useEffect(() => {
    const nextNotice = noticeFromLocationState(location.state);
    if (nextNotice) setNotice(nextNotice);
  }, [location.state]);

  return (
    <>
      {notice ? <p className="gateway-banner" role="alert">{notice}</p> : null}
      <GatewayConversationPanel
        client={client}
        connection={conversation.connection}
        issue={conversation.issue}
        onAuthorizationLost={onAuthorizationLost}
        onConversationChanged={onConversationChanged}
        onNotice={setNotice}
        onRefresh={conversation.retry}
        session={session}
        snapshot={conversation.snapshot}
        teamProgressByRun={conversation.teamProgressByRun}
        initialRunSpecId={new URLSearchParams(location.search).get('run') ?? undefined}
        initialTeamRevision={Number(new URLSearchParams(location.search).get('teamRevision')) || undefined}
      />
    </>
  );
}

function GatewayWorkspace({ client }: GatewayRoutesProps): ReactElement {
  const { conversationId } = useParams();
  const location = useLocation();
  const [state, setState] = useState<BootstrapState>({ kind: "loading" });
  const [reloadVersion, setReloadVersion] = useState(0);
  const bootstrapControllerRef = useRef<AbortController | null>(null);
  const redactedRef = useRef(false);

  const loseAuthorization = useCallback(() => {
    redactedRef.current = true;
    bootstrapControllerRef.current?.abort();
    setState({ kind: "auth_required" });
  }, []);

  const revokeAccess = useCallback(() => {
    redactedRef.current = true;
    bootstrapControllerRef.current?.abort();
    setState({ kind: "access_revoked" });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    bootstrapControllerRef.current?.abort();
    bootstrapControllerRef.current = controller;
    redactedRef.current = false;
    setState({ kind: "loading" });
    void Promise.all([
      client.getSession(controller.signal),
      client.listConversations(controller.signal),
    ])
      .then(([session, conversations]) => {
        if (!controller.signal.aborted && !redactedRef.current) {
          setState({ kind: "ready", session, conversations });
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || redactedRef.current) return;
        if (isUnauthorized(error)) {
          loseAuthorization();
          return;
        }
        setState({ kind: "unavailable" });
      });
    return () => {
      controller.abort();
      if (bootstrapControllerRef.current === controller) {
        bootstrapControllerRef.current = null;
      }
    };
  }, [client, loseAuthorization, reloadVersion]);

  const retryBootstrap = useCallback((): void => {
    setReloadVersion((version) => version + 1);
  }, []);
  const content = state.kind === "auth_required" || state.kind === "access_revoked"
    ? <RedactedWorkspace state={state} onRetry={retryBootstrap} />
    : conversationId && state.kind === "ready"
    ? (
      <LiveWorkspace
        key={`${conversationId}:${location.search}`}
        client={client}
        conversationId={conversationId}
        onAccessRevoked={revokeAccess}
        onAuthorizationLost={loseAuthorization}
        onConversationChanged={retryBootstrap}
        session={state.session}
      />
    )
    : conversationId
    ? (
      <section className="gateway-private-state" aria-label="研究访问状态">
        {state.kind === "loading" ? <p role="status">正在确认研究身份…</p> : null}
        <BootstrapNotice state={state} onRetry={retryBootstrap} />
      </section>
    )
    : (
      <EmptyWorkspace
        client={client}
        onAuthorizationLost={loseAuthorization}
        onConversationChanged={retryBootstrap}
        onRetry={retryBootstrap}
        state={state}
      />
    );

  return (
    <GatewayShell state={state}>
      {content}
    </GatewayShell>
  );
}

export function GatewayRoutes({ client }: GatewayRoutesProps): ReactElement {
  return (
    <Routes>
      <Route
        path="/"
        element={hasCompanyStudyClient(client)?<CompanyStudyWorkspace client={client}/>:<GatewayWorkspace client={client} />}
      />
      <Route path="/research/new" element={<NewIndependentWorkspace client={client}/>}/>
      <Route path="/companies/:studyId" element={<CompanyRoute client={client}/>}/>
      <Route path="/companies/:studyId/research/:conversationId" element={<CompanyResearchRoute client={client}/>}/>
      <Route
        path="/research/:conversationId"
        element={<GatewayWorkspace client={client} />}
      />
    </Routes>
  );
}

function CompanyRoute({client}:GatewayRoutesProps){const {studyId}=useParams();return hasCompanyStudyClient(client)?<CompanyStudyWorkspace client={client} studyId={studyId}/>:<GatewayWorkspace client={client}/>;}
function CompanyResearchRoute({client}:GatewayRoutesProps){const {studyId}=useParams();return <CompanyResearchContext studyId={studyId!}><GatewayWorkspace client={client}/></CompanyResearchContext>;}
function NewIndependentWorkspace({client}:GatewayRoutesProps){return <GatewayWorkspace client={client}/>;}
