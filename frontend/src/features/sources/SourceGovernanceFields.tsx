export type SourceGovernance = {
  region: string;
  effectiveFrom: string;
  effectiveUntil: string;
  retentionPolicy: string;
  deletionPolicy: string;
  contractVersion: string;
  downstreamRestrictions: string;
};

export const DEFAULT_SOURCE_GOVERNANCE: SourceGovernance = {
  region: "CN",
  effectiveFrom: "",
  effectiveUntil: "",
  retentionPolicy: "case_retained",
  deletionPolicy: "not_recorded",
  contractVersion: "",
  downstreamRestrictions: "仅限当前 Case 研究与人工审核",
};

export function sourceGovernanceMetadata(value: SourceGovernance): Record<string, unknown> {
  return {
    region: value.region.trim() || "not_recorded",
    ...(value.effectiveFrom ? { effective_from: value.effectiveFrom } : {}),
    ...(value.effectiveUntil ? { effective_until: value.effectiveUntil } : {}),
    retention_policy: value.retentionPolicy.trim() || "case_retained",
    deletion_policy: value.deletionPolicy.trim() || "not_recorded",
    ...(value.contractVersion.trim()
      ? { contract_version: value.contractVersion.trim() }
      : {}),
    downstream_restrictions: value.downstreamRestrictions
      .split(/[\n；]/)
      .map((item) => item.trim())
      .filter(Boolean),
  };
}

export function sourceGovernanceValidationError(
  value: SourceGovernance,
): string | null {
  if (
    value.effectiveFrom
    && value.effectiveUntil
    && value.effectiveUntil < value.effectiveFrom
  ) {
    return "授权失效日不能早于授权生效日。";
  }
  return null;
}

export function SourceGovernanceFields({
  value,
  onChange,
}: {
  value: SourceGovernance;
  onChange: (next: SourceGovernance) => void;
}) {
  const validationError = sourceGovernanceValidationError(value);
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
      <div className="ros-source-governance__dates">
        <label>
          授权生效日
          <input
            aria-label="授权生效日"
            type="date"
            value={value.effectiveFrom}
            onChange={(event) => update("effectiveFrom", event.target.value)}
          />
        </label>
        <label>
          授权失效日
          <input
            aria-label="授权失效日"
            type="date"
            value={value.effectiveUntil}
            onChange={(event) => update("effectiveUntil", event.target.value)}
          />
        </label>
      </div>
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
        合同或许可版本
        <input
          aria-label="合同或许可版本"
          value={value.contractVersion}
          onChange={(event) => update("contractVersion", event.target.value)}
          placeholder="例如：juyuan-research-v4"
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
      {validationError && <p className="ros-error" role="alert">{validationError}</p>}
    </fieldset>
  );
}
