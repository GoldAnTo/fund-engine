/** Human-readable labels for the immutable source types stored in Case scopes. */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  licensed_provider: "授权供应商资料",
  company_disclosure: "公司披露",
  uploaded_file: "上传原件",
  pasted_snapshot: "粘贴快照",
  public_url: "公开网页快照",
};

const SOURCE_AUTHORITY_LABELS: Record<string, string> = {
  primary_disclosure: "一手公司披露",
  licensed_research: "授权研究资料",
  secondary_source: "二手来源",
  user_supplied: "用户提交，尚未核验",
  unknown: "权威性尚未核验",
};

const PARSE_QUALITY_LABELS: Record<string, string> = {
  ok: "解析完成",
  partial: "解析不完整",
  failed: "解析失败",
};

const RETENTION_LABELS: Record<string, string> = {
  case_retained: "按 Case 保留",
  not_recorded: "删除规则未记录",
};

export function sourceTypeLabel(value: string | null | undefined): string {
  if (!value) return "未记录";
  return SOURCE_TYPE_LABELS[value] ?? value;
}

export function sourceTypeListLabel(
  values: readonly string[] | null | undefined,
): string {
  if (!values?.length) return "未记录";
  return values.map(sourceTypeLabel).join("、");
}

export function sourceAuthorityLabel(value: string | null | undefined): string {
  if (!value) return "权威性尚未记录";
  return SOURCE_AUTHORITY_LABELS[value] ?? value;
}

export function parseQualityLabel(value: string | null | undefined): string {
  if (!value) return "解析状态未记录";
  return PARSE_QUALITY_LABELS[value] ?? value;
}

export function sourceRetentionLabel(value: string | null | undefined): string {
  if (!value) return "未记录";
  return RETENTION_LABELS[value] ?? value;
}
