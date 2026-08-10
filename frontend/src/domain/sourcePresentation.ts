/** Human-readable labels for the immutable source types stored in Case scopes. */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  licensed_provider: "授权供应商资料",
  company_disclosure: "公司披露",
  uploaded_file: "上传原件",
  pasted_snapshot: "粘贴快照",
  public_url: "公开网页快照",
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
