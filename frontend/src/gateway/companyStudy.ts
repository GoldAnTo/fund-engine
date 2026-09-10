import { GatewaySchemaError } from "./GatewaySchemaError";

export type StudyMarket = "CN" | "HK" | "US" | "other";
export type StudyKind = "baseline" | "event" | "material" | "refresh";
export type CompanyStudy = {
  id: string;
  name: string;
  symbol: string | null;
  market: StudyMarket;
  focus: string;
  revision: number;
  created_at: string;
  updated_at: string;
};
export type StudyActivity = {
  id: string;
  study_id: string;
  kind: StudyKind | "linked";
  title: string;
  text: string;
  status: "queued" | "starting" | "running" | "completed" | "failed" | "blocked";
  conversation_id: string | null;
  run_spec_id: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};
export type StudyRevision = {
  id: string;
  version: number;
  activity_id: string;
  conversation_id: string;
  run_spec_id: string;
  team_revision: number;
  note: string;
  created_at: string;
};
export type StudyMonitor = {
  version: number;
  status: "active" | "paused";
  frequency: "daily" | "weekly";
  focus: string;
  next_due_at: string | null;
};
export type StudyDetail = {
  study: CompanyStudy;
  activities: StudyActivity[];
  revisions: StudyRevision[];
  monitor: StudyMonitor | null;
};
export type CreateStudy = Pick<CompanyStudy, "name" | "symbol" | "market" | "focus">;
export type ConfigureStudyMonitor = Pick<StudyMonitor, "status" | "frequency" | "focus">;
export interface CompanyStudyClient {
  listStudies(signal?: AbortSignal): Promise<CompanyStudy[]>;
  getStudy(id: string, signal?: AbortSignal): Promise<StudyDetail>;
  createStudy(body: CreateStudy, key: string, signal?: AbortSignal): Promise<CompanyStudy>;
  createStudyActivity(
    id: string,
    body: { kind: StudyKind; text: string },
    key: string,
    signal?: AbortSignal,
  ): Promise<StudyActivity>;
  linkStudyConversation(
    id: string,
    body: { conversation_id: string },
    key: string,
    signal?: AbortSignal,
  ): Promise<StudyActivity>;
  adoptStudyRevision(
    id: string,
    body: { activity_id: string; expected_revision: number; note: string },
    key: string,
    signal?: AbortSignal,
  ): Promise<StudyRevision>;
  configureStudyMonitor(
    id: string,
    body: ConfigureStudyMonitor,
    key: string,
    signal?: AbortSignal,
  ): Promise<StudyMonitor>;
  retryStudyActivity(id: string, activityId: string, key: string, signal?: AbortSignal): Promise<StudyActivity>;
}
export function hasCompanyStudyClient(value: object): value is CompanyStudyClient {
  return [
    "listStudies",
    "getStudy",
    "createStudy",
    "createStudyActivity",
    "linkStudyConversation",
    "adoptStudyRevision",
    "configureStudyMonitor",
    "retryStudyActivity",
  ].every((key) => typeof (value as Record<string, unknown>)[key] === "function");
}
function invalid(): never {
  throw new GatewaySchemaError("Company research response is invalid or belongs to another scope");
}
function obj(v: unknown, keys: string[]): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) return invalid();
  const r = v as Record<string, unknown>;
  if (Object.keys(r).length !== keys.length || keys.some((k) => !(k in r))) return invalid();
  return r;
}
function str(v: unknown, max = 20000): string {
  return typeof v === "string" && v.length <= max ? v : invalid();
}
function id(v: unknown): string {
  const s = str(v, 36);
  return /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(s) ? s : invalid();
}
function number(v: unknown, min = 0): number {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= min ? v : invalid();
}
function choice<T extends string>(v: unknown, choices: readonly T[]): T {
  return choices.includes(v as T) ? (v as T) : invalid();
}
function date(v: unknown): string {
  const s = str(v, 50);
  return /^\d{4}-\d{2}-\d{2}T/.test(s) && Number.isFinite(Date.parse(s)) ? s : invalid();
}
function array<T>(v: unknown, parse: (entry: unknown) => T): T[] {
  return Array.isArray(v) && v.length <= 10000 ? v.map(parse) : invalid();
}
export function parseStudy(v: unknown): CompanyStudy {
  const r = obj(v, ["id", "name", "symbol", "market", "focus", "revision", "created_at", "updated_at"]);
  return {
    id: id(r.id),
    name: str(r.name, 256),
    symbol: r.symbol === null ? null : str(r.symbol, 64),
    market: choice(r.market, ["CN", "HK", "US", "other"]),
    focus: str(r.focus),
    revision: number(r.revision),
    created_at: date(r.created_at),
    updated_at: date(r.updated_at),
  };
}
export function parseStudyActivity(v: unknown, studyId: string): StudyActivity {
  const r = obj(v, [
    "id",
    "study_id",
    "kind",
    "title",
    "text",
    "status",
    "conversation_id",
    "run_spec_id",
    "error_code",
    "created_at",
    "updated_at",
  ]);
  if (r.study_id !== studyId) return invalid();
  const conversation_id = r.conversation_id === null ? null : id(r.conversation_id),
    run_spec_id = r.run_spec_id === null ? null : id(r.run_spec_id);
  if ((conversation_id === null) !== (run_spec_id === null)) return invalid();
  return {
    id: id(r.id),
    study_id: id(r.study_id),
    kind: choice(r.kind, ["baseline", "event", "material", "refresh", "linked"]),
    title: str(r.title, 256),
    text: str(r.text),
    status: choice(r.status, ["queued", "starting", "running", "completed", "failed", "blocked"]),
    conversation_id,
    run_spec_id,
    error_code: r.error_code === null ? null : str(r.error_code, 128),
    created_at: date(r.created_at),
    updated_at: date(r.updated_at),
  };
}
export function parseStudyRevision(v: unknown): StudyRevision {
  const r = obj(v, [
    "id",
    "version",
    "activity_id",
    "conversation_id",
    "run_spec_id",
    "team_revision",
    "note",
    "created_at",
  ]);
  return {
    id: id(r.id),
    version: number(r.version, 1),
    activity_id: id(r.activity_id),
    conversation_id: id(r.conversation_id),
    run_spec_id: id(r.run_spec_id),
    team_revision: number(r.team_revision, 1),
    note: str(r.note),
    created_at: date(r.created_at),
  };
}
export function parseStudyMonitor(v: unknown): StudyMonitor {
  const r = obj(v, ["version", "status", "frequency", "focus", "next_due_at"]);
  return {
    version: number(r.version, 1),
    status: choice(r.status, ["active", "paused"]),
    frequency: choice(r.frequency, ["daily", "weekly"]),
    focus: str(r.focus),
    next_due_at: r.next_due_at === null ? null : date(r.next_due_at),
  };
}
export function parseStudyList(v: unknown): CompanyStudy[] {
  return array(obj(v, ["studies"]).studies, parseStudy);
}
export function parseStudyDetail(v: unknown, studyId: string): StudyDetail {
  const r = obj(v, ["study", "activities", "revisions", "monitor"]),
    study = parseStudy(r.study);
  if (study.id !== studyId) return invalid();
  const activities = array(r.activities, (item) => parseStudyActivity(item, studyId)),
    revisions = array(r.revisions, parseStudyRevision);
  if (
    new Set(activities.map((a) => a.id)).size !== activities.length ||
    new Set(revisions.map((a) => a.version)).size !== revisions.length
  )
    return invalid();
  return { study, activities, revisions, monitor: r.monitor === null ? null : parseStudyMonitor(r.monitor) };
}
