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

type ErrorDetails = Record<string, unknown>;

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
    && typeof value.object_id === "string"
    && typeof value.identity_version_id === "string"
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
    && typeof value.id === "string"
    && typeof value.primary_company_id === "string"
    && isStringArray(value.target_security_ids);
}

function isProjectList(value: unknown): value is ProductProjectList {
  return isProductDto(value) && Array.isArray(value.items) && value.items.every(isProject);
}

function isProjectChildRecord(value: unknown): value is Record<string, unknown> & {
  schema_version: "underwriting.v1";
  id: string;
  project_id: string;
} {
  return isProductDto(value) && typeof value.id === "string" && typeof value.project_id === "string";
}

function isScope(value: unknown): value is ProductScope {
  return isProjectChildRecord(value) && isRecord(value.payload);
}

function isAgenda(value: unknown): value is ProductAgenda {
  return isProjectChildRecord(value) && typeof value.scope_id === "string";
}

function isIdentifiedRecord(value: unknown): value is Record<string, unknown> & {
  schema_version: "underwriting.v1";
  id: string;
} {
  return isProductDto(value) && typeof value.id === "string";
}

function isPrice(value: unknown): value is ProductPriceSnapshot {
  return isIdentifiedRecord(value) && typeof value.security_identity_id === "string";
}

function isFx(value: unknown): value is ProductFxSnapshot {
  return isIdentifiedRecord(value)
    && typeof value.base_currency === "string"
    && typeof value.quote_currency === "string";
}

function isCapital(value: unknown): value is ProductCapitalStructure {
  return isIdentifiedRecord(value) && typeof value.company_id === "string";
}

function isRights(value: unknown): value is ProductSecurityRights {
  return isIdentifiedRecord(value) && typeof value.security_identity_id === "string";
}

function isDraftContent(value: unknown): boolean {
  return isProductDto(value)
    && value.publication_status === "draft"
    && isNullableString(value.mandate_id)
    && isNullableString(value.scope_id)
    && isNullableString(value.agenda_id)
    && isNullableString(value.historical_basis_id)
    && isStringArray(value.price_snapshot_ids)
    && isStringArray(value.fx_snapshot_ids)
    && isNullableString(value.capital_structure_snapshot_id)
    && isStringArray(value.security_rights_ids)
    && isNullableString(value.user_focus);
}

function isDraft(value: unknown): value is ProductDraft {
  return isProductDto(value)
    && typeof value.id === "string"
    && typeof value.project_id === "string"
    && typeof value.lock_version === "number"
    && isDraftContent(value.content);
}

function isPreview(value: unknown): value is ProductPreview {
  return isProductDto(value)
    && typeof value.project_id === "string"
    && typeof value.expected_lock_version === "number"
    && isProductDto(value.assessment)
    && isStringArray(value.assessment.blockers)
    && isStringArray(value.assessment.resolution_requirements)
    && isProductDto(value.boundary)
    && isRecord(value.manifest)
    && value.manifest.schema_version === "underwriting.research-revision-manifest.v1"
    && value.manifest.project_id === value.project_id;
}

