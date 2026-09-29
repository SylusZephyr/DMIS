import { describe, expect, it } from "vitest";
import { resolveColor, themeOption } from "@/lib/chart-theme";
import { SEQ } from "@/lib/viz";

const tokens = { "--ink-3": "#53627a", "--panel": "#ffffff", "--line": "#d3dce8" };

describe("chart theming", () => {
  it("resolves var(--token) strings and legacy dark literals", () => {
    expect(resolveColor("var(--ink-3)", tokens, "light")).toBe("#53627a");
    expect(resolveColor("#62789a", tokens, "light")).toBe("#53627a");
    expect(resolveColor("#0A1220", tokens, "dark")).toBe("#ffffff");
    expect(resolveColor("#3987e5", tokens, "light")).toBe("#3987e5"); // series colours are left alone
  });
  it("reverses the sequential ramp only on the light theme", () => {
    expect(resolveColor(SEQ[0], tokens, "light")).toBe(SEQ[SEQ.length - 1]);
    expect(resolveColor(SEQ[0], tokens, "dark")).toBe(SEQ[0]);
  });
  it("walks nested options, keeps functions and does not mutate the input", () => {
    const fmt = (v: number) => `${v}`;
    const opt = { axisLabel: { color: "var(--ink-3)", formatter: fmt }, series: [{ itemStyle: { borderColor: "#0a1220" } }], name: "#tag" };
    const out = themeOption(opt, tokens, "light");
    expect(out.axisLabel.color).toBe("#53627a");
    expect(out.axisLabel.formatter).toBe(fmt);
    expect(out.series[0].itemStyle.borderColor).toBe("#ffffff");
    expect(out.name).toBe("#tag");
    expect(opt.axisLabel.color).toBe("var(--ink-3)");
  });
});
