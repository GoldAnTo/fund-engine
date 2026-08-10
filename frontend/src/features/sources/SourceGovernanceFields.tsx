export type SourceGovernance = {
  region: string;
  retentionPolicy: string;
  deletionPolicy: string;
  downstreamRestrictions: string;
};

export const DEFAULT_SOURCE_GOVERNANCE: SourceGovernance = {
  region: "CN",
  retentionPolicy: "case_retained",
  deletionPolicy: "not_recorded",
  downstreamRestrictions: "仅限当前 Case 研究与人工审核",
};

export function sourceGovernanceMetadata(value: SourceGovernance): Record<string, unknown> {
  return {
    region: value.region.trim() || "not_recorded",
    retention_policy: value.retentionPolicy.trim() || "case_retained",
    deletion_policy: value.deletionPolicy.trim() || "not_recorded",
    downstream_restrictions: value.downstreamRestrictions
      .split(/[\n；]/)
      .map((item) => item.trim())
      .filter(Boolean),
  };
}

export function SourceGovernanceFields({
  value,
  onChange,
}: {
  value: SourceGovernance;
  onChange: (next: SourceGovernance) => void;
}) {
  function update<K extends keyof SourceGovernance>(key: K, next: SourceGovernance[K]) {
    onChange({ ...value, [key]: next });
  }
  return (
    <fieldset className="ros-source-governance">
      <legend>资料保留与下游边界</legend>
      <small>
        这些字段会随冻结版本保存。它们描述资料可如何被保留和使用，不会扩大来源许可或替代人工审核。
      </small>
      <label>
        资料适用地域
        <input
          aria-label="资料适用地域"
          value={value.region}
          onChange={(event) => update("region", event.target.value)}
          placeholder="例如：CN"
        />
      </label>
      <label>
        保留策略
        <input
          aria-label="保留策略"
          value={value.retentionPolicy}
          onChange={(event) => update("retentionPolicy", event.target.value)}
          placeholder="例如：case_retained"
        />
      </label>
      <label>
        删除策略
        <input
          aria-label="删除策略"
          value={value.deletionPolicy}
          onChange={(event) => update("deletionPolicy", event.target.value)}
          placeholder="例如：not_recorded"
        />
      </label>
      <label>
        下游使用限制
        <textarea
          aria-label="下游使用限制"
          value={value.downstreamRestrictions}
          onChange={(event) => update("downstreamRestrictions", event.target.value)}
          placeholder="用分号或换行分隔限制"
        />
      </label>
    </fieldset>
  );
}
