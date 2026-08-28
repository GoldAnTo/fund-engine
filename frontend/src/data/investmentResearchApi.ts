import type { components } from "../contracts/v1";

type Schemas = components["schemas"];

export type ProductObjectSearch = Schemas["ProductObjectSearchResponse"];
export type ProductObjectSearchItem = Schemas["ProductObjectSearchItemResponse"];
export type IndustryCompanyBrowse = Schemas["IndustryCompanyBrowseResponse"];
export type IndustryCompanyBrowseItem = Schemas["IndustryCompanyBrowseItemResponse"];
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
export type EffectiveSecurityRights = Schemas["EffectiveSecurityRightsResponse"];
export type CompanyResearchPreview = Schemas["CompanyResearchPreviewResponse"];
export type CompanyResearchProject = Schemas["CompanyResearchProjectResponse"];
export type CompanyResearchPreviewRequest = Schemas["CompanyResearchPreviewRequest"];
export type InitializeCompanyResearchRequest = Schemas["InitializeCompanyResearchRequest"];
export type CompanyResearchWorkspace = Schemas["CompanyResearchWorkspaceResponse"];
export type ReviewCompanyEvidenceRequest = Schemas["ReviewCompanyEvidenceRequest"];
export type CompanyResearchEvidenceReview = Schemas["CompanyResearchEvidenceReviewResponse"];

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

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function assertUuid(value: string, fieldName: string): void {
  if (!isUuid(value)) {
    throw new InvestmentResearchRequestError(`${fieldName} 必须是有效 UUID`, 0, "invalid_request", null);
  }
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

function canonicalDecimal(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(value.trim());
  if (!match) return null;
  const integer = match[2].replace(/^0+(?=\d)/, "");
  const fraction = (match[3] ?? "").replace(/0+$/, "");
  const zero = integer === "0" && fraction === "";
  return `${match[1] === "-" && !zero ? "-" : ""}${integer}${fraction ? `.${fraction}` : ""}`;
}

function isDecimal(value: unknown): value is string {
  return canonicalDecimal(value) !== null;
}

function sameDecimal(actual: unknown, expected: unknown): boolean {
  const left = canonicalDecimal(actual);
  const right = canonicalDecimal(expected == null ? expected : String(expected));
  return left !== null && left === right;
}

function isNonNegativeDecimal(value: unknown, positive = false): value is string {
  const normalized = canonicalDecimal(value);
  return normalized !== null && !normalized.startsWith("-") && (!positive || normalized !== "0");
}

function decimalAtMostOne(value: unknown, allowOne = true): boolean {
  const normalized = canonicalDecimal(value);
  if (normalized === null || normalized.startsWith("-")) return false;
  if (normalized === "1") return allowOne;
  return normalized === "0" || normalized.startsWith("0.");
}

function compareNonNegativeDecimals(left: unknown, right: unknown): number {
  const leftValue = canonicalDecimal(left);
  const rightValue = canonicalDecimal(right);
  if (leftValue === null || rightValue === null || leftValue.startsWith("-") || rightValue.startsWith("-")) return -1;
  const [leftInteger, leftFraction = ""] = leftValue.split(".");
  const [rightInteger, rightFraction = ""] = rightValue.split(".");
  if (leftInteger.length !== rightInteger.length) return leftInteger.length > rightInteger.length ? 1 : -1;
  if (leftInteger !== rightInteger) return leftInteger > rightInteger ? 1 : -1;
  const length = Math.max(leftFraction.length, rightFraction.length);
  const paddedLeft = leftFraction.padEnd(length, "0");
  const paddedRight = rightFraction.padEnd(length, "0");
  return paddedLeft === paddedRight ? 0 : paddedLeft > paddedRight ? 1 : -1;
}

function sameInstant(actual: unknown, expected: unknown): boolean {
  return isDateTime(actual) && isDateTime(expected) && Date.parse(actual) === Date.parse(expected);
}

function isAtOrBefore(left: unknown, right: unknown): boolean {
  return isDateTime(left) && isDateTime(right) && Date.parse(left) <= Date.parse(right);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
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
  if (!value.every((ref) => isRecord(ref)
    && hasExactKeys(ref, ["membership_id", "security_id", "content_hash"])
    && isUuid(ref.membership_id) && isUuid(ref.security_id) && isHash(ref.content_hash))) return false;
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
    && hasExactKeys(value, ["schema_version", "object_id", "identity_version_id", "kind", "external_key", "canonical_name", "symbol", "exchange", "share_class", "trading_currency"])
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
  return isProductDto(value) && hasExactKeys(value, ["schema_version", "items"]) && Array.isArray(value.items) && value.items.every(isObjectItem);
}

function isIndustryCompanyBrowseItem(value: unknown): value is IndustryCompanyBrowseItem {
  if (!isProductDto(value)
    || !hasExactKeys(value, ["schema_version", "object_id", "kind", "external_key", "canonical_name", "symbol", "exchange", "share_class", "trading_currency"])
    || !isUuid(value.object_id)
    || !isNonEmptyString(value.external_key)
    || !isNonEmptyString(value.canonical_name)) return false;
  if (value.kind === "company") {
    return value.symbol === null && value.exchange === null && value.share_class === null && value.trading_currency === null;
  }
  return value.kind === "security"
    && isNonEmptyString(value.symbol)
    && isNonEmptyString(value.exchange)
    && isNonEmptyString(value.share_class)
    && (value.trading_currency === "CNY" || value.trading_currency === "USD");
}

function isIndustryCompanyBrowse(value: unknown): value is IndustryCompanyBrowse {
  if (!isProductDto(value) || !hasExactKeys(value, ["schema_version", "industry_id", "items"])
    || !isUuid(value.industry_id)
    || !Array.isArray(value.items) || !value.items.every(isIndustryCompanyBrowseItem)) return false;
  const objectIds = value.items.map((item) => item.object_id);
  if (new Set(objectIds).size !== objectIds.length) return false;
  let hasAnchor = false;
  for (const item of value.items) {
    if (item.kind === "company") {
      hasAnchor = true;
    } else {
      if (!hasAnchor) return false;
    }
  }
  return true;
}

function isProject(value: unknown): value is ProductProject {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "id", "primary_company_id", "target_security_ids", "company_identity", "security_identities", "content_hash", "created_at"])
    && isUuid(value.id)
    && isUuid(value.primary_company_id)
    && isUniqueUuidArray(value.target_security_ids, true)
    && isProjectCompanyIdentity(value.company_identity)
    && value.company_identity.object_id === value.primary_company_id
    && Array.isArray(value.security_identities)
    && value.security_identities.every(isProjectSecurityIdentity)
    && new Set(value.security_identities.map((item) => item.identity_version_id)).size === value.security_identities.length
    && sameStringSets(value.security_identities.map((item) => item.object_id), value.target_security_ids)
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isProjectCompanyIdentity(value: unknown): value is ProductProject["company_identity"] {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "object_id", "identity_version_id", "canonical_name"])
    && isUuid(value.object_id) && isUuid(value.identity_version_id) && isNonEmptyString(value.canonical_name);
}

function isProjectSecurityIdentity(value: unknown): value is ProductProject["security_identities"][number] {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "object_id", "identity_version_id", "canonical_name", "symbol", "exchange", "share_class", "trading_currency"])
    && isUuid(value.object_id) && isUuid(value.identity_version_id)
    && isNonEmptyString(value.canonical_name) && isNonEmptyString(value.symbol)
    && isNonEmptyString(value.exchange) && isNonEmptyString(value.share_class)
    && (value.trading_currency === "CNY" || value.trading_currency === "USD");
}

function isProjectList(value: unknown): value is ProductProjectList {
  return isProductDto(value) && hasExactKeys(value, ["schema_version", "items"]) && Array.isArray(value.items) && value.items.every(isProject);
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
    && hasExactKeys(value, ["schema_version", "id", "project_id", "mandate_key", "horizon_years", "base_currency", "required_return", "permanent_loss_limit", "comparison_set", "benchmark_key", "required_excess_return", "effective_at", "expires_at", "version", "supersedes_id", "content_hash", "created_at"])
    && typeof value.mandate_key === "string" && value.mandate_key.trim() !== ""
    && typeof value.horizon_years === "number" && Number.isInteger(value.horizon_years) && value.horizon_years >= 3 && value.horizon_years <= 5
    && (value.base_currency === "CNY" || value.base_currency === "USD")
    && decimalAtMostOne(value.required_return, false)
    && decimalAtMostOne(value.permanent_loss_limit)
    && isNonEmptyStrings(value.comparison_set)
    && isNullableString(value.benchmark_key)
    && (value.required_excess_return === null || decimalAtMostOne(value.required_excess_return, false))
    && ((value.benchmark_key === null) === (value.required_excess_return === null))
    && isDateTime(value.effective_at)
    && isNullableDateTime(value.expires_at)
    && (value.expires_at === null || isAtOrBefore(value.effective_at, value.expires_at))
    && isPositiveInteger(value.version)
    && isNullableUuid(value.supersedes_id)
    && isHash(value.content_hash)
    && isDateTime(value.created_at);
}

