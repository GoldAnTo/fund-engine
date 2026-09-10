import { describe, expect, it } from "vitest";

import {
  parseQualityLabel,
  sourceAuthorityLabel,
  sourceRetentionLabel,
} from "./sourcePresentation";

describe("source presentation", () => {
  it("translates governance states without hiding the underlying evidence boundary", () => {
    expect(sourceAuthorityLabel("unknown")).toBe("权威性尚未核验");
    expect(parseQualityLabel("ok")).toBe("解析完成");
    expect(sourceRetentionLabel("case_retained")).toBe("按 Case 保留");
    expect(sourceRetentionLabel("not_recorded")).toBe("删除规则未记录");
  });
});
