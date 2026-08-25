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
export type EffectiveSecurityRights = Schemas["EffectiveSecurityRightsResponse"];
export type CompanyResearchPreview = Schemas["CompanyResearchPreviewResponse"];
export type CompanyResearchProject = Schemas["CompanyResearchProjectResponse"];
export type CompanyResearchPreviewRequest = Schemas["CompanyResearchPreviewRequest"];
export type InitializeCompanyResearchRequest = Schemas["InitializeCompanyResearchRequest"];

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
    return step === "evidence_index" && value.progress === 0
      && value.next_attempt_at === null && value.last_error_code === null;
  }
  if (value.status === "completed") {
    return step === null && value.progress === 100
      && value.next_attempt_at === null && value.last_error_code === null;
  }
  if (value.status === "preparing_sources" || value.status === "awaiting_evidence_review") {
    return step === "evidence_index" && value.last_error_code === null;
  }
  if (value.status === "building_model") {
    return typeof step === "string" && COMPANY_RESEARCH_STEPS.includes(step as typeof COMPANY_RESEARCH_STEPS[number])
      && step !== "evidence_index" && value.last_error_code === null;
  }
  if (value.status === "awaiting_judgment_review") return step === "judgment_context" && value.last_error_code === null;
  if (value.status === "ready_to_freeze") return step === "memo" && value.last_error_code === null;
  if (value.status === "recoverable_failure" || value.status === "blocked") {
    return typeof step === "string" && COMPANY_RESEARCH_STEPS.includes(step as typeof COMPANY_RESEARCH_STEPS[number])
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
}

export const investmentResearchApi = new InvestmentResearchApi();
