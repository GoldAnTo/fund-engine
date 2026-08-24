import type { components } from "../contracts/v1";

type Schemas = components["schemas"];

export type ProductObjectSearch = Schemas["ProductObjectSearchResponse"];
export type ProductObjectSearchItem = Schemas["ProductObjectSearchItemResponse"];
export type ProductProject = Schemas["ResearchProjectResponse"];
export type ProductProjectList = Schemas["ResearchProjectListResponse"];
export type ProductDraft = Schemas["WorkspaceDraftResponse"];
export type ProductPreview = Schemas["PublicationPreviewResponse"];
export type ProductRevision = Schemas["ProductRevisionResponse"];
export type CreateProjectRequest = Schemas["CreateResearchProjectRequest"];
export type CreateMandateRequest = Schemas["CreateProductMandateRequest"];
export type CreateScopeRequest = Schemas["CreateResearchScopeRequest"];
export type CreateAgendaRequest = Schemas["CreateResearchAgendaRequest"];
export type CreateHistoricalBasisRequest = Schemas["CreateProductHistoricalBasisRequest"];
export type CreatePriceSnapshotRequest = Schemas["CreatePriceSnapshotRequest"];
export type CreateFxSnapshotRequest = Schemas["CreateFXSnapshotRequest"];
export type CreateCapitalStructureRequest = Schemas["CreateCapitalStructureSnapshotRequest"];
export type CreateSecurityRightsRequest = Schemas["CreateSecurityRightsRequest"];
export type PatchDraftRequest = Schemas["PatchWorkspaceDraftRequest"];
export type PreviewRevisionRequest = Schemas["PreviewProductRevisionRequest"];
export type PublishRevisionRequest = Schemas["PublishProductRevisionRequest"];
export type ProductMandate = Schemas["ProductMandateResponse"];
export type ProductScope = Schemas["ResearchScopeResponse"];
export type ProductAgenda = Schemas["ResearchAgendaResponse"];
export type ProductHistoricalBasis = Schemas["ProductHistoricalBasisResponse"];
export type ProductPriceSnapshot = Schemas["PriceSnapshotResponse"];
export type ProductFxSnapshot = Schemas["FXSnapshotResponse"];
export type ProductCapitalStructure = Schemas["CapitalStructureSnapshotResponse"];
export type ProductSecurityRights = Schemas["SecurityRightsResponse"];

type ErrorDetails = NonNullable<Schemas["UnderwritingErrorBody"]["details"]>;

export class InvestmentResearchRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null,
    readonly details: ErrorDetails | null = null,
  ) {
    super(message);
    this.name = "InvestmentResearchRequestError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/i;

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function isNullableUuid(value: unknown): value is string | null {
  return value === null || isUuid(value);
}

function isHash(value: unknown): value is string {
  return typeof value === "string" && SHA256_PATTERN.test(value);
}

function isDateTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts || Number.isNaN(Date.parse(value))) return false;
  const [, year, month, day, hour, minute, second] = parts;
  const wallClock = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second)));
  return wallClock.getUTCFullYear() === Number(year)
    && wallClock.getUTCMonth() === Number(month) - 1
    && wallClock.getUTCDate() === Number(day)
    && wallClock.getUTCHours() === Number(hour)
    && wallClock.getUTCMinutes() === Number(minute)
    && wallClock.getUTCSeconds() === Number(second);
}

function isNullableDateTime(value: unknown): value is string | null {
  return value === null || isDateTime(value);
}

function isDecimal(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value));
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isNonEmptyStrings(value: unknown): value is string[] {
  return isStringArray(value) && value.every((item) => item.trim() !== "");
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isUuidArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isUuid);
}

function isUniqueUuidArray(value: unknown, nonempty = false): value is string[] {
  return isUuidArray(value)
    && (!nonempty || value.length > 0)
    && new Set(value).size === value.length;
}

function isMarketSnapshotRefs(value: unknown): value is string[] {
  return isNonEmptyStrings(value)
    && value.length >= 3
    && new Set(value).size === value.length
    && value.every((ref) => /^(?:price|fx|capital_structure|security_rights):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(ref));
}