function isScope(value: unknown): value is ProductScope {
  return isProjectChildRecord(value)
    && hasExactKeys(value, ["schema_version", "id", "project_id", "version", "payload", "supersedes_id", "content_hash", "created_at"])
    && isPositiveInteger(value.version)
    && isProductDto(value.payload)
    && hasExactKeys(value.payload, ["schema_version", "primary_company_id", "target_security_ids", "industry_ids", "covered_segments", "user_focus", "exclusions"])
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
  return hasExactKeys(value, ["schema_version", "id", "project_id", "version", "scope_id", "payload", "generator_provenance", "supersedes_id", "content_hash", "created_at"])
    && hasExactKeys(value.payload, ["schema_version", "items"])
    && hasExactKeys(generator, ["schema_version", "method", "template_key", "template_version", "model_name", "prompt_template_version", "input_summary_hash", "output_hash"])
    && isPositiveInteger(value.version)
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
      && isHash(generator.input_summary_hash);
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
    && hasExactKeys(value, ["schema_version", "id", "cutoff_at", "price_as_of", "source_manifest_hash", "definition_bundle_hash", "parser_bundle_hash", "boundary_schema_version", "content_hash", "created_at"])
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
    && hasExactKeys(value, ["schema_version", "id", "security_identity_id", "price", "currency", "price_type", "adjustment_basis", "market_at", "available_at", "source_id", "raw_hash", "content_hash", "created_at"])
    && isUuid(value.security_identity_id)
    && isNonNegativeDecimal(value.price, true)
    && (value.currency === "CNY" || value.currency === "USD")
    && typeof value.price_type === "string" && value.price_type.trim() !== ""
    && typeof value.adjustment_basis === "string" && value.adjustment_basis.trim() !== ""
    && isDateTime(value.market_at) && isDateTime(value.available_at) && isAtOrBefore(value.market_at, value.available_at)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isFx(value: unknown): value is ProductFxSnapshot {
  return isIdentifiedRecord(value)
    && hasExactKeys(value, ["schema_version", "id", "base_currency", "quote_currency", "rate", "quote_direction", "market_at", "available_at", "source_id", "raw_hash", "content_hash", "created_at"])
    && (value.base_currency === "CNY" || value.base_currency === "USD")
    && (value.quote_currency === "CNY" || value.quote_currency === "USD")
    && value.base_currency !== value.quote_currency
    && isNonNegativeDecimal(value.rate, true)
    && value.quote_direction === "quote_per_base"
    && isDateTime(value.market_at) && isDateTime(value.available_at) && isAtOrBefore(value.market_at, value.available_at)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isCapital(value: unknown): value is ProductCapitalStructure {
  return isIdentifiedRecord(value)
    && hasExactKeys(value, ["schema_version", "id", "company_id", "currency", "cash", "debt", "minority_interest", "investments", "pension_liabilities", "other_adjustments", "basic_shares", "diluted_shares", "potential_dilution_descriptors", "report_period_start", "report_period_end", "market_at", "available_at", "source_id", "raw_hash", "content_hash", "created_at"])
    && isUuid(value.company_id)
    && (value.currency === "CNY" || value.currency === "USD")
    && isNonNegativeDecimal(value.cash) && isNonNegativeDecimal(value.debt) && isNonNegativeDecimal(value.minority_interest)
    && isNonNegativeDecimal(value.investments) && isNonNegativeDecimal(value.pension_liabilities)
    && isDecimal(value.other_adjustments) && isNonNegativeDecimal(value.basic_shares, true) && isNonNegativeDecimal(value.diluted_shares, true)
    && compareNonNegativeDecimals(value.diluted_shares, value.basic_shares) >= 0
    && isStringArray(value.potential_dilution_descriptors)
    && isDateTime(value.report_period_start) && isDateTime(value.report_period_end) && isAtOrBefore(value.report_period_start, value.report_period_end)
    && isDateTime(value.market_at) && isDateTime(value.available_at) && isAtOrBefore(value.market_at, value.available_at)
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isRights(value: unknown): value is ProductSecurityRights {
  return isIdentifiedRecord(value)
    && hasExactKeys(value, ["schema_version", "id", "security_identity_id", "version", "economic_units", "votes_per_unit", "conversion_ratio", "adr_ratio", "dividend_rights_per_unit", "effective_from", "effective_to", "source_id", "raw_hash", "supersedes_id", "content_hash", "created_at"])
    && isUuid(value.security_identity_id)
    && isPositiveInteger(value.version)
    && isNonNegativeDecimal(value.economic_units, true) && isNonNegativeDecimal(value.votes_per_unit)
    && isNonNegativeDecimal(value.conversion_ratio, true) && isNonNegativeDecimal(value.adr_ratio, true)
    && isNonNegativeDecimal(value.dividend_rights_per_unit)
    && isDateTime(value.effective_from) && isNullableDateTime(value.effective_to)
    && (value.effective_to === null || Date.parse(value.effective_from) < Date.parse(value.effective_to))
    && typeof value.source_id === "string" && value.source_id.trim() !== ""
    && isHash(value.raw_hash) && isNullableUuid(value.supersedes_id)
    && isHash(value.content_hash) && isDateTime(value.created_at);
}

function isEffectiveRights(value: unknown): value is EffectiveSecurityRights {
  if (!isProductDto(value)
    || !hasExactKeys(value, ["schema_version", "security_identity_id", "as_of", "effective", "head", "append_allowed", "expected_parent_id", "minimum_effective_from", "reason"])
    || !isUuid(value.security_identity_id) || !isDateTime(value.as_of)
    || !(value.effective === null || isRights(value.effective))
    || !(value.head === null || (isProductDto(value.head)
      && hasExactKeys(value.head, ["schema_version", "id", "effective_from", "effective_to"])
      && isUuid(value.head.id) && isDateTime(value.head.effective_from)
      && isNullableDateTime(value.head.effective_to)
      && (value.head.effective_to === null
        || Date.parse(String(value.head.effective_from)) < Date.parse(String(value.head.effective_to)))))
    || typeof value.append_allowed !== "boolean"
    || !isNullableUuid(value.expected_parent_id)
    || !isNullableDateTime(value.minimum_effective_from)
    || !isProductDto(value.reason)
    || !hasExactKeys(value.reason, ["schema_version", "code", "action"])) return false;

  if (value.effective !== null && (
    value.effective.security_identity_id !== value.security_identity_id
    || !isAtOrBefore(value.effective.effective_from, value.as_of)
    || (value.effective.effective_to !== null
      && Date.parse(value.as_of) >= Date.parse(value.effective.effective_to))
  )) return false;

  const state = `${String(value.reason.code)}:${String(value.reason.action)}`;
  if (state === "effective_version_found:reuse_effective") {
    if (value.effective === null || value.head === null || value.append_allowed
      || value.expected_parent_id !== null || value.minimum_effective_from !== null) return false;
    if (value.head.id === value.effective.id) {
      return sameInstant(value.head.effective_from, value.effective.effective_from)
        && sameNullableInstant(value.head.effective_to, value.effective.effective_to);
    }
    const headFrom = Date.parse(String(value.head.effective_from));
    return headFrom > Date.parse(String(value.effective.effective_from))
      && Date.parse(value.as_of) < headFrom
      && (value.effective.effective_to === null
        || isAtOrBefore(value.effective.effective_to, value.head.effective_from));
  }
  if (state === "no_history:create_initial") {
    return value.effective === null && value.head === null && value.append_allowed
      && value.expected_parent_id === null && value.minimum_effective_from === null;
  }
  if (state === "before_head:adjust_market_at") {
    return value.effective === null && value.head !== null && !value.append_allowed
      && value.expected_parent_id === null && value.minimum_effective_from === null
      && Date.parse(value.as_of) < Date.parse(String(value.head.effective_from));
  }
  if (state === "successor_required:append_successor") {
    return value.effective === null && value.head !== null && value.append_allowed
      && value.head.effective_to !== null
      && value.expected_parent_id === value.head.id && value.minimum_effective_from !== null
      && sameInstant(value.minimum_effective_from, value.head.effective_to)
      && isAtOrBefore(value.minimum_effective_from, value.as_of);
  }
  return false;
}

function isDraftContent(value: unknown): boolean {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "publication_status", "mandate_id", "scope_id", "agenda_id", "historical_basis_id", "price_snapshot_ids", "fx_snapshot_ids", "capital_structure_snapshot_id", "security_rights_ids", "user_focus"])
    && value.publication_status === "draft"
    && isNullableUuid(value.mandate_id)
    && isNullableUuid(value.scope_id)
    && isNullableUuid(value.agenda_id)
    && isNullableUuid(value.historical_basis_id)
    && isUniqueUuidArray(value.price_snapshot_ids)
    && isUniqueUuidArray(value.fx_snapshot_ids)
    && isNullableUuid(value.capital_structure_snapshot_id)
    && isUniqueUuidArray(value.security_rights_ids)
    && isNullableString(value.user_focus);
}

function isDraft(value: unknown): value is ProductDraft {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "id", "project_id", "base_revision_id", "lock_version", "content", "created_at", "updated_at"])
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
    && hasExactKeys(value, ["schema_version", "project_id", "expected_lock_version", "boundary_as_of", "assessment", "boundary", "boundary_hash", "manifest", "manifest_hash"])
    && isUuid(value.project_id)
    && isNonNegativeInteger(value.expected_lock_version)
    && isDateTime(value.boundary_as_of)
    && isProductDto(value.assessment)
    && hasExactKeys(value.assessment, ["schema_version", "answerability", "direction", "confidence", "publication_status", "blockers", "resolution_requirements", "next_review_at", "parent_assessment_id", "content_hash"])
    && hasConsistentAssessment(value.assessment)
    && (value.assessment.publication_status === "user_frozen" || value.assessment.publication_status === "superseded")
    && isStringArray(value.assessment.blockers)
    && isStringArray(value.assessment.resolution_requirements)
    && isNullableDateTime(value.assessment.next_review_at)
    && isNullableUuid(value.assessment.parent_assessment_id)
    && isHash(value.assessment.content_hash)
    && isProductDto(value.boundary)
    && hasExactKeys(value.boundary, ["schema_version", "historical_basis_id", "mandate_id", "scope_id", "agenda_id", "price_snapshot_ids", "fx_snapshot_ids", "capital_structure_snapshot_id", "security_rights_ids", "parent_revision_id"])
    && isUuid(value.boundary.historical_basis_id)
    && isUuid(value.boundary.mandate_id) && isUuid(value.boundary.scope_id) && isUuid(value.boundary.agenda_id)
    && isUniqueUuidArray(value.boundary.price_snapshot_ids, true) && isUniqueUuidArray(value.boundary.fx_snapshot_ids)
    && isUuid(value.boundary.capital_structure_snapshot_id) && isUniqueUuidArray(value.boundary.security_rights_ids, true)
    && isNullableUuid(value.boundary.parent_revision_id)
    && isHash(value.boundary_hash)
    && isRecord(value.manifest)
    && hasExactKeys(value.manifest, ["schema_version", "project_id", "project_ref", "project_membership_refs", "primary_object_id", "boundary_ref", "mandate_id", "scope_id", "agenda_id", "historical_basis_id", "price_snapshot_ids", "fx_snapshot_ids", "capital_structure_snapshot_id", "security_rights_ids", "market_snapshot_refs", "model_refs", "assessment_ref", "memo_ref", "parent_revision_id"])
    && value.manifest.schema_version === "underwriting.research-revision-manifest.v1"
    && value.manifest.project_id === value.project_id
    && isRecord(value.manifest.project_ref)
    && hasExactKeys(value.manifest.project_ref, ["project_id", "content_hash"])
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
    && hasExactKeys(value, ["schema_version", "id", "project_id", "object_id", "basis_id", "boundary_id", "manifest_id", "version_kind", "sequence", "content_hash", "cutoff", "source_manifest_hash", "manifest_hash", "parent_revision_id", "price_snapshot_ids", "fx_snapshot_ids", "capital_structure_snapshot_id", "security_rights_ids", "market_snapshot_ids", "answerability", "direction", "confidence", "publication_status"])
    && isUuid(value.id)
    && isUuid(value.project_id)
    && isUuid(value.object_id) && isUuid(value.basis_id) && isUuid(value.boundary_id) && isUuid(value.manifest_id)
    && value.version_kind === "independent_research"
    && isPositiveInteger(value.sequence)
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

