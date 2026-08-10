import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_SOURCE_GOVERNANCE,
  SourceGovernanceFields,
  sourceGovernanceMetadata,
  sourceGovernanceValidationError,
} from "./SourceGovernanceFields";

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

  it("rejects an end date before the source contract becomes effective", () => {
    expect(
      sourceGovernanceValidationError({
        region: "CN",
        effectiveFrom: "2026-12-31",
        effectiveUntil: "2026-01-01",
        retentionPolicy: "case_retained",
        deletionPolicy: "not_recorded",
        contractVersion: "",
        downstreamRestrictions: "仅限当前 Case 研究与人工审核",
      }),
    ).toBe("授权失效日不能早于授权生效日。");
  });

  it("keeps advanced retention controls available without leading the intake flow", () => {
    render(createElement(SourceGovernanceFields, {
      value: DEFAULT_SOURCE_GOVERNANCE,
      onChange: vi.fn(),
    }));

    const controls = screen
      .getByText("资料保留与下游边界")
      .closest("details");
    expect(controls).not.toBeNull();
    expect(controls).not.toHaveAttribute("open");
    expect(screen.getByLabelText("资料适用地域")).toBeInTheDocument();
  });
});
