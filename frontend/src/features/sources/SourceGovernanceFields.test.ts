import { describe, expect, it } from "vitest";

import { sourceGovernanceMetadata } from "./SourceGovernanceFields";

describe("sourceGovernanceMetadata", () => {
  it("preserves explicit governance fields and separates downstream restrictions", () => {
    expect(
      sourceGovernanceMetadata({
        region: " US ",
        retentionPolicy: " contract_2026 ",
        deletionPolicy: " delete_after_2027 ",
        downstreamRestrictions: "仅限投研团队；\n禁止外部导出",
      }),
    ).toEqual({
      region: "US",
      retention_policy: "contract_2026",
      deletion_policy: "delete_after_2027",
      downstream_restrictions: ["仅限投研团队", "禁止外部导出"],
    });
  });
});