const COMPANY_RESEARCH_AGENDA_KEYS = [
  "overview",
  "business_map",
  "operating_drivers",
  "evidence_and_gaps",
  "industry_competition_regulation",
  "financials_cash_flow_capital_allocation",
  "scenarios_valuation_implied_expectations",
  "counterevidence_risks_next_checks",
  "versions_changes_memo",
] as const;

const COMPANY_RESEARCH_STEPS = [
  "evidence_index",
  "business_map",
  "driver_map",
  "financial_bridge",
  "scenario_set",
  "valuation_set",
  "research_gaps",
  "judgment_context",
  "memo",
] as const;
const COMPANY_RESEARCH_PREPARATION_STEPS = [...COMPANY_RESEARCH_STEPS, "model_bundle"] as const;

function isCompanyResearchIdentity(value: unknown): boolean {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "object_id", "external_key", "canonical_name"])
    && isUuid(value.object_id) && isNonEmptyString(value.external_key)
    && isNonEmptyString(value.canonical_name);
}

function isCompanyResearchSecurity(value: unknown): boolean {
  return isProductDto(value)
    && hasExactKeys(value, ["schema_version", "object_id", "external_key", "canonical_name", "symbol", "exchange", "share_class", "trading_currency"])
    && isUuid(value.object_id) && isNonEmptyString(value.external_key)
    && isNonEmptyString(value.canonical_name)
    && isNonEmptyString(value.symbol) && isNonEmptyString(value.exchange)
    && isNonEmptyString(value.share_class)
    && (value.trading_currency === "CNY" || value.trading_currency === "USD");
}

function isCompanyResearchPreview(value: unknown): value is CompanyResearchPreview {
  if (!isProductDto(value)
    || !hasExactKeys(value, ["schema_version", "company", "securities", "strategy_version", "horizon_years", "base_currency", "required_return", "permanent_loss_limit", "cutoff_at", "agenda", "preview_hash"])
    || !isCompanyResearchIdentity(value.company)
    || !Array.isArray(value.securities) || value.securities.length === 0
    || !value.securities.every(isCompanyResearchSecurity)
    || new Set(value.securities.map((security) => security.object_id)).size !== value.securities.length
    || new Set(value.securities.map((security) => security.external_key)).size !== value.securities.length
    || value.strategy_version !== "company-research-default.v1"
    || value.horizon_years !== 5 || value.base_currency !== "CNY"
    || !sameDecimal(value.required_return, "0.12")
    || !sameDecimal(value.permanent_loss_limit, "0.25")
    || !isDateTime(value.cutoff_at) || !isHash(value.preview_hash)
    || !Array.isArray(value.agenda) || value.agenda.length !== COMPANY_RESEARCH_AGENDA_KEYS.length) return false;
  if (!value.agenda.every((module) => isProductDto(module)
    && hasExactKeys(module, ["schema_version", "key", "label"])
    && isNonEmptyString(module.key) && isNonEmptyString(module.label))) return false;
  return sameStringSets(
    value.agenda.map((module) => module.key),
    [...COMPANY_RESEARCH_AGENDA_KEYS],
  );
}

function isPreparationStateAndStep(value: Record<string, unknown>): boolean {
  const step = value.current_step;
  if (value.status === "queued") {
    return typeof step === "string" && COMPANY_RESEARCH_STEPS.includes(step as typeof COMPANY_RESEARCH_STEPS[number]) && value.progress === 0
      && value.next_attempt_at === null && value.last_error_code === null;
  }
  if (value.status === "completed") {
    return step === null && value.progress === 100
      && value.next_attempt_at === null && value.last_error_code === null;
  }
  if (value.status === "preparing_sources" || value.status === "awaiting_evidence_review") {
    return step === (value.status === "awaiting_evidence_review" ? "research_gaps" : "evidence_index")
      && value.last_error_code === null;
  }
  if (value.status === "building_model") {
    return typeof step === "string" && COMPANY_RESEARCH_PREPARATION_STEPS.includes(step as typeof COMPANY_RESEARCH_PREPARATION_STEPS[number])
      && step !== "evidence_index" && value.last_error_code === null;
  }
  if (value.status === "awaiting_judgment_review") return step === "judgment_context" && value.last_error_code === null;
  if (value.status === "ready_to_freeze") return step === "memo" && value.last_error_code === null;
  if (value.status === "recoverable_failure" || value.status === "blocked") {
    return typeof step === "string" && COMPANY_RESEARCH_PREPARATION_STEPS.includes(step as typeof COMPANY_RESEARCH_PREPARATION_STEPS[number])
      && isNonEmptyString(value.last_error_code);
  }
  return false;
}

function isCompanyResearchProject(value: unknown): value is CompanyResearchProject {
  if (!isProductDto(value)
    || !hasExactKeys(value, ["schema_version", "project_id", "company_id", "preparation"])
    || !isUuid(value.project_id) || !isUuid(value.company_id)
    || !isProductDto(value.preparation)
    || !hasExactKeys(value.preparation, ["schema_version", "id", "project_id", "request_hash", "strategy_version", "status", "current_step", "progress", "attempt", "next_attempt_at", "last_error_code"])
    || !isUuid(value.preparation.id) || value.preparation.project_id !== value.project_id
    || !isHash(value.preparation.request_hash)
    || value.preparation.strategy_version !== "company-research-default.v1"
    || !isNonNegativeInteger(value.preparation.progress)
    || value.preparation.progress > 100 || !isPositiveInteger(value.preparation.attempt)
    || !isNullableDateTime(value.preparation.next_attempt_at)
    || !isNullableString(value.preparation.last_error_code)) return false;
  return isPreparationStateAndStep(value.preparation);
}

