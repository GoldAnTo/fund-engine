import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  UnderwritingResearchRequestError,
  underwritingResearchApi,
  type ResearchArchiveItem,
  type ResearchArchiveList,
  type ResearchRevision,
  type ResearchRevisionDiff,
  type ResearchRevisionHistory,
} from "../../data/underwritingResearchApi";

type LoadState<T> =
  | { state: "loading"; value?: T }
  | { state: "ready"; value: T }
  | { state: "error"; error: unknown };

type ArchiveKind = "" | ResearchArchiveItem["object_kind"];
type Artifact = ResearchRevisionDiff["entries"][number]["after"] extends infer Value
  ? Exclude<Value, null>
  : never;

const GROUPS = [
  ["evidence", "证据"],
  ["mechanism", "机制"],
  ["industry_model", "行业模型"],
  ["answerability", "可回答性"],
] as const;

function errorCopy(error: unknown, subject: "directory" | "detail"): string {
  if (error instanceof UnderwritingResearchRequestError) {
    if (error.status === 404 && subject === "detail") return "找不到该冻结版本档案。";
    if (error.status === 422) return "查询条件无法读取，请修改后重试。";
  }
  return subject === "directory"
    ? "冻结档案目录暂时无法读取，未展示替代资料。"
    : "这个冻结版本暂时无法读取，未展示替代资料。";
}

function labelForStatus(status: string | null): string | null {
  if (status === "not_answerable") return "研究尚需验证";
  if (status === "wait_for_validation") return "等待验证资料";
  if (status === "unknown_evidence_gap") return "Unknown evidence gap";
  return status;
}

function formatPeriod(start: string | null, end: string | null): string {
  if (!start && !end) return "未提供";
  if (!start) return `截至 ${end}`;
  if (!end) return `自 ${start}`;
  return `${start} 至 ${end}`;
}

function ArtifactDetails({ artifact }: { artifact: Artifact }) {
  const status = labelForStatus(artifact.status);
  return (
    <dl className="ura-artifact-details">
      <div><dt>参考</dt><dd>{artifact.reference}</dd></div>
      <div><dt>标识</dt><dd>{artifact.identity}</dd></div>
      <div><dt>定位</dt><dd>{artifact.source_locators.length ? artifact.source_locators.join("；") : "未提供"}</dd></div>
      <div><dt>单位</dt><dd>{artifact.unit ?? "未提供"}</dd></div>
      <div><dt>期间</dt><dd>{formatPeriod(artifact.period_start, artifact.period_end)}</dd></div>
      <div><dt>可用时间</dt><dd>{artifact.available_at ?? "未提供"}</dd></div>
      <div><dt>状态</dt><dd>{status ?? "未提供"}</dd></div>
    </dl>
  );
}