function isMembershipRefs(value: unknown): boolean {
  if (!Array.isArray(value) || value.length === 0) return false;
  if (!value.every((ref) => isRecord(ref) && isUuid(ref.membership_id) && isUuid(ref.security_id) && isHash(ref.content_hash))) return false;
  const membershipIds = value.map((ref) => isRecord(ref) ? ref.membership_id : null);
  const securityIds = value.map((ref) => isRecord(ref) ? ref.security_id : null);
  return new Set(membershipIds).size === value.length && new Set(securityIds).size === value.length;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isProductDto(value: unknown): value is Record<string, unknown> & {
  schema_version: "underwriting.v1";
} {
  return isRecord(value) && value.schema_version === "underwriting.v1";
}

function isObjectItem(value: unknown): value is ProductObjectSearchItem {
  return isProductDto(value)
    && isUuid(value.object_id)
    && isUuid(value.identity_version_id)
    && ["industry", "company", "security"].includes(String(value.kind))
    && typeof value.external_key === "string"
    && typeof value.canonical_name === "string"
    && isNullableString(value.symbol)
    && isNullableString(value.exchange)
    && isNullableString(value.share_class)
    && (value.trading_currency === null || value.trading_currency === "CNY" || value.trading_currency === "USD");
}

function isObjectSearch(value: unknown): value is ProductObjectSearch {
  return isProductDto(value) && Array.isArray(value.items) && value.items.every(isObjectItem);
}

function isProject(value: unknown): value is ProductProject {
  return isProductDto(value)
    && isUuid(value.id)
    && isUuid(value.primary_company_id)
    && isUuidArray(value.target_security_ids)
    && new Set(value.target_security_ids).size === value.target_security_ids.length
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isProjectList(value: unknown): value is ProductProjectList {
  return isProductDto(value) && Array.isArray(value.items) && value.items.every(isProject);
}

function isProjectChildRecord(value: unknown): value is Record<string, unknown> & {
  schema_version: "underwriting.v1";
  id: string;
  project_id: string;
} {
  return isProductDto(value) && isUuid(value.id) && isUuid(value.project_id);
}

function isMandate(value: unknown): value is ProductMandate {
  return isProjectChildRecord(value)
    && typeof value.mandate_key === "string" && value.mandate_key.trim() !== ""
    && isNonNegativeInteger(value.horizon_years)
    && (value.base_currency === "CNY" || value.base_currency === "USD")
    && isDecimal(value.required_return)
    && isDecimal(value.permanent_loss_limit)
    && isNonEmptyStrings(value.comparison_set)
    && isNullableString(value.benchmark_key)
    && (value.required_excess_return === null || isDecimal(value.required_excess_return))
    && isDateTime(value.effective_at)
    && isNullableDateTime(value.expires_at)
    && isNonNegativeInteger(value.version)
    && isNullableUuid(value.supersedes_id)
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isScope(value: unknown): value is ProductScope {
  return isProjectChildRecord(value)
    && isNonNegativeInteger(value.version)
    && isProductDto(value.payload)
    && isUuid(value.payload.primary_company_id)
    && isUuidArray(value.payload.target_security_ids)
    && new Set(value.payload.target_security_ids).size === value.payload.target_security_ids.length
    && isUuidArray(value.payload.industry_ids)
    && isStringArray(value.payload.covered_segments)
    && isNullableString(value.payload.user_focus)
    && isStringArray(value.payload.exclusions)
    && isNullableUuid(value.supersedes_id)
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isAgenda(value: unknown): value is ProductAgenda {
  if (!isProjectChildRecord(value) || !isProductDto(value.payload) || !isProductDto(value.generator_provenance)) return false;
  const generator = value.generator_provenance;
  return isNonNegativeInteger(value.version)
    && isUuid(value.scope_id)
    && isNonEmptyStrings(value.payload.items)
    && isAgendaGenerator(generator)
    && isNullableUuid(value.supersedes_id)
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isAgendaGenerator(generator: Record<string, unknown>): boolean {
  if (!isHash(generator.output_hash)) return false;
  if (generator.method === "deterministic_template") {
    return isNonEmptyString(generator.template_key)
      && isNonEmptyString(generator.template_version)
      && generator.model_name === null
      && generator.prompt_template_version === null
      && generator.input_summary_hash === null;
  }
  if (generator.method === "ai_generated") {
    return generator.template_key === null
      && generator.template_version === null
      && isNonEmptyString(generator.model_name)
      && isNonEmptyString(generator.prompt_template_version)
      && isHash(generator.input_summary_hash);
  }
  return false;
}

function hasExactPreviewSnapshotRefs(manifestRefs: unknown, boundary: Record<string, unknown>): boolean {
  if (!isStringArray(manifestRefs)
    || !isStringArray(boundary.price_snapshot_ids)
    || !isStringArray(boundary.fx_snapshot_ids)
    || !isStringArray(boundary.security_rights_ids)
    || typeof boundary.capital_structure_snapshot_id !== "string") return false;
  const expected = [
    ...boundary.price_snapshot_ids.map((id) => `price:${id}`),
    ...boundary.fx_snapshot_ids.map((id) => `fx:${id}`),
    `capital_structure:${boundary.capital_structure_snapshot_id}`,
    ...boundary.security_rights_ids.map((id) => `security_rights:${id}`),
  ];
  return sameStringSets(manifestRefs, expected);
}

function hasExactRevisionSnapshotIds(value: Record<string, unknown>): boolean {
  if (!isStringArray(value.market_snapshot_ids)
    || !isStringArray(value.price_snapshot_ids)
    || !isStringArray(value.fx_snapshot_ids)
    || !isStringArray(value.security_rights_ids)
    || typeof value.capital_structure_snapshot_id !== "string") return false;
  return sameStringSets(value.market_snapshot_ids, [
    ...value.price_snapshot_ids,
    ...value.fx_snapshot_ids,
    value.capital_structure_snapshot_id,
    ...value.security_rights_ids,
  ]);
}

function isIdentifiedRecord(value: unknown): value is Record<string, unknown> & {
  schema_version: "underwriting.v1";
  id: string;
} {
  return isProductDto(value) && isUuid(value.id);
}

function isHistoricalBasis(value: unknown): value is ProductHistoricalBasis {
  return isIdentifiedRecord(value)
    && isDateTime(value.cutoff_at)
    && (value.price_as_of === undefined || value.price_as_of === null)
    && isHash(value.source_manifest_hash)
    && isHash(value.definition_bundle_hash)
    && isHash(value.parser_bundle_hash)
    && value.boundary_schema_version === "product.historical-basis.v1"
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isPrice(value: unknown): value is ProductPriceSnapshot {
  return isIdentifiedRecord(value)
    && isUuid(value.security_identity_id)
    && isDecimal(value.price)
    && (value.currency === "CNY" || value.currency === "USD")
    && typeof value.price_type === "string" && value.price_type.trim() !== ""
    && typeof value.adjustment_basis === "string" && value.adjustment_basis.trim() !== ""
    && isDateTime(value.market_at) && isDateTime(value.available_at)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isFx(value: unknown): value is ProductFxSnapshot {
  return isIdentifiedRecord(value)
    && (value.base_currency === "CNY" || value.base_currency === "USD")
    && (value.quote_currency === "CNY" || value.quote_currency === "USD")
    && isDecimal(value.rate)
    && value.quote_direction === "quote_per_base"
    && isDateTime(value.market_at) && isDateTime(value.available_at)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isCapital(value: unknown): value is ProductCapitalStructure {
  return isIdentifiedRecord(value)
    && isUuid(value.company_id)
    && (value.currency === "CNY" || value.currency === "USD")
    && isDecimal(value.cash) && isDecimal(value.debt) && isDecimal(value.minority_interest)
    && isDecimal(value.investments) && isDecimal(value.pension_liabilities)
    && isDecimal(value.other_adjustments) && isDecimal(value.basic_shares) && isDecimal(value.diluted_shares)
    && isStringArray(value.potential_dilution_descriptors)
    && isDateTime(value.report_period_start) && isDateTime(value.report_period_end)
    && isDateTime(value.market_at) && isDateTime(value.available_at)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isRights(value: unknown): value is ProductSecurityRights {
  return isIdentifiedRecord(value)
    && isUuid(value.security_identity_id)
    && isNonNegativeInteger(value.version)
    && isDecimal(value.economic_units) && isDecimal(value.votes_per_unit)
    && isDecimal(value.conversion_ratio) && isDecimal(value.adr_ratio)
    && isDecimal(value.dividend_rights_per_unit)
    && isDateTime(value.effective_from) && isNullableDateTime(value.effective_to)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isNullableUuid(value.supersedes_id)
    && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isDraftContent(value: unknown): boolean {
  return isProductDto(value)
    && value.publication_status === "draft"
    && isNullableUuid(value.mandate_id)
    && isNullableUuid(value.scope_id)
    && isNullableUuid(value.agenda_id)
    && isNullableUuid(value.historical_basis_id)
    && isUuidArray(value.price_snapshot_ids)
    && isUuidArray(value.fx_snapshot_ids)
    && isNullableUuid(value.capital_structure_snapshot_id)
    && isUuidArray(value.security_rights_ids)
    && isNullableString(value.user_focus);
}

function isDraft(value: unknown): value is ProductDraft {
  return isProductDto(value)
    && isUuid(value.id)
    && isUuid(value.project_id)
    && isNullableUuid(value.base_revision_id)
    && isNonNegativeInteger(value.lock_version)
    && isDraftContent(value.content)
    && isDateTime(value.created_at)
    && isDateTime(value.updated_at);
}

const answerabilities = ["answerable", "partially_answerable", "not_answerable"];
const directions = ["provisional_bullish", "provisional_neutral", "provisional_cautious"];
const confidences = ["low", "medium", "high"];

function hasConsistentAssessment(value: Record<string, unknown>): boolean {
  if (!answerabilities.includes(String(value.answerability))) return false;
  if (value.answerability === "not_answerable") return value.direction === null && value.confidence === null;
  return directions.includes(String(value.direction)) && confidences.includes(String(value.confidence));
}

function isPreview(value: unknown): value is ProductPreview {
  return isProductDto(value)
    && isUuid(value.project_id)
    && isNonNegativeInteger(value.expected_lock_version)
    && isDateTime(value.boundary_as_of)
    && isProductDto(value.assessment)
    && hasConsistentAssessment(value.assessment)
    && (value.assessment.publication_status === "user_frozen" || value.assessment.publication_status === "superseded")
    && isStringArray(value.assessment.blockers)
    && isStringArray(value.assessment.resolution_requirements)
    && isNullableDateTime(value.assessment.next_review_at)
    && isNullableUuid(value.assessment.parent_assessment_id)
    && isHash(value.assessment.content_hash)
    && isProductDto(value.boundary)
    && isUuid(value.boundary.historical_basis_id)
    && isUuid(value.boundary.mandate_id) && isUuid(value.boundary.scope_id) && isUuid(value.boundary.agenda_id)
    && isUniqueUuidArray(value.boundary.price_snapshot_ids, true) && isUniqueUuidArray(value.boundary.fx_snapshot_ids)
    && isUuid(value.boundary.capital_structure_snapshot_id) && isUniqueUuidArray(value.boundary.security_rights_ids, true)
    && isNullableUuid(value.boundary.parent_revision_id)
    && isHash(value.boundary_hash)
    && isRecord(value.manifest)
    && value.manifest.schema_version === "underwriting.research-revision-manifest.v1"
    && value.manifest.project_id === value.project_id
    && isRecord(value.manifest.project_ref)
    && value.manifest.project_ref.project_id === value.project_id
    && isHash(value.manifest.project_ref.content_hash)
    && isMembershipRefs(value.manifest.project_membership_refs)
    && isUuid(value.manifest.primary_object_id)
    && value.manifest.boundary_ref === "$boundary"
    && value.manifest.mandate_id === value.boundary.mandate_id
    && value.manifest.scope_id === value.boundary.scope_id
    && value.manifest.agenda_id === value.boundary.agenda_id
    && value.manifest.historical_basis_id === value.boundary.historical_basis_id
    && sameStringSets(value.manifest.price_snapshot_ids, value.boundary.price_snapshot_ids)
    && sameStringSets(value.manifest.fx_snapshot_ids, value.boundary.fx_snapshot_ids)
    && value.manifest.capital_structure_snapshot_id === value.boundary.capital_structure_snapshot_id
    && sameStringSets(value.manifest.security_rights_ids, value.boundary.security_rights_ids)
    && isMarketSnapshotRefs(value.manifest.market_snapshot_refs)
    && hasExactPreviewSnapshotRefs(value.manifest.market_snapshot_refs, value.boundary)
    && Array.isArray(value.manifest.model_refs) && value.manifest.model_refs.length === 0
    && value.manifest.assessment_ref === "$assessment"
    && value.manifest.memo_ref === null
    && value.manifest.parent_revision_id === value.boundary.parent_revision_id
    && isHash(value.manifest_hash);
}

function isRevision(value: unknown): value is ProductRevision {
  return isProductDto(value)
    && isUuid(value.id)
    && isUuid(value.project_id)
    && isUuid(value.object_id) && isUuid(value.basis_id) && isUuid(value.boundary_id) && isUuid(value.manifest_id)
    && value.version_kind === "independent_research"
    && isNonNegativeInteger(value.sequence)
    && isHash(value.content_hash) && isDateTime(value.cutoff)
    && isHash(value.source_manifest_hash) && isHash(value.manifest_hash)
    && isNullableUuid(value.parent_revision_id)
    && isUniqueUuidArray(value.price_snapshot_ids, true) && isUniqueUuidArray(value.fx_snapshot_ids)
    && isUuid(value.capital_structure_snapshot_id) && isUniqueUuidArray(value.security_rights_ids, true)
    && isUniqueUuidArray(value.market_snapshot_ids, true)
    && hasExactRevisionSnapshotIds(value)
    && hasConsistentAssessment(value)
    && (value.publication_status === "user_frozen" || value.publication_status === "superseded");
}

function safeParse(text: string): unknown | null {
  if (!text.trim()) return null;
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed;
  } catch {
    return null;
  }
}

function validatedError(value: unknown, headerRequestId: string | null): {
  code: string;
  message: string;
  requestId: string;
  details: ErrorDetails | null;
} | null {
  if (!isProductDto(value) || !isRecord(value.error)) return null;
  if (typeof value.error.code !== "string" || typeof value.error.message !== "string" || typeof value.error.request_id !== "string") return null;
  const message = value.error.message.trim();
  const requestId = value.error.request_id.trim();
  if (!message || message.length > 500 || !requestId || (headerRequestId !== null && headerRequestId !== requestId)) return null;
  if (value.error.details !== undefined && value.error.details !== null && !isRecord(value.error.details)) return null;
  return {
    code: value.error.code,
    message,
    requestId,
    details: isRecord(value.error.details) ? value.error.details : null,
  };
}

type Validator<T> = (value: unknown) => value is T;

async function requestJson<T>(
  url: string,
  validator: Validator<T>,
  expectedStatus: number,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: "include", ...init });
  } catch {
    throw new InvestmentResearchRequestError(
      "无法连接投资研究服务",
      0,
      "network_error",
      null,
    );
  }
  const parsed = safeParse(await response.text());
  const requestId = response.headers.get("x-request-id");
  if (!response.ok) {
    const envelope = validatedError(parsed, requestId);
    if (envelope) {
      throw new InvestmentResearchRequestError(
        envelope.message,
        response.status,
        envelope.code,
        envelope.requestId,
        envelope.details,
      );
    }
    throw new InvestmentResearchRequestError(
      "投资研究服务暂时无法完成请求",
      response.status,
      "invalid_error_response",
      requestId,
    );
  }
  if (response.status !== expectedStatus) {
    throw new InvestmentResearchRequestError(
      "投资研究服务返回了意外状态",
      response.status,
      "unexpected_status",
      requestId,
    );
  }
  if (!validator(parsed)) {
    throw new InvestmentResearchRequestError(
      "投资研究服务返回了无法验证的数据",
      response.status,
      "invalid_response",
      requestId,
    );
  }
  return parsed;
}

function sameStringSets(actual: unknown, expected: unknown): boolean {
  if (!isStringArray(actual) || !isStringArray(expected) || actual.length !== expected.length) return false;
  if (new Set(actual).size !== actual.length || new Set(expected).size !== expected.length) return false;
  return [...actual].sort().every((item, index) => item === [...expected].sort()[index]);
}

function sameOrderedStrings(actual: string[], expected: string[]): boolean {
  return actual.length === expected.length && actual.every((item, index) => item === expected[index]);
}

async function agendaItemsHash(items: string[]): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(items)),
  );
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sameAgendaGenerator(
  actual: ProductAgenda["generator_provenance"],
  expected: CreateAgendaRequest["generator"],
): boolean {
  return actual.method === expected.method
    && actual.template_key === (expected.template_key ?? null)
    && actual.template_version === (expected.template_version ?? null)
    && actual.model_name === (expected.model_name ?? null)
    && actual.prompt_template_version === (expected.prompt_template_version ?? null)
    && actual.input_summary_hash === (expected.input_summary_hash ?? null)
    && actual.output_hash === expected.output_hash;
}

function jsonInit(method: "POST" | "PATCH", body: object, headers?: HeadersInit): RequestInit {
  return {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  };
}

function mismatch(message: string): never {
  throw new InvestmentResearchRequestError(message, 502, "identity_mismatch", null);
}

export class InvestmentResearchApi {
  private readonly root: string;

  constructor(baseUrl = "") {
    this.root = `${baseUrl.replace(/\/$/, "")}/api/underwriting/v1/product`;
  }

  searchObjects(query: string, options: { asOf?: string; limit?: number } = {}): Promise<ProductObjectSearch> {
    const params = new URLSearchParams({ query });
    if (options.asOf) params.set("as_of", options.asOf);
    params.set("limit", String(options.limit ?? 20));
    return requestJson(`${this.root}/objects?${params}`, isObjectSearch, 200, { method: "GET" });
  }

  projects(limit = 20): Promise<ProductProjectList> {
    return requestJson(`${this.root}/projects?limit=${limit}`, isProjectList, 200, { method: "GET" });
  }

  async createProject(body: CreateProjectRequest): Promise<ProductProject> {
    const project = await requestJson(`${this.root}/projects`, isProject, 201, jsonInit("POST", body));
    if (project.primary_company_id !== body.primary_company_id
      || !sameStringSets(project.target_security_ids, body.target_security_ids)) {
      mismatch("project identity mismatch");
    }
    return project;
  }

  async project(projectId: string): Promise<ProductProject> {
    const project = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}`, isProject, 200, { method: "GET" });
    if (project.id !== projectId) mismatch("project identity mismatch");
    return project;
  }

  async createMandate(projectId: string, body: CreateMandateRequest): Promise<ProductMandate> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/mandates`, isMandate, 201, jsonInit("POST", body));
    if (value.project_id !== projectId) mismatch("mandate project identity mismatch");
    return value;
  }

  async createScope(projectId: string, body: CreateScopeRequest): Promise<ProductScope> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/scopes`, isScope, 201, jsonInit("POST", body));
    if (value.project_id !== projectId
      || value.payload.primary_company_id !== body.primary_company_id
      || !sameStringSets(value.payload.target_security_ids, body.target_security_ids)) {
      mismatch("scope project identity mismatch");
    }
    return value;
  }

  async createAgenda(projectId: string, body: CreateAgendaRequest): Promise<ProductAgenda> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/agendas`, isAgenda, 201, jsonInit("POST", body));
    if (value.project_id !== projectId
      || value.scope_id !== body.scope_id
      || !sameOrderedStrings(value.payload.items, body.items)
      || !sameAgendaGenerator(value.generator_provenance, body.generator)) {
      mismatch("agenda scope or provenance identity mismatch");
    }
    const expectedHash = await agendaItemsHash(body.items);
    if (body.generator.output_hash !== expectedHash || value.generator_provenance.output_hash !== expectedHash) {
      mismatch("agenda output hash mismatch");
    }
    return value;
  }

  createHistoricalBasis(body: CreateHistoricalBasisRequest): Promise<ProductHistoricalBasis> {
    return requestJson(`${this.root}/historical-bases`, isHistoricalBasis, 201, jsonInit("POST", body));
  }

  async createPriceSnapshot(body: CreatePriceSnapshotRequest): Promise<ProductPriceSnapshot> {
    const value = await requestJson(`${this.root}/market/price-snapshots`, isPrice, 201, jsonInit("POST", body));
    if (value.security_identity_id !== body.security_identity_id) mismatch("price security identity mismatch");
    return value;
  }

  async createFxSnapshot(body: CreateFxSnapshotRequest): Promise<ProductFxSnapshot> {
    const value = await requestJson(`${this.root}/market/fx-snapshots`, isFx, 201, jsonInit("POST", body));
    if (value.base_currency !== body.base_currency || value.quote_currency !== body.quote_currency) mismatch("FX currency identity mismatch");
    return value;
  }

  async createCapitalStructure(body: CreateCapitalStructureRequest): Promise<ProductCapitalStructure> {
    const value = await requestJson(`${this.root}/market/capital-structure-snapshots`, isCapital, 201, jsonInit("POST", body));
    if (value.company_id !== body.company_id) mismatch("capital structure company identity mismatch");
    return value;
  }

  async createSecurityRights(body: CreateSecurityRightsRequest): Promise<ProductSecurityRights> {
    const value = await requestJson(`${this.root}/market/security-rights`, isRights, 201, jsonInit("POST", body));
    if (value.security_identity_id !== body.security_identity_id) mismatch("security rights identity mismatch");
    return value;
  }

  async draft(projectId: string): Promise<ProductDraft> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/draft`, isDraft, 200, { method: "GET" });
    if (value.project_id !== projectId) mismatch("draft project identity mismatch");
    return value;
  }

  async saveDraft(projectId: string, body: PatchDraftRequest): Promise<ProductDraft> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/draft`, isDraft, 200, jsonInit("PATCH", body));
    if (value.project_id !== projectId) mismatch("draft project identity mismatch");
    const content = value.content;
    const scalarKeys = ["mandate_id", "scope_id", "agenda_id", "historical_basis_id", "capital_structure_snapshot_id", "user_focus"] as const;
    for (const key of scalarKeys) {
      if (body[key] !== undefined && content[key] !== body[key]) mismatch(`draft ${key} mismatch`);
    }
    const arrayKeys = ["price_snapshot_ids", "fx_snapshot_ids", "security_rights_ids"] as const;
    for (const key of arrayKeys) {
      if (body[key] !== undefined && body[key] !== null && !sameStringSets(content[key], body[key])) mismatch(`draft ${key} mismatch`);
    }
    return value;
  }

  async preview(projectId: string, body: PreviewRevisionRequest): Promise<ProductPreview> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/publication-preview`, isPreview, 200, jsonInit("POST", body));
    if (value.project_id !== projectId || value.manifest.project_id !== projectId) mismatch("preview project identity mismatch");
    return value;
  }

  async publish(projectId: string, body: PublishRevisionRequest, idempotencyKey: string): Promise<ProductRevision> {
    const value = await requestJson(
      `${this.root}/projects/${encodeURIComponent(projectId)}/publish`,
      isRevision,
      201,
      jsonInit("POST", body, { "Idempotency-Key": idempotencyKey }),
    );
    if (value.project_id !== projectId) mismatch("published revision project identity mismatch");
    return value;
  }

  async revision(revisionId: string): Promise<ProductRevision> {
    const value = await requestJson(`${this.root}/revisions/${encodeURIComponent(revisionId)}`, isRevision, 200, { method: "GET" });
    if (value.id !== revisionId) mismatch("revision identity mismatch");
    return value;
  }
}

export const investmentResearchApi = new InvestmentResearchApi();