const COMPANY_RESEARCH_ARTIFACT_KINDS = new Set(COMPANY_RESEARCH_STEPS);

function isCanonicalDecimal(value: unknown): value is string {
  return typeof value === "string" && canonicalDecimal(value) === value;
}

function isDateOnly(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!parts) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime())
    && date.getUTCFullYear() === Number(parts[1])
    && date.getUTCMonth() + 1 === Number(parts[2])
    && date.getUTCDate() === Number(parts[3]);
}

const SOURCE_REF_KEYS = ["raw_hash", "source_locator", "source_role", "source_url"] as const;
const LINEAGE_SOURCE_REF_KEYS = [...SOURCE_REF_KEYS, "fact_key"] as const;

function isCompanyResearchSourceRef(value: unknown, withFactKey = false): value is Record<string, unknown> {
  if (!isRecord(value)
    || !hasExactKeys(value, withFactKey ? LINEAGE_SOURCE_REF_KEYS : SOURCE_REF_KEYS)
    || !isHash(value.raw_hash) || !isNonEmptyString(value.source_locator)
    || !isNonEmptyString(value.source_role) || !isNonEmptyString(value.source_url)) return false;
  return !withFactKey || isNonEmptyString(value.fact_key);
}

function sourceRefIdentity(value: Record<string, unknown>): string {
  return JSON.stringify([value.source_role, value.source_url, value.source_locator, value.raw_hash]);
}

const ARTIFACT_PARENT_KEYS = ["artifact_id", "artifact_kind", "content_hash"] as const;

function isArtifactParentRef(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && hasExactKeys(value, ARTIFACT_PARENT_KEYS)
    && isUuid(value.artifact_id) && typeof value.artifact_kind === "string"
    && COMPANY_RESEARCH_ARTIFACT_KINDS.has(value.artifact_kind as typeof COMPANY_RESEARCH_STEPS[number])
    && isHash(value.content_hash);
}

function isNumericSource(value: unknown): boolean {
  if (!isRecord(value) || typeof value.kind !== "string") return false;
  if (value.kind === "external") {
    return hasExactKeys(value, ["kind", ...LINEAGE_SOURCE_REF_KEYS])
      && isCompanyResearchSourceRef(Object.fromEntries(Object.entries(value).filter(([key]) => key !== "kind")), true);
  }
  return value.kind === "artifact_computation"
    && hasExactKeys(value, ["kind", "artifact_refs", "market_snapshot_ids", "equation_id"])
    && Array.isArray(value.artifact_refs) && value.artifact_refs.length > 0
    && value.artifact_refs.every(isArtifactParentRef)
    && new Set(value.artifact_refs.map((ref) => ref.artifact_id)).size === value.artifact_refs.length
    && isUuidArray(value.market_snapshot_ids)
    && new Set(value.market_snapshot_ids).size === value.market_snapshot_ids.length
    && isNonEmptyString(value.equation_id);
}

function isNumericObservation(value: unknown): boolean {
  if (!isRecord(value)
    || !hasExactKeys(value, ["key", "value", "unit", "currency", "period", "state", "source_ref", "gap_key", "assumption_key"])
    || !isNonEmptyString(value.key) || !isCanonicalDecimal(value.value)
    || !isNonEmptyString(value.unit) || !isNonEmptyString(value.currency)
    || !isNonEmptyString(value.period)
    || !["reported", "derived", "assumption", "gap"].includes(String(value.state))) return false;
  const paths = [value.source_ref, value.gap_key, value.assumption_key]
    .filter((item) => item !== null).length;
  if (paths !== 1) return false;
  if (value.state === "reported") return isRecord(value.source_ref) && value.source_ref.kind === "external" && isNumericSource(value.source_ref);
  if (value.state === "derived") return isRecord(value.source_ref)
    && value.source_ref.kind === "artifact_computation" && isNumericSource(value.source_ref);
  if (value.state === "assumption") return isNonEmptyString(value.assumption_key);
  return isNonEmptyString(value.gap_key);
}

function isCompanyResearchLineage(
  value: unknown,
  expectedParents: readonly string[],
  requiresMarket = false,
): boolean {
  if (!isRecord(value)
    || !hasExactKeys(value, ["artifact_refs", "market_snapshot_ids", "market_snapshot_bindings"])
    || !Array.isArray(value.artifact_refs)
    || !value.artifact_refs.every(isArtifactParentRef)
    || new Set(value.artifact_refs.map((ref) => isRecord(ref) ? ref.artifact_id : null)).size !== value.artifact_refs.length
    || !sameOrderedStrings(
      value.artifact_refs.map((ref) => isRecord(ref) && typeof ref.artifact_kind === "string" ? ref.artifact_kind : ""),
      [...expectedParents],
    )
    || !isUuidArray(value.market_snapshot_ids)
    || new Set(value.market_snapshot_ids).size !== value.market_snapshot_ids.length
    || !Array.isArray(value.market_snapshot_bindings)
    || value.market_snapshot_bindings.length !== value.market_snapshot_ids.length) return false;
  const snapshotIds = value.market_snapshot_ids as string[];
  const bindings = value.market_snapshot_bindings;
  if (!bindings.every((binding, index) => isRecord(binding)
    && hasExactKeys(binding, ["snapshot_id", "snapshot_kind", "snapshot_content_hash", "security_external_key", "source_ref", "capture_envelope_id", "capture_content_hash", "provenance_role", "provider_policy_version", "raw_components"])
    && binding.snapshot_id === snapshotIds[index]
    && ["price", "fx", "capital_structure", "security_rights"].includes(String(binding.snapshot_kind))
    && isHash(binding.snapshot_content_hash)
    && (binding.security_external_key === null || isNonEmptyString(binding.security_external_key))
    && isCompanyResearchSourceRef(binding.source_ref, true)
    && isUuid(binding.capture_envelope_id) && isHash(binding.capture_content_hash)
    && binding.provenance_role === "primary" && isNonEmptyString(binding.provider_policy_version)
    && Array.isArray(binding.raw_components)
    && binding.raw_components.every((component) => isRecord(component)
      && hasExactKeys(component, ["raw_file", "raw_hash", "source_url", "source_locator"])
      && isNonEmptyString(component.raw_file) && isHash(component.raw_hash)
      && isNonEmptyString(component.source_url) && isNonEmptyString(component.source_locator)))) return false;
  if (!requiresMarket) return true;
  const roles = new Set(bindings.map((binding) => isRecord(binding) ? binding.snapshot_kind : null));
  return snapshotIds.length > 0
    && ["price", "fx", "capital_structure", "security_rights"].every((role) => roles.has(role));
}

function isEvidenceFact(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  const keys = ["fact_key", "company_external_key", "business_module", "metric_key", "observation", "period_start", "period_end", "published_at", "available_at", "source_role", "source_url", "source_locator", "raw_hash"];
  return (hasExactKeys(value, keys) || hasExactKeys(value, [...keys, "review_decision"]))
    && isNonEmptyString(value.fact_key) && isNonEmptyString(value.company_external_key)
    && isNonEmptyString(value.business_module) && isNonEmptyString(value.metric_key)
    && isNumericObservation(value.observation)
    && isDateOnly(value.period_start) && isDateOnly(value.period_end)
    && value.period_start <= value.period_end
    && isDateTime(value.published_at) && isDateTime(value.available_at)
    && Date.parse(value.published_at) <= Date.parse(value.available_at)
    && isNonEmptyString(value.source_role) && isNonEmptyString(value.source_url)
    && isNonEmptyString(value.source_locator) && isHash(value.raw_hash)
    && (!("review_decision" in value) || value.review_decision === "confirmed" || value.review_decision === "rejected");
}

function isEvidencePayload(value: unknown, sourceRefs: Record<string, unknown>[]): boolean {
  if (!isRecord(value)
    || !hasExactKeys(value, ["fixture_content_hash", "cutoff", "company_external_key", "security_external_keys", "facts"])
    || !isHash(value.fixture_content_hash) || !isDateTime(value.cutoff)
    || !isNonEmptyString(value.company_external_key) || !isNonEmptyStrings(value.security_external_keys)
    || new Set(value.security_external_keys).size !== value.security_external_keys.length
    || !Array.isArray(value.facts) || value.facts.length === 0 || !value.facts.every(isEvidenceFact)) return false;
  const keys = value.facts.map((fact) => isRecord(fact) ? fact.fact_key : null);
  if (new Set(keys).size !== value.facts.length) return false;
  const parents = new Set(sourceRefs.map(sourceRefIdentity));
  return value.facts.every((fact) => {
    if (!isRecord(fact) || !isRecord(fact.observation) || !isRecord(fact.observation.source_ref)) return false;
    const external = { ...fact.observation.source_ref };
    delete external.kind;
    return parents.has(sourceRefIdentity(external));
  });
}

