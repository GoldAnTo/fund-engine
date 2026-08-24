import type { components } from "../../contracts/v1";

type AgendaGenerator = components["schemas"]["AgendaGeneratorRequest"];

export type FrozenTimezone = "+08:00" | "Z";

export type FoundationAgendaInput = {
  company: {
    objectId: string;
    canonicalName: string;
    externalKey: string;
  };
  securities: Array<{
    objectId: string;
    canonicalName: string;
    externalKey: string;
    symbol: string | null;
  }>;
  mandate: {
    horizonYears: number;
    baseCurrency: "CNY" | "USD";
    requiredReturn: string;
    permanentLossLimit: string;
    comparisonSet: string[];
    benchmarkKey: string | null;
    requiredExcessReturn: string | null;
  };
  scope: {
    industryNames: string[];
    coveredSegments: string[];
    userFocus: string | null;
    exclusions: string[];
  };
};

export type GeneratedFoundationAgenda = {
  items: string[];
  inputSummaryHash: string;
  generator: AgendaGenerator;
};

export function toFrozenIso(value: string, timezone: FrozenTimezone): string {
  if (timezone !== "+08:00" && timezone !== "Z") {
    throw new Error("不支持的冻结时区");
  }
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!parts) {
    throw new Error("时间格式无法识别");
  }
  const [, year, month, day, hour, minute, second = "0"] = parts;
  const wallClock = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second)));
  if (wallClock.getUTCFullYear() !== Number(year)
    || wallClock.getUTCMonth() !== Number(month) - 1
    || wallClock.getUTCDate() !== Number(day)
    || wallClock.getUTCHours() !== Number(hour)
    || wallClock.getUTCMinutes() !== Number(minute)
    || wallClock.getUTCSeconds() !== Number(second)) {
    throw new Error("时间格式无法识别");
  }
  const timestamp = new Date(`${value}${timezone === "Z" ? "Z" : timezone}`);
  if (Number.isNaN(timestamp.getTime())) throw new Error("时间格式无法识别");
  return timestamp.toISOString();
}

function sorted(values: string[]): string[] {
  return values.slice().sort((left, right) => left.localeCompare(right, "zh-CN"));
}

function canonicalInput(input: FoundationAgendaInput) {
  return {
    schema_version: "product.foundation-agenda-input.v1",
    company: {
      object_id: input.company.objectId,
      canonical_name: input.company.canonicalName,
      external_key: input.company.externalKey,
    },
    securities: input.securities
      .map((security) => ({
        object_id: security.objectId,
        canonical_name: security.canonicalName,
        external_key: security.externalKey,
        symbol: security.symbol,
      }))
      .sort((left, right) => left.object_id.localeCompare(right.object_id)),
    mandate: {
      horizon_years: input.mandate.horizonYears,
      base_currency: input.mandate.baseCurrency,
      required_return: input.mandate.requiredReturn,
      permanent_loss_limit: input.mandate.permanentLossLimit,
      comparison_set: sorted(input.mandate.comparisonSet),
      benchmark_key: input.mandate.benchmarkKey,
      required_excess_return: input.mandate.requiredExcessReturn,
    },
    scope: {
      industry_names: sorted(input.scope.industryNames),
      covered_segments: sorted(input.scope.coveredSegments),
      user_focus: input.scope.userFocus,
      exclusions: sorted(input.scope.exclusions),
    },
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function agendaItemsHash(items: string[]): Promise<string> {
  return sha256(JSON.stringify(items));
}

export async function generateFoundationAgenda(
  input: FoundationAgendaInput,
): Promise<GeneratedFoundationAgenda> {
  const canonical = canonicalInput(input);
  const securityNames = canonical.securities
    .map((security) => security.symbol ?? security.canonical_name)
    .sort((left, right) => left.localeCompare(right, "zh-CN"))
    .join("、");
  const industries = canonical.scope.industry_names.length > 0
    ? canonical.scope.industry_names.join("、")
    : "相关行业";
  const comparisonSet = canonical.mandate.comparison_set.join("、");
  const focus = canonical.scope.user_focus ?? "未指定额外焦点";
  const items = [
    `核验${canonical.company.canonical_name}的公司身份、业务边界与${industries}分类，并追溯主要来源。`,
    `核验${securityNames}的证券身份、经济权利、交易货币与公司关系，不合并不同证券类别。`,
    `在${canonical.mandate.horizon_years}年研究期限内，分析竞争位置、业务驱动与资本配置，区分事实、假设和待证事项。`,
    `以${comparisonSet}为比较集合，核验口径一致性，并记录无法比较的数据缺口。`,
    `围绕必要回报率${canonical.mandate.required_return}与永久损失上限${canonical.mandate.permanent_loss_limit}，检查下行情景和反证条件。`,
    `按研究焦点“${focus}”整理证据需求、阻塞项与解除条件；议程本身不构成研究完成或投资结论。`,
  ];
  const inputHash = await sha256(JSON.stringify(canonical));
  const outputHash = await agendaItemsHash(items);
  return {
    items,
    inputSummaryHash: inputHash,
    generator: {
      schema_version: "underwriting.v1",
      method: "deterministic_template",
      template_key: "product.foundation.agenda",
      template_version: "1.0.0",
      model_name: null,
      prompt_template_version: null,
      input_summary_hash: null,
      output_hash: outputHash,
    },
  };
}
