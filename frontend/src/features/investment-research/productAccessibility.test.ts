// @ts-expect-error Vitest executes this regression check in Node; app builds omit Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const productCss = readFileSync("src/styles/underwriting-research.css", "utf8");

function channel(value: number): number {
  const normalized = value / 255;
  return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const rgb = [1, 3, 5].map((start) => channel(Number.parseInt(hex.slice(start, start + 2), 16)));
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

function contrast(left: string, right: string): number {
  const [lighter, darker] = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

describe("investment research product contrast", () => {
  it("keeps muted and amber text at 4.5:1 across product surfaces", () => {
    expect(productCss).toContain("--ros-paper");
    const color = (name: string) => productCss.match(new RegExp(`${name}:\\s*(#[0-9a-f]{6})`))?.[1] ?? "";
    const paper = color("--ros-paper");
    const surface = color("--ros-surface");
    const deep = color("--ros-deep");
    const muted = color("--ros-muted");
    const amber = color("--ros-amber");
    const amberSoft = color("--ros-amber-soft");
    expect([paper, surface, deep, muted, amber, amberSoft]).not.toContain("");
    for (const background of [paper, surface, deep]) expect(contrast(muted, background)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(amber, amberSoft)).toBeGreaterThanOrEqual(4.5);
  });
});