function isResearchGapsPayload(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.gaps)) return false;
  if (hasExactKeys(value, ["fixture_content_hash", "company_external_key", "gaps"])) {
    return isHash(value.fixture_content_hash) && isNonEmptyString(value.company_external_key)
      && value.gaps.every((gap) => isRecord(gap)
        && hasExactKeys(gap, ["gap_key", "business_module", "reason"])
        && isNonEmptyString(gap.gap_key) && isNonEmptyString(gap.business_module) && isNonEmptyString(gap.reason));
  }
  if (!hasExactKeys(value, ["gaps", "_lineage"])) return false;
  const parents = isRecord(value._lineage) && Array.isArray(value._lineage.artifact_refs)
    ? value._lineage.artifact_refs.map((ref) => isRecord(ref) ? String(ref.artifact_kind) : "") : [];
  const expected = parents.includes("valuation_set")
    ? ["evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set", "valuation_set"]
    : ["evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set"];
  return isCompanyResearchLineage(value._lineage, expected)
    && value.gaps.every((gap) => isRecord(gap)
      && hasExactKeys(gap, ["code", "module_key", "severity", "message"])
      && isNonEmptyString(gap.code) && isNonEmptyString(gap.module_key)
      && ["low", "medium", "high", "critical"].includes(String(gap.severity))
      && isNonEmptyString(gap.message));
}

function isBusinessMapPayload(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.modules) || value.modules.length === 0) return false;
  if (hasExactKeys(value, ["evidence_index_id", "evidence_content_hash", "modules"])) {
    return isUuid(value.evidence_index_id) && isHash(value.evidence_content_hash)
      && value.modules.every((module) => isRecord(module)
        && hasExactKeys(module, ["key", "fact_keys"])
        && isNonEmptyString(module.key) && isNonEmptyStrings(module.fact_keys));
  }
  return hasExactKeys(value, ["modules", "_lineage"])
    && isCompanyResearchLineage(value._lineage, ["evidence_index"])
    && value.modules.every((module) => isRecord(module)
      && hasExactKeys(module, ["module_key", "revenue_sources", "cost_structure", "capital_needs", "fact_refs", "gap_refs", "classified_evidence"])
      && isNonEmptyString(module.module_key) && isNonEmptyStrings(module.revenue_sources)
      && isNonEmptyStrings(module.cost_structure) && isNonEmptyStrings(module.capital_needs)
      && Array.isArray(module.fact_refs) && module.fact_refs.every((ref) => isCompanyResearchSourceRef(ref, true))
      && isStringArray(module.gap_refs) && Array.isArray(module.classified_evidence)
      && module.classified_evidence.every((observation) => isRecord(observation)
        && hasExactKeys(observation, ["fact_ref", "metric_key", "category", "observation", "period_start", "period_end"])
        && isCompanyResearchSourceRef(observation.fact_ref, true) && isNonEmptyString(observation.metric_key)
        && ["revenue", "cost", "capital"].includes(String(observation.category))
        && isNumericObservation(observation.observation) && isDateOnly(observation.period_start)
        && isDateOnly(observation.period_end) && observation.period_start <= observation.period_end));
}

function isDriverMapPayload(value: unknown): boolean {
  return isRecord(value) && hasExactKeys(value, ["drivers", "_lineage"])
    && isCompanyResearchLineage(value._lineage, ["business_map"])
    && Array.isArray(value.drivers) && value.drivers.length > 0
    && value.drivers.every((driver) => isRecord(driver)
      && hasExactKeys(driver, ["driver_key", "module_key", "fact_refs", "assumption_refs", "equation", "output_metric", "equation_id", "values", "assumption_rationale", "assumption_equation"])
      && isNonEmptyString(driver.driver_key) && isNonEmptyString(driver.module_key)
      && Array.isArray(driver.fact_refs) && driver.fact_refs.every((ref) => isCompanyResearchSourceRef(ref, true))
      && Array.isArray(driver.assumption_refs) && driver.assumption_refs.every((ref) => isCompanyResearchSourceRef(ref, true))
      && isNonEmptyString(driver.equation) && isNonEmptyString(driver.output_metric)
      && (driver.equation_id === null || isNonEmptyString(driver.equation_id))
      && Array.isArray(driver.values) && driver.values.length > 0 && driver.values.every(isNumericObservation)
      && (driver.assumption_rationale === null || isNonEmptyString(driver.assumption_rationale))
      && (driver.assumption_equation === null || isNonEmptyString(driver.assumption_equation)));
}

function isFinancialBridgePayload(value: unknown): boolean {
  const observationKeys = ["revenue", "operating_income", "cash_tax_rate", "depreciation", "capex", "working_capital_change", "fcff"];
  return isRecord(value) && hasExactKeys(value, ["rows", "_lineage"])
    && isCompanyResearchLineage(value._lineage, ["driver_map"])
    && Array.isArray(value.rows) && value.rows.length === 5
    && value.rows.every((row) => isRecord(row)
      && hasExactKeys(row, ["period", ...observationKeys, "fact_refs", "assumption_refs"])
      && isNonEmptyString(row.period) && observationKeys.every((key) => isNumericObservation(row[key]))
      && Array.isArray(row.fact_refs) && row.fact_refs.every((ref) => isCompanyResearchSourceRef(ref, true))
      && Array.isArray(row.assumption_refs) && row.assumption_refs.every((ref) => isCompanyResearchSourceRef(ref, true)));
}

function isScenarioSetPayload(value: unknown): boolean {
  if (!isRecord(value) || !hasExactKeys(value, ["scenarios", "_lineage"])
    || !isCompanyResearchLineage(value._lineage, ["driver_map"])
    || !Array.isArray(value.scenarios) || value.scenarios.length !== 3) return false;
  const ids = value.scenarios.map((scenario) => isRecord(scenario) ? scenario.scenario_id : null);
  const mechanisms = value.scenarios.map((scenario) => isRecord(scenario) ? scenario.mechanism_id : null);
  return sameStringSets(ids.filter(isNonEmptyString), ["base", "bull", "bear"])
    && new Set(mechanisms).size === 3
    && value.scenarios.every((scenario) => isRecord(scenario)
      && hasExactKeys(scenario, ["scenario_id", "mechanism_id", "driver_overrides"])
      && isNonEmptyString(scenario.mechanism_id) && Array.isArray(scenario.driver_overrides)
      && scenario.driver_overrides.length > 0
      && scenario.driver_overrides.every((override) => isRecord(override)
        && hasExactKeys(override, ["driver_key", "observation", "rationale", "equation"])
        && isNonEmptyString(override.driver_key) && isNumericObservation(override.observation)
        && isRecord(override.observation) && override.observation.unit === "multiplier"
        && override.observation.currency === "N/A"
        && (override.rationale === null || isNonEmptyString(override.rationale))
        && (override.equation === null || isNonEmptyString(override.equation))));
}

function isValueRange(value: unknown): boolean {
  return isRecord(value) && hasExactKeys(value, ["minimum", "maximum"])
    && isNumericObservation(value.minimum) && isNumericObservation(value.maximum);
}

function isValuationSetPayload(value: unknown): boolean {
  if (!isRecord(value)
    || !hasExactKeys(value, ["scenario_dcf_values", "reverse_dcf", "security_value_ranges", "required_return", "required_return_comparisons", "_lineage"])
    || !isCompanyResearchLineage(value._lineage, ["scenario_set", "financial_bridge"], true)
    || !Array.isArray(value.scenario_dcf_values) || value.scenario_dcf_values.length !== 3
    || !value.scenario_dcf_values.every((item) => isRecord(item)
      && hasExactKeys(item, ["scenario_id", "enterprise_value"])
      && ["base", "bull", "bear"].includes(String(item.scenario_id)) && isNumericObservation(item.enterprise_value))
    || !(value.reverse_dcf === null || isRecord(value.reverse_dcf)
      && hasExactKeys(value.reverse_dcf, ["driver_key", "implied_value", "achieved_residual", "iteration_count"])
      && value.reverse_dcf.driver_key === "fcff_multiplier"
      && isNumericObservation(value.reverse_dcf.implied_value) && isNumericObservation(value.reverse_dcf.achieved_residual)
      && isNumericObservation(value.reverse_dcf.iteration_count))
    || !Array.isArray(value.security_value_ranges) || value.security_value_ranges.length === 0
    || !value.security_value_ranges.every((item) => isRecord(item)
      && hasExactKeys(item, ["security_external_key", "usd_per_share", "cny_return"])
      && isNonEmptyString(item.security_external_key) && isValueRange(item.usd_per_share) && isValueRange(item.cny_return))
    || !isNumericObservation(value.required_return)
    || !Array.isArray(value.required_return_comparisons) || value.required_return_comparisons.length === 0
    || !value.required_return_comparisons.every((item) => isRecord(item)
      && hasExactKeys(item, ["security_external_key", "required_return", "achieved_return_range", "meets_required_return"])
      && isNonEmptyString(item.security_external_key) && isNumericObservation(item.required_return)
      && isValueRange(item.achieved_return_range) && typeof item.meets_required_return === "boolean")) return false;
  const values = value.security_value_ranges.map((item) => isRecord(item) ? item.security_external_key : null);
  const comparisons = value.required_return_comparisons.map((item) => isRecord(item) ? item.security_external_key : null);
  return sameStringSets(values.filter(isNonEmptyString), comparisons.filter(isNonEmptyString));
}