function isRevision(value: unknown): value is ProductRevision {
  return isProductDto(value)
    && typeof value.id === "string"
    && typeof value.project_id === "string"
    && value.version_kind === "independent_research"
    && ["answerable", "partially_answerable", "not_answerable"].includes(String(value.answerability))
    && isNullableString(value.direction)
    && isNullableString(value.confidence);
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

function validatedError(value: unknown): {
  code: string;
  message: string;
  details: ErrorDetails | null;
} | null {
  if (!isProductDto(value) || !isRecord(value.error)) return null;
  if (typeof value.error.code !== "string" || typeof value.error.message !== "string") return null;
  const message = value.error.message.trim();
  if (!message || message.length > 500) return null;
  return {
    code: value.error.code,
    message,
    details: isRecord(value.error.details) ? value.error.details : null,
  };
}

type Validator<T> = (value: unknown) => value is T;

async function requestJson<T>(
  url: string,
  validator: Validator<T>,
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
    const envelope = validatedError(parsed);
    if (envelope) {
      throw new InvestmentResearchRequestError(
        envelope.message,
        response.status,
        envelope.code,
        requestId,
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

function sameStrings(actual: string[], expected: string[]): boolean {
  return actual.length === expected.length && actual.every((item, index) => item === expected[index]);
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
    return requestJson(`${this.root}/objects?${params}`, isObjectSearch, { method: "GET" });
  }

  projects(limit = 20): Promise<ProductProjectList> {
    return requestJson(`${this.root}/projects?limit=${limit}`, isProjectList, { method: "GET" });
  }

  async createProject(body: CreateProjectRequest): Promise<ProductProject> {
    const project = await requestJson(`${this.root}/projects`, isProject, jsonInit("POST", body));
    if (project.primary_company_id !== body.primary_company_id
      || !sameStrings(project.target_security_ids, body.target_security_ids)) {
      mismatch("project identity mismatch");
    }
    return project;
  }

  async project(projectId: string): Promise<ProductProject> {
    const project = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}`, isProject, { method: "GET" });
    if (project.id !== projectId) mismatch("project identity mismatch");
    return project;
  }

  async createMandate(projectId: string, body: CreateMandateRequest): Promise<ProductMandate> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/mandates`, (candidate): candidate is ProductMandate => isProjectChildRecord(candidate), jsonInit("POST", body));
    if (value.project_id !== projectId) mismatch("mandate project identity mismatch");
    return value;
  }

  async createScope(projectId: string, body: CreateScopeRequest): Promise<ProductScope> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/scopes`, isScope, jsonInit("POST", body));
    if (value.project_id !== projectId
      || value.payload.primary_company_id !== body.primary_company_id
      || !sameStrings(value.payload.target_security_ids, body.target_security_ids)) {
      mismatch("scope project identity mismatch");
    }
    return value;
  }

  async createAgenda(projectId: string, body: CreateAgendaRequest): Promise<ProductAgenda> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/agendas`, isAgenda, jsonInit("POST", body));
    if (value.project_id !== projectId || value.scope_id !== body.scope_id) mismatch("agenda scope identity mismatch");
    return value;
  }

  createHistoricalBasis(body: CreateHistoricalBasisRequest): Promise<ProductHistoricalBasis> {
    return requestJson(`${this.root}/historical-bases`, (candidate): candidate is ProductHistoricalBasis => isIdentifiedRecord(candidate), jsonInit("POST", body));
  }

  async createPriceSnapshot(body: CreatePriceSnapshotRequest): Promise<ProductPriceSnapshot> {
    const value = await requestJson(`${this.root}/market/price-snapshots`, isPrice, jsonInit("POST", body));
    if (value.security_identity_id !== body.security_identity_id) mismatch("price security identity mismatch");
    return value;
  }

  async createFxSnapshot(body: CreateFxSnapshotRequest): Promise<ProductFxSnapshot> {
    const value = await requestJson(`${this.root}/market/fx-snapshots`, isFx, jsonInit("POST", body));
    if (value.base_currency !== body.base_currency || value.quote_currency !== body.quote_currency) mismatch("FX currency identity mismatch");
    return value;
  }

  async createCapitalStructure(body: CreateCapitalStructureRequest): Promise<ProductCapitalStructure> {
    const value = await requestJson(`${this.root}/market/capital-structure-snapshots`, isCapital, jsonInit("POST", body));
    if (value.company_id !== body.company_id) mismatch("capital structure company identity mismatch");
    return value;
  }

  async createSecurityRights(body: CreateSecurityRightsRequest): Promise<ProductSecurityRights> {
    const value = await requestJson(`${this.root}/market/security-rights`, isRights, jsonInit("POST", body));
    if (value.security_identity_id !== body.security_identity_id) mismatch("security rights identity mismatch");
    return value;
  }

  async draft(projectId: string): Promise<ProductDraft> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/draft`, isDraft, { method: "GET" });
    if (value.project_id !== projectId) mismatch("draft project identity mismatch");
    return value;
  }

  async saveDraft(projectId: string, body: PatchDraftRequest): Promise<ProductDraft> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/draft`, isDraft, jsonInit("PATCH", body));
    if (value.project_id !== projectId) mismatch("draft project identity mismatch");
    const content = value.content;
    const scalarKeys = ["mandate_id", "scope_id", "agenda_id", "historical_basis_id", "capital_structure_snapshot_id", "user_focus"] as const;
    for (const key of scalarKeys) {
      if (body[key] !== undefined && content[key] !== body[key]) mismatch(`draft ${key} mismatch`);
    }
    const arrayKeys = ["price_snapshot_ids", "fx_snapshot_ids", "security_rights_ids"] as const;
    for (const key of arrayKeys) {
      if (body[key] !== undefined && body[key] !== null && !sameStrings(content[key], body[key])) mismatch(`draft ${key} mismatch`);
    }
    return value;
  }

  async preview(projectId: string, body: PreviewRevisionRequest): Promise<ProductPreview> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/publication-preview`, isPreview, jsonInit("POST", body));
    if (value.project_id !== projectId || value.manifest.project_id !== projectId) mismatch("preview project identity mismatch");
    return value;
  }

  async publish(projectId: string, body: PublishRevisionRequest, idempotencyKey: string): Promise<ProductRevision> {
    const value = await requestJson(
      `${this.root}/projects/${encodeURIComponent(projectId)}/publish`,
      isRevision,
      jsonInit("POST", body, { "Idempotency-Key": idempotencyKey }),
    );
    if (value.project_id !== projectId) mismatch("published revision project identity mismatch");
    return value;
  }

  async revision(revisionId: string): Promise<ProductRevision> {
    const value = await requestJson(`${this.root}/revisions/${encodeURIComponent(revisionId)}`, isRevision, { method: "GET" });
    if (value.id !== revisionId) mismatch("revision identity mismatch");
    return value;
  }
}

export const investmentResearchApi = new InvestmentResearchApi();