function EvidenceTable({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null;
  return (
    <section className="ura-evidence" aria-label="冻结证据记录">
      <h2>冻结证据记录</h2>
      <div className="ura-evidence-scroll">
        <table>
          <thead><tr><th>参考</th><th>定位</th><th>单位</th><th>期间</th><th>可用时间</th><th>状态</th></tr></thead>
          <tbody>{artifacts.map((artifact) => (
            <tr key={`${artifact.artifact_type}:${artifact.identity}:${artifact.content_hash}`}>
              <td>{artifact.reference}</td>
              <td>{artifact.source_locators.length ? artifact.source_locators.join("；") : "未提供"}</td>
              <td>{artifact.unit ?? "未提供"}</td>
              <td>{formatPeriod(artifact.period_start, artifact.period_end)}</td>
              <td>{artifact.available_at ?? "未提供"}</td>
              <td>{labelForStatus(artifact.status) ?? "未提供"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

function DirectoryPage() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<ArchiveKind>("");
  const [archiveState, setArchiveState] = useState<LoadState<ResearchArchiveList>>({ state: "loading" });

  useEffect(() => {
    let live = true;
    setArchiveState({ state: "loading" });
    void underwritingResearchApi.listArchives({ query: query.trim() || undefined, kind: kind === "" ? undefined : kind })
      .then((value) => { if (live) setArchiveState({ state: "ready", value }); })
      .catch((error: unknown) => { if (live) setArchiveState({ state: "error", error }); });
    return () => { live = false; };
  }, [query, kind]);

  async function loadMore() {
    if (archiveState.state !== "ready") return;
    const previous = archiveState.value;
    const cursor = previous.next_cursor;
    if (!cursor) return;
    setArchiveState({ state: "loading", value: previous });
    try {
      const next = await underwritingResearchApi.listArchives({
        query: query.trim() || undefined,
        kind: kind === "" ? undefined : kind,
        cursor,
      });
      setArchiveState({ state: "ready", value: { ...next, items: [...previous.items, ...next.items] } });
    } catch (error) {
      setArchiveState({ state: "error", error });
    }
  }

  const archives: ResearchArchiveItem[] = archiveState.state === "error"
    ? []
    : archiveState.value?.items ?? [];
  return (
    <main className="ros-page ura-page">
      <header className="ros-page-head ura-page-head">
        <div>
          <p className="ros-eyebrow">研究资产 · Immutable archive</p>
          <h1>公司／行业档案</h1>
          <p>从冻结版本、原始定位与证据缺口进入研究，不以新的资料替代历史记录。</p>
        </div>
      </header>
      <section className="ura-directory" aria-label="档案目录">
        <div className="ura-filters">
          <label><span>检索</span><input type="search" aria-label="搜索公司或行业档案" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称或代码" /></label>
          <label><span>对象</span><select aria-label="研究对象类型" value={kind} onChange={(event) => setKind(event.target.value as ArchiveKind)}><option value="">全部</option><option value="company">公司</option><option value="industry">行业</option><option value="security">证券</option></select></label>
        </div>
        {archiveState.state === "loading" && !archiveState.value && <p className="ura-loading" role="status">正在读取冻结档案目录</p>}
        {archiveState.state === "error" && <p className="ura-alert" role="alert">{errorCopy(archiveState.error, "directory")}</p>}
        {archiveState.state === "ready" && archives.length === 0 && <p className="ura-empty" role="status">没有符合条件的冻结档案。</p>}
        {archives.length > 0 && <div className="ura-directory-list">{archives.map((item) => item.lineage_state === "readable" ? (
          <Link className="ura-archive-row" key={`${item.object_id}:${item.version_kind}`} to={`/underwriting/research/${encodeURIComponent(item.object_id)}/${encodeURIComponent(item.version_kind)}`} aria-label={`打开${item.canonical_name}档案`}>
            <span className="ura-archive-row__title"><b>{item.canonical_name}</b><small>{item.external_key}</small></span><span>{item.object_kind}</span><span>版本 {item.version_count}</span><span>序列 {item.latest_sequence ?? "未提供"}</span><span>{item.cutoff ?? "未提供"}</span>
          </Link>
        ) : (
          <article className="ura-archive-row ura-archive-row--unreadable" key={`${item.object_id}:${item.version_kind}`}>
            <span className="ura-archive-row__title"><b>{item.canonical_name}</b><small>{item.external_key}</small></span><span>{item.object_kind}</span><span>版本 {item.version_count}</span><span className="ura-unreadable-copy">该档案无法校验，未展示版本资料。</span>
          </article>
        ))}</div>}
        {archiveState.state === "ready" && archiveState.value.next_cursor && <button className="ura-load-more" type="button" onClick={() => void loadMore()}>载入后续档案</button>}
      </section>
    </main>
  );
}

function DetailPage({ objectId, versionKind }: { objectId: string; versionKind: string }) {
  const [historyState, setHistoryState] = useState<LoadState<ResearchRevisionHistory>>({ state: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [revisionState, setRevisionState] = useState<LoadState<ResearchRevision> | null>(null);
  const [diffState, setDiffState] = useState<LoadState<ResearchRevisionDiff> | null>(null);

  useEffect(() => {
    let live = true;
    setHistoryState({ state: "loading" });
    setSelectedId(null); setRevisionState(null); setDiffState(null);
    void underwritingResearchApi.history(objectId, versionKind).then((value) => {
      if (!live) return;
      setHistoryState({ state: "ready", value });
      const sorted = [...value.revisions].sort((left, right) => left.sequence - right.sequence);
      const latest = sorted[sorted.length - 1];
      setSelectedId(latest?.id ?? null);
    }).catch((error: unknown) => { if (live) setHistoryState({ state: "error", error }); });
    return () => { live = false; };
  }, [objectId, versionKind]);

  const revisions = useMemo(() => historyState.state === "ready" ? [...historyState.value.revisions].sort((left, right) => left.sequence - right.sequence) : [], [historyState]);
  const selectedIndex = revisions.findIndex((revision) => revision.id === selectedId);
  const selected = selectedIndex >= 0 ? revisions[selectedIndex] : null;
  const previous = selectedIndex > 0 ? revisions[selectedIndex - 1] : null;

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setRevisionState({ state: "loading" });
    setDiffState(previous ? { state: "loading" } : null);
    void underwritingResearchApi.revision(selected.id).then((value) => { if (live) setRevisionState({ state: "ready", value }); }).catch((error: unknown) => { if (live) setRevisionState({ state: "error", error }); });
    if (previous) void underwritingResearchApi.diff(previous.id, selected.id).then((value) => { if (live) setDiffState({ state: "ready", value }); }).catch((error: unknown) => { if (live) setDiffState({ state: "error", error }); });
    return () => { live = false; };
  }, [selected?.id, previous?.id]);

  if (historyState.state === "loading") return <main className="ros-page ura-page"><p className="ura-loading" role="status">正在读取冻结版本档案</p></main>;
  if (historyState.state === "error") return <main className="ros-page ura-page"><p className="ura-alert" role="alert">{errorCopy(historyState.error, "detail")}</p></main>;
  if (!revisions.length || !selected) return <main className="ros-page ura-page"><p className="ura-empty" role="status">该档案没有可读取的冻结版本。</p></main>;

  const changed = diffState?.state === "ready" ? diffState.value.entries : [];
  const artifacts = [
    ...(revisionState?.state === "ready" ? revisionState.value.parent_refs : []),
    ...changed.flatMap((entry) => [entry.after, entry.before].filter((artifact): artifact is Artifact => artifact !== null)),
  ];
  const statuses = [...new Set(artifacts.map((artifact) => labelForStatus(artifact.status)).filter((status): status is string => Boolean(status)))];
  if (
    versionKind === "catl_economic_model_evidence_only"
    && statuses.includes("研究尚需验证")
    && !statuses.includes("等待验证资料")
  ) statuses.unshift("等待验证资料");

  return (
    <main className="ros-page ura-page">
      <header className="ros-page-head ura-page-head"><div><p className="ros-eyebrow">冻结研究档案</p><h1>{versionKind}</h1><p>对象 {objectId}</p></div><Link className="ura-back-link" to="/underwriting/research">返回档案目录</Link></header>
      <div className="ura-layout">
        <aside className="ura-timeline" aria-label="研究版本时间线"><p className="ros-eyebrow">不可变版本</p>{revisions.map((revision) => <button key={revision.id} type="button" aria-pressed={revision.id === selected.id} className={revision.id === selected.id ? "ura-version is-selected" : "ura-version"} onClick={() => setSelectedId(revision.id)}><b>版本 {revision.sequence}</b><span>{revision.cutoff}</span></button>)}</aside>
        <section className="ura-detail" aria-label="已选冻结版本">
          {revisionState?.state === "loading" && <p className="ura-loading" role="status">正在读取已选版本</p>}
          {revisionState?.state === "error" && <p className="ura-alert" role="alert">{errorCopy(revisionState.error, "detail")}</p>}
          {revisionState?.state === "ready" && <section className="ura-version-summary"><h2>版本 {revisionState.value.sequence}</h2><dl><div><dt>截点</dt><dd>{revisionState.value.cutoff}</dd></div><div><dt>来源摘要</dt><dd>{revisionState.value.source_manifest_hash}</dd></div><div><dt>内容摘要</dt><dd>{revisionState.value.content_hash}</dd></div></dl>{statuses.length > 0 && <div className="ura-statuses" aria-label="当前冻结状态">{statuses.map((status) => <span key={status}>{status}</span>)}</div>}</section>}
          {previous ? <p className="ura-predecessor">仅与紧邻的版本 {previous.sequence} 对照。</p> : <p className="ura-predecessor">这是该档案最早的冻结版本，没有前序版本可对照。</p>}
          {diffState?.state === "loading" && <p className="ura-loading" role="status">正在读取相邻版本差异</p>}
          {diffState?.state === "error" && <p className="ura-alert" role="alert">这个相邻版本差异暂时无法读取。</p>}
          {previous && diffState?.state === "ready" && <div className="ura-diff-groups">{GROUPS.map(([group, heading]) => {
            const entries = changed.filter((entry) => entry.group === group);
            return <section className="ura-diff-group" key={group}><h2>{heading}</h2>{entries.length === 0 ? <p>这一版本没有该类冻结变化</p> : entries.map((entry) => <article className="ura-diff-entry" key={`${entry.change_type}:${entry.artifact_type}:${entry.identity}`}><header><b>{entry.change_type}</b><span>{entry.artifact_type}</span></header>{entry.after && <ArtifactDetails artifact={entry.after} />}{!entry.after && entry.before && <ArtifactDetails artifact={entry.before} />}</article>)}</section>;
          })}</div>}
          <EvidenceTable artifacts={artifacts} />
        </section>
      </div>
    </main>
  );
}

export default function ResearchArchivePage() {
  const { objectId, versionKind } = useParams();
  return objectId && versionKind ? <DetailPage objectId={objectId} versionKind={versionKind} /> : <DirectoryPage />;
}