function isJudgmentContextPayload(value: unknown): boolean {
  if (!isRecord(value) || !hasExactKeys(value, ["operating_baseline_available", "financial_bridge_closed", "market_security_bridge_available", "strongest_counterevidence", "next_verification_events", "_lineage"])) return false;
  const parents = isRecord(value._lineage) && Array.isArray(value._lineage.artifact_refs)
    ? value._lineage.artifact_refs.map((ref) => isRecord(ref) ? String(ref.artifact_kind) : "") : [];
  const expected = parents.includes("valuation_set")
    ? ["evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set", "valuation_set", "research_gaps"]
    : ["evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set", "research_gaps"];
  return isCompanyResearchLineage(value._lineage, expected)
    && typeof value.operating_baseline_available === "boolean"
    && typeof value.financial_bridge_closed === "boolean"
    && typeof value.market_security_bridge_available === "boolean"
    && Array.isArray(value.strongest_counterevidence)
    && value.strongest_counterevidence.every((ref) => isCompanyResearchSourceRef(ref, true))
    && isStringArray(value.next_verification_events);
}

function isMemoPayload(value: unknown): boolean {
  if (!isRecord(value) || !hasExactKeys(value, ["assessment_status", "business_map_ref", "driver_map_ref", "financial_bridge_ref", "scenario_set_ref", "valuation_set_ref", "gap_keys", "strongest_counterevidence", "next_verification_events", "candidate_status", "_lineage"])
    || !["not_answerable", "partially_answerable", "answerable"].includes(String(value.assessment_status))
    || value.candidate_status !== "machine_draft" || !isStringArray(value.gap_keys)
    || !Array.isArray(value.strongest_counterevidence)
    || !value.strongest_counterevidence.every((ref) => isCompanyResearchSourceRef(ref, true))
    || !isStringArray(value.next_verification_events)
    || !isCompanyResearchLineage(value._lineage, ["judgment_context"])) return false;
  const refs: [string, unknown][] = [
    ["business_map", value.business_map_ref], ["driver_map", value.driver_map_ref],
    ["financial_bridge", value.financial_bridge_ref], ["scenario_set", value.scenario_set_ref],
  ];
  if (value.valuation_set_ref !== null) refs.push(["valuation_set", value.valuation_set_ref]);
  return refs.every(([kind, ref]) => isRecord(ref)
    && hasExactKeys(ref, ["artifact_kind", "content_hash"])
    && ref.artifact_kind === kind && isHash(ref.content_hash));
}

function isCompanyResearchArtifact(value: unknown): boolean {
  if (!isProductDto(value) || !hasExactKeys(value, ["schema_version", "id", "project_id", "kind", "version", "input_hash", "content_hash", "payload", "source_refs"])
    || !isUuid(value.id) || !isUuid(value.project_id) || typeof value.kind !== "string" || !COMPANY_RESEARCH_ARTIFACT_KINDS.has(value.kind as typeof COMPANY_RESEARCH_STEPS[number])
    || !isPositiveInteger(value.version) || !isHash(value.input_hash) || !isHash(value.content_hash)
    || !isRecord(value.payload) || !Array.isArray(value.source_refs) || value.source_refs.length === 0
    || !value.source_refs.every((ref) => isCompanyResearchSourceRef(ref))) return false;
  const refs = value.source_refs.filter(isRecord).map(sourceRefIdentity);
  if (new Set(refs).size !== refs.length) return false;
  if (value.kind === "evidence_index") return isEvidencePayload(value.payload, value.source_refs.filter(isRecord));
  if (value.kind === "research_gaps") return isResearchGapsPayload(value.payload);
  if (value.kind === "business_map") return isBusinessMapPayload(value.payload);
  if (value.kind === "driver_map") return isDriverMapPayload(value.payload);
  if (value.kind === "financial_bridge") return isFinancialBridgePayload(value.payload);
  if (value.kind === "scenario_set") return isScenarioSetPayload(value.payload);
  if (value.kind === "valuation_set") return isValuationSetPayload(value.payload);
  if (value.kind === "judgment_context") return isJudgmentContextPayload(value.payload);
  return value.kind === "memo" && isMemoPayload(value.payload);
}

export const COMPANY_RESEARCH_MODULE_ARTIFACTS: Readonly<Record<string, readonly string[]>> = {
  overview: ["judgment_context"],
  business_map: ["business_map"],
  operating_drivers: ["driver_map"],
  evidence_and_gaps: ["evidence_index", "research_gaps"],
  industry_competition_regulation: ["business_map"],
  financials_cash_flow_capital_allocation: ["financial_bridge"],
  scenarios_valuation_implied_expectations: ["scenario_set", "valuation_set"],
  counterevidence_risks_next_checks: ["research_gaps", "judgment_context"],
  versions_changes_memo: ["memo"],
};

function isRegistryRef(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && hasExactKeys(value, ["id", "kind", "content_hash"])
    && isUuid(value.id) && typeof value.kind === "string"
    && COMPANY_RESEARCH_ARTIFACT_KINDS.has(value.kind as typeof COMPANY_RESEARCH_STEPS[number])
    && isHash(value.content_hash);
}

function collectArtifactParentRefs(value: unknown, refs: Record<string, unknown>[] = []): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    value.forEach((item) => collectArtifactParentRefs(item, refs));
    return refs;
  }
  if (!isRecord(value)) return refs;
  if (value.kind === "artifact_computation" && Array.isArray(value.artifact_refs)) {
    value.artifact_refs.filter(isArtifactParentRef).forEach((ref) => refs.push(ref));
  }
  if (isRecord(value._lineage) && Array.isArray(value._lineage.artifact_refs)) {
    value._lineage.artifact_refs.filter(isArtifactParentRef).forEach((ref) => refs.push(ref));
  }
  Object.values(value).forEach((item) => collectArtifactParentRefs(item, refs));
  return refs;
}

function collectComputationSources(value: unknown, sources: Record<string, unknown>[] = []): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    value.forEach((item) => collectComputationSources(item, sources));
    return sources;
  }
  if (!isRecord(value)) return sources;
  if (value.kind === "artifact_computation") sources.push(value);
  Object.values(value).forEach((item) => collectComputationSources(item, sources));
  return sources;
}

function collectNumericObservations(value: unknown, observations: Record<string, unknown>[] = []): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    value.forEach((item) => collectNumericObservations(item, observations));
    return observations;
  }
  if (!isRecord(value)) return observations;
  if (isNumericObservation(value)) {
    observations.push(value);
    return observations;
  }
  Object.values(value).forEach((item) => collectNumericObservations(item, observations));
  return observations;
}

