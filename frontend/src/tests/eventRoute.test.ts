import { describe, expect, it } from "vitest";

import { parseSelectedEventRoute } from "../app/eventRoute";

describe("parseSelectedEventRoute", () => {
  it("does not treat the event creation route as a Case", () => {
    expect(parseSelectedEventRoute("/events/new")).toBeNull();
    expect(parseSelectedEventRoute("/events/new/")).toBeNull();
    expect(parseSelectedEventRoute("/events/%6Eew")).toBeNull();
    expect(parseSelectedEventRoute("/events/NEW")).toBeNull();
  });

  it("parses an encoded Case id and distinguishes overview from child pages", () => {
    expect(parseSelectedEventRoute("/events/case%20one")).toEqual({
      caseId: "case one",
      isOverview: true,
    });
    expect(parseSelectedEventRoute("/events/case%20one/monitor")).toEqual({
      caseId: "case one",
      isOverview: false,
    });
  });

  it("ignores event collection and unrelated routes", () => {
    expect(parseSelectedEventRoute("/events")).toBeNull();
    expect(parseSelectedEventRoute("/monitoring")).toBeNull();
  });

  it("fails closed for malformed encoded Case ids without throwing", () => {
    expect(parseSelectedEventRoute("/events/%")).toBeNull();
    expect(parseSelectedEventRoute("/events/%E0%A4%A")).toBeNull();
  });

  it("does not exclude real Case ids that only contain the reserved word", () => {
    expect(parseSelectedEventRoute("/events/new-case")).toEqual({
      caseId: "new-case",
      isOverview: true,
    });
    expect(parseSelectedEventRoute("/events/renew")).toEqual({
      caseId: "renew",
      isOverview: true,
    });
  });
});
