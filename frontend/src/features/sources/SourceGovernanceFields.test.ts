import { describe, expect, it } from "vitest";

import { sourceGovernanceMetadata } from "./SourceGovernanceFields";

describe("sourceGovernanceMetadata", () => {
  it("preserves explicit governance fields and separates downstream restrictions", () => {
    expect(
      sourceGovernanceMetadata({
        region: " US ",
        effectiveFrom: "2026-01-01",
        effectiveUntil: "2026-12-31",
        retentionPolicy: " contract_2026 ",
        deletionPolicy: " delete_after_2027 ",
        contractVersion: " juyuan-research-v4 ",
        downstreamRestrictions: "仅限投研团队；\n禁止外部导出",
      }),
    ).toEqual({
      region: "US",
      effective_from: "2026-01-01",
      effective_until: "2026-12-31",
      retention_policy: "contract_2026",
      deletion_policy: "delete_after_2027",
      contract_version: "juyuan-research-v4",
      downstream_restrictions: ["仅限投研团队", "禁止外部导出"],
    });
  });
});