function isCompanyResearchWorkspace(value: unknown): value is CompanyResearchWorkspace {
  if (!isProductDto(value) || !hasExactKeys(value, ["schema_version", "project_id", "company", "preparation", "artifacts", "modules", "source_count", "gap_count", "draft", "selected_revision", "change_summary"])
    || !isUuid(value.project_id) || !isProductDto(value.company)
    || !hasExactKeys(value.company, ["schema_version", "object_id", "external_key", "canonical_name", "id"])
    || !isUuid(value.company.id) || value.company.object_id !== value.company.id || !isNonEmptyString(value.company.external_key) || !isNonEmptyString(value.company.canonical_name)
    || !isProductDto(value.preparation) || !hasExactKeys(value.preparation, ["schema_version", "id", "status", "current_step", "progress", "error"])
    || !isUuid(value.preparation.id) || !isNonNegativeInteger(value.preparation.progress) || Number(value.preparation.progress) > 100
    || !["queued", "preparing_sources", "awaiting_evidence_review", "building_model", "awaiting_judgment_review", "ready_to_freeze", "recoverable_failure", "blocked", "completed"].includes(String(value.preparation.status))
    || !(value.preparation.current_step === null || isNonEmptyString(value.preparation.current_step))
    || !Array.isArray(value.artifacts) || !value.artifacts.every(isCompanyResearchArtifact)
    || !Array.isArray(value.modules) || value.modules.length !== COMPANY_RESEARCH_AGENDA_KEYS.length
    || !isNonNegativeInteger(value.source_count) || !isNonNegativeInteger(value.gap_count)
    || !isProductDto(value.draft) || !hasExactKeys(value.draft, ["schema_version", "id", "lock_version", "base_revision_id"])
    || !isUuid(value.draft.id) || !isPositiveInteger(value.draft.lock_version) || !isNullableUuid(value.draft.base_revision_id)
    || !isNullableUuid(value.selected_revision) || value.selected_revision !== value.draft.base_revision_id || !isRecord(value.change_summary)) return false;
  const moduleKeys = value.modules.map((item) => isProductDto(item) ? item.key : null);
  if (!sameOrderedStrings(moduleKeys.filter(isNonEmptyString), [...COMPANY_RESEARCH_AGENDA_KEYS])) return false;
  const expectedEvidenceReview = value.preparation.status === "awaiting_evidence_review";
  const failed = value.preparation.status === "recoverable_failure" || value.preparation.status === "blocked";
  const error = value.preparation.error;
  if (failed) {
    const failureSteps = new Set([...COMPANY_RESEARCH_STEPS, "model_bundle"]);
    if (!isProductDto(error) || !hasExactKeys(error, ["schema_version", "code", "failed_step", "retryable", "next_attempt_at"])
      || !isNonEmptyString(error.code) || error.failed_step !== value.preparation.current_step
      || !failureSteps.has(String(error.failed_step))
      || typeof error.retryable !== "boolean" || (value.preparation.status === "recoverable_failure") !== error.retryable
      || !(error.next_attempt_at === null || isDateTime(error.next_attempt_at))) return false;
    if (value.preparation.status === "recoverable_failure" && error.next_attempt_at === null) return false;
    if (value.preparation.status === "blocked" && error.next_attempt_at !== null) return false;
  } else if (error !== null) return false;
  const summary = value.change_summary;
  const artifactVersions = isRecord(summary) ? summary.artifact_versions : null;
  if (!hasExactKeys(summary, ["artifact_versions", "reviewed_fact_count"])
    || !isRecord(artifactVersions)
    || !Object.values(artifactVersions).every(isPositiveInteger)
    || !isNonNegativeInteger(summary.reviewed_fact_count)) return false;
  const artifacts = value.artifacts.filter(isRecord);
  const registry = new Map<string, Record<string, unknown>>();
  const kinds = new Set<string>();
  for (const artifact of artifacts) {
    if (artifact.project_id !== value.project_id || registry.has(String(artifact.id)) || kinds.has(String(artifact.kind))) return false;
    registry.set(String(artifact.id), artifact);
    kinds.add(String(artifact.kind));
  }
  if (kinds.has("valuation_set") && !kinds.has("scenario_set")) return false;
  if (!sameStringSets(Object.keys(artifactVersions), [...kinds])) return false;
  if (artifacts.some((artifact) => artifactVersions[String(artifact.kind)] !== artifact.version)) return false;
  const exactRef = (ref: Record<string, unknown>, registryShape: boolean): boolean => {
    const id = String(registryShape ? ref.id : ref.artifact_id);
    const kind = String(registryShape ? ref.kind : ref.artifact_kind);
    const artifact = registry.get(id);
    return artifact !== undefined && artifact.kind === kind && artifact.content_hash === ref.content_hash;
  };
  if (artifacts.some((artifact) => {
    if (collectArtifactParentRefs(artifact.payload).some((ref) => !exactRef(ref, false))) return true;
    const payload = isRecord(artifact.payload) ? artifact.payload : null;
    const lineage = payload && isRecord(payload._lineage) ? payload._lineage : null;
    return collectComputationSources(artifact.payload).some((source) => lineage === null
      || JSON.stringify(source.artifact_refs) !== JSON.stringify(lineage.artifact_refs)
      || JSON.stringify(source.market_snapshot_ids) !== JSON.stringify(lineage.market_snapshot_ids));
  })) return false;
  const evidence = artifacts.find((artifact) => artifact.kind === "evidence_index");
  const evidencePayload = evidence && isRecord(evidence.payload) ? evidence.payload : null;
  const facts = evidencePayload && Array.isArray(evidencePayload.facts)
    ? evidencePayload.facts.filter(isRecord) : [];
  const factRegistry = new Map<string, Record<string, unknown>>(
    facts.map((fact) => [String(fact.fact_key), fact]),
  );
  const reviewedFactCount = facts.filter((fact) => fact.review_decision === "confirmed" || fact.review_decision === "rejected").length;
  if (summary.reviewed_fact_count !== reviewedFactCount) return false;
  const sourceIdentities = new Set(artifacts.flatMap((artifact) => Array.isArray(artifact.source_refs)
    ? artifact.source_refs.filter(isRecord).map(sourceRefIdentity) : []));
  if (value.source_count !== sourceIdentities.size) return false;
  const gapArtifact = artifacts.find((artifact) => artifact.kind === "research_gaps");
  const gapPayload = gapArtifact && isRecord(gapArtifact.payload) ? gapArtifact.payload : null;
  const gaps = gapPayload && Array.isArray(gapPayload.gaps) ? gapPayload.gaps : [];
  const memoArtifact = artifacts.find((artifact) => artifact.kind === "memo");
  const memoPayload = memoArtifact && isRecord(memoArtifact.payload) ? memoArtifact.payload : null;
  const expectedGapCount = memoPayload && Array.isArray(memoPayload.gap_keys)
    ? memoPayload.gap_keys.length : gaps.length;
  if (value.gap_count !== expectedGapCount) return false;
  for (const artifact of artifacts) {
    for (const observation of collectNumericObservations(artifact.payload)) {
      if (observation.state !== "reported" || !isRecord(observation.source_ref)) continue;
      const fact = factRegistry.get(String(observation.source_ref.fact_key));
      const factObservation = fact && isRecord(fact.observation) ? fact.observation : null;
      const source = factObservation && isRecord(factObservation.source_ref) ? factObservation.source_ref : null;
      if (factObservation === null || source === null
        || (artifact.kind !== "evidence_index" && fact?.review_decision !== "confirmed")
        || observation.value !== factObservation.value
        || observation.unit !== factObservation.unit
        || observation.currency !== factObservation.currency
        || observation.period !== factObservation.period
        || observation.source_ref.kind !== source.kind
        || observation.source_ref.fact_key !== source.fact_key
        || observation.source_ref.source_role !== source.source_role
        || observation.source_ref.source_url !== source.source_url
        || observation.source_ref.source_locator !== source.source_locator
        || observation.source_ref.raw_hash !== source.raw_hash) return false;
    }
  }
  return value.modules.every((item) => {
    if (!isProductDto(item)
      || !hasExactKeys(item, ["schema_version", "key", "state", "artifact_refs", "valuation_state"])
      || !isNonEmptyString(item.key)
      || COMPANY_RESEARCH_MODULE_ARTIFACTS[item.key] === undefined
      || !["not_started", "preparing", "needs_review", "ready", "blocked"].includes(String(item.state))
      || !Array.isArray(item.artifact_refs) || !item.artifact_refs.every(isRegistryRef)
      || !["not_applicable", "pending", "ready", "blocked"].includes(String(item.valuation_state))) return false;
    const refs = item.artifact_refs.filter(isRecord);
    const refKinds = refs.map((ref) => String(ref.kind));
    const allowed = COMPANY_RESEARCH_MODULE_ARTIFACTS[item.key];
    const expectedKinds = item.key === "scenarios_valuation_implied_expectations" && item.valuation_state === "blocked"
      ? ["scenario_set"] : allowed;
    if (!refs.every((ref) => exactRef(ref, true))
      || !sameOrderedStrings(refKinds, allowed.filter((kind) => refKinds.includes(kind)))) return false;
    if ((item.state === "ready" || item.state === "needs_review")
      && !sameOrderedStrings(refKinds, expectedKinds)) return false;
    if (item.key === "scenarios_valuation_implied_expectations") {
      if (item.valuation_state === "ready" && !sameOrderedStrings(refKinds, ["scenario_set", "valuation_set"])) return false;
      if (item.valuation_state === "blocked" && !sameOrderedStrings(refKinds, ["scenario_set"])) return false;
      if ((item.valuation_state === "ready" || item.valuation_state === "blocked") && item.state !== "ready") return false;
      if (item.valuation_state === "pending" && item.state === "ready") return false;
    } else if (item.valuation_state !== "not_applicable") return false;
    if (item.state === "ready") return refs.length > 0 && !(expectedEvidenceReview && refKinds.includes("evidence_index"));
    if (item.state === "needs_review") return expectedEvidenceReview && refKinds.includes("evidence_index");
    return refs.length === 0;
  });
}

