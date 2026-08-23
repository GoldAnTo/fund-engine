import { useEffect, useMemo, useRef, useState } from "react";
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
type DetailLoad = { revision: ResearchRevision; diff: ResearchRevisionDiff | null };
type DiffEntry = ResearchRevisionDiff["entries"][number];

const GROUPS = [
  ["evidence", "证据"],
  ["mechanism", "机制"],
  ["industry_model", "行业模型"],
  ["answerability", "可回答性"],
] as const;

const INTEGRITY_ERROR = "冻结档案的身份或版本链无法校验，未展示任何资料。";

class ArchiveIntegrityError extends Error {
  constructor() {
    super(INTEGRITY_ERROR);
    this.name = "ArchiveIntegrityError";
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function nullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function isArtifact(value: unknown): value is Artifact {
  const artifact = record(value);
  return artifact?.schema_version === "underwriting.v1"
    && nonEmptyString(artifact.reference)
    && nonEmptyString(artifact.artifact_type)
    && nonEmptyString(artifact.identity)
    && nonEmptyString(artifact.content_hash)
    && Array.isArray(artifact.source_locators)
    && artifact.source_locators.every((locator) => typeof locator === "string")
    && nullableString(artifact.unit)
    && nullableString(artifact.period_start)
    && nullableString(artifact.period_end)
    && nullableString(artifact.available_at)
    && nullableString(artifact.status);
}

function sameArtifact(left: Artifact, right: Artifact): boolean {
  return left.schema_version === right.schema_version
    && left.reference === right.reference
    && left.artifact_type === right.artifact_type
    && left.identity === right.identity
    && left.content_hash === right.content_hash
    && left.source_locators.length === right.source_locators.length
    && left.source_locators.every((locator, index) => locator === right.source_locators[index])
    && left.unit === right.unit
    && left.period_start === right.period_start
    && left.period_end === right.period_end
    && left.available_at === right.available_at
    && left.status === right.status;
}

function artifactIdentity(artifact: Artifact): string {
  return `${artifact.artifact_type}\u0000${artifact.identity}`;
}

function artifactOrder(artifact: Artifact): string {
  return `${artifactIdentity(artifact)}\u0000${artifact.reference}`;
}

function canonicalArtifacts(value: unknown): value is Artifact[] {
  if (!Array.isArray(value)) return false;
  let previousOrder: string | null = null;
  const identities = new Set<string>();
  return value.every((artifact) => {
    if (!isArtifact(artifact)) return false;
    const order = artifactOrder(artifact);
    const identity = artifactIdentity(artifact);
    if ((previousOrder !== null && previousOrder >= order) || identities.has(identity)) return false;
    previousOrder = order;
    identities.add(identity);
    return true;
  });
}

function sameArtifactList(expected: Artifact[], received: Artifact[]): boolean {
  return expected.length === received.length
    && expected.every((artifact, index) => sameArtifact(artifact, received[index]));
}

function isRevision(value: unknown): value is ResearchRevision {
  const revision = record(value);
  return revision?.schema_version === "underwriting.v1"
    && nonEmptyString(revision.id)
    && nonEmptyString(revision.object_id)
    && nonEmptyString(revision.basis_id)
    && nonEmptyString(revision.version_kind)
    && Number.isSafeInteger(revision.sequence)
    && (revision.sequence as number) > 0
    && nonEmptyString(revision.content_hash)
    && nonEmptyString(revision.cutoff)
    && nonEmptyString(revision.source_manifest_hash)
    && canonicalArtifacts(revision.parent_refs);
}

function checkedHistory(value: unknown, objectId: string, versionKind: string): ResearchRevisionHistory {
  const history = record(value);
  if (
    history?.schema_version !== "underwriting.v1"
    || history.object_id !== objectId
    || history.version_kind !== versionKind
    || !["company", "industry", "security"].includes(history.object_kind as string)
    || !nonEmptyString(history.canonical_name)
    || !nonEmptyString(history.external_key)
    || !Array.isArray(history.revisions)
  ) throw new ArchiveIntegrityError();

  const ids = new Set<string>();
  const sequences = new Set<number>();
  for (const revision of history.revisions) {
    if (
      !isRevision(revision)
      || revision.object_id !== objectId
      || revision.version_kind !== versionKind
      || ids.has(revision.id)
      || sequences.has(revision.sequence)
    ) throw new ArchiveIntegrityError();
    ids.add(revision.id);
    sequences.add(revision.sequence);
  }
  const ordered = [...history.revisions].sort((left, right) => left.sequence - right.sequence);
  if (ordered.some((revision, index) => revision.sequence !== index + 1)) throw new ArchiveIntegrityError();
  return { ...history, revisions: ordered } as ResearchRevisionHistory;
}

function labelForObjectKind(kind: ResearchRevisionHistory["object_kind"]): string {
  const labels: Record<ResearchRevisionHistory["object_kind"], string> = {
    company: "公司",
    industry: "行业",
    security: "证券",
  };
  return labels[kind];
}

function sameRevisionSummary(expected: ResearchRevision, received: ResearchRevision): boolean {
  return expected.id === received.id
    && expected.object_id === received.object_id
    && expected.basis_id === received.basis_id
    && expected.version_kind === received.version_kind
    && expected.sequence === received.sequence
    && expected.content_hash === received.content_hash
    && expected.cutoff === received.cutoff
    && expected.source_manifest_hash === received.source_manifest_hash;
}

function checkedRevision(value: unknown, expected: ResearchRevision): ResearchRevision {
  if (
    !isRevision(value)
    || !sameRevisionSummary(expected, value)
    || !sameArtifactList(expected.parent_refs, value.parent_refs)
  ) throw new ArchiveIntegrityError();
  return value;
}

function isDiff(value: unknown): value is ResearchRevisionDiff {
  const diff = record(value);
  return diff?.schema_version === "underwriting.v1"
    && nonEmptyString(diff.from_revision_id)
    && nonEmptyString(diff.to_revision_id)
    && nonEmptyString(diff.from_content_hash)
    && nonEmptyString(diff.to_content_hash)
    && nonEmptyString(diff.diff_hash)
    && Array.isArray(diff.entries)
    && diff.entries.every((entry) => {
      const change = record(entry);
      return change?.schema_version === "underwriting.v1"
        && GROUPS.some(([group]) => change.group === group)
        && ["added", "removed", "replaced"].includes(change.change_type as string)
        && nonEmptyString(change.artifact_type)
        && nonEmptyString(change.identity)
        && (change.before === null || isArtifact(change.before))
        && (change.after === null || isArtifact(change.after));
    });
}

function checkedDiff(value: unknown, previous: ResearchRevision, selected: ResearchRevision): ResearchRevisionDiff {
  if (
    !isDiff(value)
    || value.from_revision_id !== previous.id
    || value.to_revision_id !== selected.id
    || value.from_content_hash !== previous.content_hash
    || value.to_content_hash !== selected.content_hash
  ) throw new ArchiveIntegrityError();

  const before = new Map(previous.parent_refs.map((artifact) => [artifactIdentity(artifact), artifact]));
  const after = new Map(selected.parent_refs.map((artifact) => [artifactIdentity(artifact), artifact]));
  const expected = [...new Set([...before.keys(), ...after.keys()])]
    .sort()
    .flatMap((identity): DiffEntry[] => {
      const earlier = before.get(identity) ?? null;
      const later = after.get(identity) ?? null;
      if (earlier !== null && later !== null && sameArtifact(earlier, later)) return [];
      const artifact = later ?? earlier;
      if (artifact === null) return [];
      return [{
        schema_version: "underwriting.v1",
        group: groupForArtifact(artifact.artifact_type),
        change_type: earlier === null ? "added" : later === null ? "removed" : "replaced",
        artifact_type: artifact.artifact_type,
        identity: artifact.identity,
        before: earlier,
        after: later,
      } as DiffEntry];
    })
    .sort(compareDiffEntries);

  if (
    value.entries.length !== expected.length
    || value.entries.some((entry, index) => !sameDiffEntry(entry, expected[index]))
  ) throw new ArchiveIntegrityError();
  return value;
}

function groupForArtifact(artifactType: string): DiffEntry["group"] {
  if (["source_manifest", "metric_definition", "metric_observation", "ledger", "semantic_snapshot", "source_fact"].includes(artifactType)) return "evidence";
  if (artifactType === "mechanism") return "mechanism";
  if (["industry_state", "industry_scenario", "company_exposure", "earnings_engine", "forecast_input", "falsifier"].includes(artifactType)) return "industry_model";
  if (["answerability", "research_boundary"].includes(artifactType)) return "answerability";
  throw new ArchiveIntegrityError();
}

function compareDiffEntries(left: DiffEntry, right: DiffEntry): number {
  const rank: Record<DiffEntry["group"], number> = {
    evidence: 0, mechanism: 1, industry_model: 2, answerability: 3,
  };
  const leftKey = [rank[left.group], left.artifact_type, left.identity, left.change_type, left.before?.reference ?? "", left.after?.reference ?? ""];
  const rightKey = [rank[right.group], right.artifact_type, right.identity, right.change_type, right.before?.reference ?? "", right.after?.reference ?? ""];
  return leftKey.join("\u0000").localeCompare(rightKey.join("\u0000"));
}

function sameDiffEntry(left: DiffEntry, right: DiffEntry | undefined): boolean {
  return right !== undefined
    && left.schema_version === right.schema_version
    && left.group === right.group
    && left.change_type === right.change_type
    && left.artifact_type === right.artifact_type
    && left.identity === right.identity
    && (left.before === null ? right.before === null : right.before !== null && sameArtifact(left.before, right.before))
    && (left.after === null ? right.after === null : right.after !== null && sameArtifact(left.after, right.after));
}

function safeEnvelopeText(value: string | undefined): string | null {
  if (!value) return null;
  const clean = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 240);
  return clean || null;
}

function errorCopy(error: unknown, subject: "directory" | "detail"): string {
  if (error instanceof ArchiveIntegrityError) return INTEGRITY_ERROR;
  if (error instanceof UnderwritingResearchRequestError) {
    if (error.status === 404 && subject === "detail") return "找不到该冻结版本档案。";
    if (error.status === 422) {
      const message = safeEnvelopeText(error.message);
      const requestId = safeEnvelopeText(error.requestId);
      const prefix = subject === "directory"
        ? "查询条件无法读取，请修改后重试。"
        : "所选冻结版本无法安全读取或完成校验，未展示该版本、当前或更新资料。";
      return [prefix, message && `服务说明：${message}`, requestId && `请求编号：${requestId}`]
        .filter((part): part is string => Boolean(part))
        .join(" ");
    }
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

function ResearchBoundaries({ artifacts }: { artifacts: Artifact[] }) {
  const boundaryRecords = artifacts.filter((artifact) => artifact.artifact_type === "research_boundary"
    || ["not_answerable", "wait_for_validation", "unknown_evidence_gap", "candidate"].includes(artifact.status ?? ""));
  return (
    <aside className="ura-boundaries" aria-label="研究边界">
      <p className="ros-eyebrow">当前冻结父引用</p>
      <h2>研究边界</h2>
      <p className="ura-boundaries__intro">仅列出当前版本明确记录的状态、Unknown 或候选资料，不作额外推断。</p>
      {boundaryRecords.length === 0 ? <p className="ura-boundaries__empty">当前版本没有记录边界资料。</p> : (
        <ul className="ura-boundary-list">
          {boundaryRecords.map((artifact) => (
            <li key={`${artifact.artifact_type}:${artifact.identity}:${artifact.content_hash}`}>
              <b>{labelForStatus(artifact.status) ?? "已记录资料"}</b>
              <span>{artifact.reference}</span>
              <small>{artifact.identity}{artifact.source_locators.length ? ` · ${artifact.source_locators.join("；")}` : ""}</small>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function DirectoryPage() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<ArchiveKind>("");
  const [archiveState, setArchiveState] = useState<LoadState<ResearchArchiveList>>({ state: "loading" });
  const filterGeneration = useRef(0);

  useEffect(() => {
    let live = true;
    const generation = ++filterGeneration.current;
    setArchiveState({ state: "loading" });
    void underwritingResearchApi.listArchives({ query: query.trim() || undefined, kind: kind === "" ? undefined : kind })
      .then((value) => { if (live && filterGeneration.current === generation) setArchiveState({ state: "ready", value }); })
      .catch((error: unknown) => { if (live && filterGeneration.current === generation) setArchiveState({ state: "error", error }); });
    return () => { live = false; };
  }, [query, kind]);

  async function loadMore() {
    if (archiveState.state !== "ready") return;
    const previous = archiveState.value;
    const cursor = previous.next_cursor;
    if (!cursor) return;
    const generation = filterGeneration.current;
    setArchiveState({ state: "loading", value: previous });
    try {
      const next = await underwritingResearchApi.listArchives({
        query: query.trim() || undefined,
        kind: kind === "" ? undefined : kind,
        cursor,
      });
      if (filterGeneration.current === generation) {
        setArchiveState({ state: "ready", value: { ...next, items: [...previous.items, ...next.items] } });
      }
    } catch (error) {
      if (filterGeneration.current === generation) setArchiveState({ state: "error", error });
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
  const [detailState, setDetailState] = useState<LoadState<DetailLoad> | null>(null);

  useEffect(() => {
    let live = true;
    setHistoryState({ state: "loading" });
    setSelectedId(null); setDetailState(null);
    void underwritingResearchApi.history(objectId, versionKind).then((value) => {
      if (!live) return;
      const history = checkedHistory(value, objectId, versionKind);
      setHistoryState({ state: "ready", value: history });
      const latest = history.revisions[history.revisions.length - 1];
      setSelectedId(latest?.id ?? null);
    }).catch((error: unknown) => { if (live) setHistoryState({ state: "error", error }); });
    return () => { live = false; };
  }, [objectId, versionKind]);

  const revisions = useMemo(() => historyState.state === "ready" ? historyState.value.revisions : [], [historyState]);
  const selectedIndex = revisions.findIndex((revision) => revision.id === selectedId);
  const selected = selectedIndex >= 0 ? revisions[selectedIndex] : null;
  const previous = selectedIndex > 0 ? revisions[selectedIndex - 1] : null;

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setDetailState({ state: "loading" });
    void Promise.all([
      underwritingResearchApi.revision(selected.id),
      previous ? underwritingResearchApi.diff(previous.id, selected.id) : Promise.resolve(null),
    ]).then(([revision, diff]) => {
      if (!live) return;
      const checked = checkedRevision(revision, selected);
      setDetailState({ state: "ready", value: {
        revision: checked,
        diff: previous ? checkedDiff(diff, previous, checked) : null,
      } });
    }).catch((error: unknown) => { if (live) setDetailState({ state: "error", error }); });
    return () => { live = false; };
  }, [selected?.id, previous?.id]);

  if (historyState.state === "loading") return <main className="ros-page ura-page"><p className="ura-loading" role="status">正在读取冻结版本档案</p></main>;
  if (historyState.state === "error") return <main className="ros-page ura-page"><p className="ura-alert" role="alert">{errorCopy(historyState.error, "detail")}</p></main>;
  if (!revisions.length || !selected) return <main className="ros-page ura-page"><p className="ura-empty" role="status">该档案没有可读取的冻结版本。</p></main>;
  if (!detailState || detailState.state === "loading") return <main className="ros-page ura-page"><p className="ura-loading" role="status">正在校验已选冻结版本与相邻差异</p></main>;
  if (detailState.state === "error") return <main className="ros-page ura-page"><p className="ura-alert" role="alert">{errorCopy(detailState.error, "detail")}</p></main>;

  const revision = detailState.value.revision;
  const changed = detailState.value.diff?.entries ?? [];
  const artifacts = revision.parent_refs;
  const statuses = [...new Set(artifacts.map((artifact) => labelForStatus(artifact.status)).filter((status): status is string => Boolean(status)))];

  return (
    <main className="ros-page ura-page">
      <header className="ros-page-head ura-page-head"><div><p className="ros-eyebrow">冻结研究档案</p><h1>{historyState.value.canonical_name}</h1><p>{labelForObjectKind(historyState.value.object_kind)} · {historyState.value.external_key}</p></div><Link className="ura-back-link" to="/underwriting/research">返回档案目录</Link></header>
      <div className="ura-layout">
        <aside className="ura-timeline" aria-label="研究版本时间线"><p className="ros-eyebrow">不可变版本</p>{revisions.map((revision) => <button key={revision.id} type="button" aria-pressed={revision.id === selected.id} className={revision.id === selected.id ? "ura-version is-selected" : "ura-version"} onClick={() => setSelectedId(revision.id)}><b>版本 {revision.sequence}</b><span>{revision.cutoff}</span></button>)}</aside>
        <section className="ura-detail" aria-label="已选冻结版本">
          <section className="ura-version-summary"><h2>版本 {revision.sequence}</h2><dl><div><dt>截点</dt><dd>{revision.cutoff}</dd></div><div><dt>来源摘要</dt><dd>{revision.source_manifest_hash}</dd></div><div><dt>内容摘要</dt><dd>{revision.content_hash}</dd></div></dl>{statuses.length > 0 && <div className="ura-statuses" aria-label="当前冻结状态">{statuses.map((status) => <span key={status}>{status}</span>)}</div>}</section>
          {previous ? <p className="ura-predecessor">仅与紧邻的版本 {previous.sequence} 对照。</p> : <p className="ura-predecessor">这是该档案最早的冻结版本，没有前序版本可对照。</p>}
          {previous && detailState.value.diff && <div className="ura-diff-groups">{GROUPS.map(([group, heading]) => {
            const entries = changed.filter((entry) => entry.group === group);
            return <section className="ura-diff-group" key={group}><h2>{heading}</h2>{entries.length === 0 ? <p>这一版本没有该类冻结变化</p> : entries.map((entry) => <article className="ura-diff-entry" key={`${entry.change_type}:${entry.artifact_type}:${entry.identity}`}><header><b>{entry.change_type}</b><span>{entry.artifact_type}</span></header>{entry.before && <section className="ura-diff-card" aria-label="前一版本证据"><h3>前一版本</h3><ArtifactDetails artifact={entry.before} /></section>}{entry.after && <section className="ura-diff-card" aria-label="当前版本证据"><h3>当前版本</h3><ArtifactDetails artifact={entry.after} /></section>}</article>)}</section>;
          })}</div>}
          <EvidenceTable artifacts={artifacts} />
        </section>
        <ResearchBoundaries artifacts={artifacts} />
      </div>
    </main>
  );
}

export default function ResearchArchivePage() {
  const { objectId, versionKind } = useParams();
  return objectId && versionKind ? <DetailPage objectId={objectId} versionKind={versionKind} /> : <DirectoryPage />;
}
