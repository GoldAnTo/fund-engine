import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const gatewayCss = readFileSync(
  resolve(
    dirname(fileURLToPath(import.meta.url)),
    "../workbench/GatewayWorkbench.css",
  ),
  "utf8",
);

describe("Gateway compact workbench layout", () => {
  it("gives the reader remaining width without a separate explanatory column", () => {
    expect(gatewayCss).toContain(
      "@media (min-width: 761px) and (max-width: 1024px)",
    );
    expect(gatewayCss).toContain(
      'grid-template-areas: "topbar topbar" "sidebar main"',
    );
    expect(gatewayCss).toMatch(
      /\.gateway-side-panel\s*\{[^}]*position:\s*static[^}]*transform:\s*none/s,
    );
  });

  it("allows a full accessible research title to wrap beside its connection state", () => {
    expect(gatewayCss).toMatch(
      /\.gateway-conversation \.research-title h1\s*\{[^}]*white-space:\s*normal[^}]*overflow-wrap:\s*anywhere/s,
    );
  });

  it("keeps the continue-research composer reachable while a long history scrolls", () => {
    expect(gatewayCss).toMatch(
      /\.gateway-conversation \.gateway-composer\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0/s,
    );
  });

  it("collapses mobile navigation and gives the research its own document flow", () => {
    expect(gatewayCss).toContain('grid-template-areas: "topbar" "main"');
    expect(gatewayCss).toMatch(
      /\.gateway-workbench \.workbench-sidebar,\s*\.gateway-workbench \.gateway-side-panel\s*\{[^}]*position:\s*static[^}]*transform:\s*none/s,
    );
  });
});