function isCompanyResearchEvidenceReview(value: unknown): value is CompanyResearchEvidenceReview {
  const artifact = isProductDto(value) ? value.evidence_artifact : null;
  return isProductDto(value) && hasExactKeys(value, ["schema_version", "evidence_artifact"])
    && isCompanyResearchArtifact(artifact) && isRecord(artifact) && artifact.kind === "evidence_index";
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
  if (!isProductDto(value) || !hasExactKeys(value, ["schema_version", "error"])
    || !isRecord(value.error) || !hasExactKeys(value.error, ["code", "message", "request_id", "details"])) return null;
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

function sameOrderedStrings(actual: string[], expected: readonly string[]): boolean {
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

function sameNullableDecimal(actual: unknown, expected: unknown): boolean {
  return actual == null && expected == null || sameDecimal(actual, expected);
}

function sameNullableInstant(actual: unknown, expected: unknown): boolean {
  return actual == null && expected == null || sameInstant(actual, expected);
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

  async industryCompanies(industryId: string, options: { asOf?: string; limit?: number } = {}): Promise<IndustryCompanyBrowse> {
    assertUuid(industryId, "industryId");
    const normalizedIndustryId = industryId.toLowerCase();
    const params = new URLSearchParams();
    if (options.asOf) params.set("as_of", options.asOf);
    params.set("limit", String(options.limit ?? 20));
    const browse = await requestJson(
      `${this.root}/industries/${encodeURIComponent(industryId)}/companies?${params}`,
      isIndustryCompanyBrowse,
      200,
      { method: "GET" },
    );
    if (browse.industry_id !== normalizedIndustryId) {
      throw new InvestmentResearchRequestError("投资研究服务返回了不匹配的行业结果", 502, "invalid_response", null);
    }
    return browse;
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
    if (value.project_id !== projectId
      || value.mandate_key !== `product.project:${projectId}`
      || value.horizon_years !== body.horizon_years
      || value.base_currency !== body.base_currency
      || !sameDecimal(value.required_return, body.required_return)
      || !sameDecimal(value.permanent_loss_limit, body.permanent_loss_limit)
      || !sameStringSets(value.comparison_set, body.comparison_set)
      || value.benchmark_key !== (body.benchmark_key ?? null)
      || !sameNullableDecimal(value.required_excess_return, body.required_excess_return)
      || !sameInstant(value.effective_at, body.effective_at)
      || !sameNullableInstant(value.expires_at, body.expires_at)) mismatch("mandate request identity mismatch");
    return value;
  }

  async createScope(projectId: string, body: CreateScopeRequest): Promise<ProductScope> {
    const value = await requestJson(`${this.root}/projects/${encodeURIComponent(projectId)}/scopes`, isScope, 201, jsonInit("POST", body));
    if (value.project_id !== projectId
      || value.payload.primary_company_id !== body.primary_company_id
      || !sameStringSets(value.payload.target_security_ids, body.target_security_ids)
      || !sameStringSets(value.payload.industry_ids, body.industry_ids ?? [])
      || !sameStringSets(value.payload.covered_segments, body.covered_segments ?? [])
      || value.payload.user_focus !== (body.user_focus ?? null)
      || !sameStringSets(value.payload.exclusions, body.exclusions ?? [])) {
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

  async createHistoricalBasis(body: CreateHistoricalBasisRequest): Promise<ProductHistoricalBasis> {
    const value = await requestJson(`${this.root}/historical-bases`, isHistoricalBasis, 201, jsonInit("POST", body));
    if (!sameInstant(value.cutoff_at, body.cutoff_at)
      || value.price_as_of !== null
      || value.source_manifest_hash !== body.source_manifest_hash
      || value.definition_bundle_hash !== body.definition_bundle_hash
      || value.parser_bundle_hash !== body.parser_bundle_hash) mismatch("historical basis request identity mismatch");
    return value;
  }

  async createPriceSnapshot(body: CreatePriceSnapshotRequest): Promise<ProductPriceSnapshot> {
    const value = await requestJson(`${this.root}/market/price-snapshots`, isPrice, 201, jsonInit("POST", body));
    if (value.security_identity_id !== body.security_identity_id
      || !sameDecimal(value.price, body.price) || value.currency !== body.currency
      || value.price_type !== body.price_type || value.adjustment_basis !== body.adjustment_basis
      || !sameInstant(value.market_at, body.market_at) || !sameInstant(value.available_at, body.available_at)
      || value.source_id !== body.source_id || value.raw_hash !== body.raw_hash) mismatch("price snapshot request identity mismatch");
    return value;
  }

  async createFxSnapshot(body: CreateFxSnapshotRequest): Promise<ProductFxSnapshot> {
    const value = await requestJson(`${this.root}/market/fx-snapshots`, isFx, 201, jsonInit("POST", body));
    if (value.base_currency !== body.base_currency || value.quote_currency !== body.quote_currency
      || !sameDecimal(value.rate, body.rate) || value.quote_direction !== body.quote_direction
      || !sameInstant(value.market_at, body.market_at) || !sameInstant(value.available_at, body.available_at)
      || value.source_id !== body.source_id || value.raw_hash !== body.raw_hash) mismatch("FX snapshot request identity mismatch");
    return value;
  }

  async createCapitalStructure(body: CreateCapitalStructureRequest): Promise<ProductCapitalStructure> {
    const value = await requestJson(`${this.root}/market/capital-structure-snapshots`, isCapital, 201, jsonInit("POST", body));
    const decimalKeys = ["cash", "debt", "minority_interest", "investments", "pension_liabilities", "other_adjustments", "basic_shares", "diluted_shares"] as const;
    if (value.company_id !== body.company_id || value.currency !== body.currency
      || decimalKeys.some((key) => !sameDecimal(value[key], body[key]))
      || !sameOrderedStrings(value.potential_dilution_descriptors, body.potential_dilution_descriptors ?? [])
      || !sameInstant(value.report_period_start, body.report_period_start) || !sameInstant(value.report_period_end, body.report_period_end)
      || !sameInstant(value.market_at, body.market_at) || !sameInstant(value.available_at, body.available_at)
      || value.source_id !== body.source_id || value.raw_hash !== body.raw_hash) mismatch("capital structure request identity mismatch");
    return value;
  }

  async createSecurityRights(body: CreateSecurityRightsRequest): Promise<ProductSecurityRights> {
    const value = await requestJson(`${this.root}/market/security-rights`, isRights, 201, jsonInit("POST", body));
    const decimalKeys = ["economic_units", "votes_per_unit", "conversion_ratio", "adr_ratio", "dividend_rights_per_unit"] as const;
    if (value.security_identity_id !== body.security_identity_id
      || decimalKeys.some((key) => !sameDecimal(value[key], body[key]))
      || !sameInstant(value.effective_from, body.effective_from) || !sameNullableInstant(value.effective_to, body.effective_to)
      || value.source_id !== body.source_id || value.raw_hash !== body.raw_hash
      || value.supersedes_id !== (body.expected_parent_id ?? null)) mismatch("security rights request identity mismatch");
    return value;
  }

  async effectiveSecurityRights(securityIdentityId: string, asOf: string): Promise<EffectiveSecurityRights> {
    const params = new URLSearchParams({ security_identity_id: securityIdentityId, as_of: asOf });
    const value = await requestJson(`${this.root}/market/security-rights/effective?${params}`, isEffectiveRights, 200, { method: "GET" });
    if (value.security_identity_id !== securityIdentityId || !sameInstant(value.as_of, asOf)) mismatch("effective rights request identity mismatch");
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
    if (value.project_id !== projectId || value.manifest.project_id !== projectId
      || value.expected_lock_version !== body.expected_lock_version) mismatch("preview project identity mismatch");
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

  async previewCompanyResearch(body: CompanyResearchPreviewRequest): Promise<CompanyResearchPreview> {
    const value = await requestJson(
      `${this.root}/company-research/preview`,
      isCompanyResearchPreview,
      200,
      jsonInit("POST", body),
    );
    if (value.company.object_id !== body.company_id
      || !sameInstant(value.cutoff_at, body.cutoff_at)) {
      mismatch("company-research preview binding mismatch");
    }
    return value;
  }

  async initializeCompanyResearch(
    body: InitializeCompanyResearchRequest,
    idempotencyKey: string,
  ): Promise<CompanyResearchProject> {
    const value = await requestJson(
      `${this.root}/company-research/initializations`,
      isCompanyResearchProject,
      201,
      jsonInit("POST", body, { "Idempotency-Key": idempotencyKey }),
    );
    if (value.company_id !== body.company_id || value.preparation.request_hash !== body.preview_hash) {
      mismatch("company-research initialization preview binding mismatch");
    }
    return value;
  }

  async companyResearchProject(projectId: string): Promise<CompanyResearchProject> {
    const value = await requestJson(`${this.root}/company-research/projects/${encodeURIComponent(projectId)}`, isCompanyResearchProject, 200, { method: "GET" });
    if (value.project_id !== projectId) mismatch("company-research project identity mismatch");
    return value;
  }

  async retryCompanyResearchProject(projectId: string): Promise<CompanyResearchProject> {
    const value = await requestJson(`${this.root}/company-research/projects/${encodeURIComponent(projectId)}/retry`, isCompanyResearchProject, 202, { method: "POST" });
    if (value.project_id !== projectId) mismatch("company-research project identity mismatch");
    return value;
  }

  async companyResearchWorkspace(projectId: string): Promise<CompanyResearchWorkspace> {
    assertUuid(projectId, "projectId");
    const value = await requestJson(`${this.root}/company-research/projects/${encodeURIComponent(projectId)}/workspace`, isCompanyResearchWorkspace, 200, { method: "GET" });
    if (value.project_id !== projectId) mismatch("company-research workspace project identity mismatch");
    return value;
  }

  async reviewCompanyEvidence(projectId: string, body: ReviewCompanyEvidenceRequest): Promise<CompanyResearchEvidenceReview> {
    assertUuid(projectId, "projectId");
    const value = await requestJson(`${this.root}/company-research/projects/${encodeURIComponent(projectId)}/evidence-reviews`, isCompanyResearchEvidenceReview, 200, jsonInit("POST", body));
    if (value.evidence_artifact.kind !== "evidence_index") mismatch("company-research evidence review kind mismatch");
    return value;
  }
}

export const investmentResearchApi = new InvestmentResearchApi();
