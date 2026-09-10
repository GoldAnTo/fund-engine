import { describe, expect, it } from "vitest";

import { decodeRecoveryRouteState, encodeRecoveryRouteState } from "./recoveryRoute";

describe("RecoveryRouteState", () => {
  it("round-trips an explicit continuation target", () => {
    const encoded = encodeRecoveryRouteState({
      documentId: "document-1",
      reason: "parse_failed",
    });

    expect(encoded.toString()).toBe("document=document-1&recovery=continue&recovery_reason=parse_failed");
    expect(decodeRecoveryRouteState(encoded)).toEqual({ documentId: "document-1", reason: "parse_failed" });
  });

  it("rejects incomplete and unknown recovery state instead of guessing an old target", () => {
    expect(decodeRecoveryRouteState(new URLSearchParams("document=document-1&recovery=continue"))).toBeNull();
    expect(decodeRecoveryRouteState(new URLSearchParams("document=document-1&recovery=continue&recovery_reason=ocr_timeout"))).toBeNull();
    expect(decodeRecoveryRouteState(new URLSearchParams("document=document-1&recovery=resume&recovery_reason=parse_failed"))).toBeNull();
  });
});
