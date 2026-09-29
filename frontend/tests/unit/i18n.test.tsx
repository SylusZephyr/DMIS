import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LangProvider, useI18n } from "@/lib/i18n";
import { en } from "@/messages/en";
import { zh } from "@/messages/zh";

function Probe({ k, vars }: { k: string; vars?: Record<string, string | number> }) {
  const { t, lang, setLang } = useI18n();
  return (
    <div>
      <span data-testid="out">{t(k, vars)}</span>
      <span data-testid="lang">{lang}</span>
      <button onClick={() => setLang(lang === "en" ? "zh" : "en")}>switch</button>
    </div>
  );
}
const out = () => screen.getByTestId("out").textContent;

afterEach(() => { window.localStorage.clear(); });

describe("i18n t()", () => {
  it("interpolates {vars} (every occurrence) and leaves text without vars alone", () => {
    render(<LangProvider><Probe k="a11y.chartSummary" vars={{ title: "Price map" }} /></LangProvider>);
    expect(out()).toBe(en.a11y.chartSummary.replaceAll("{title}", "Price map"));
    expect(out()).not.toContain("{title}");
  });
  it("interpolates numbers", () => {
    render(<LangProvider><Probe k="graph.counts" vars={{ n: 12, e: 3 }} /></LangProvider>);
    expect(out()).toBe(en.graph.counts.replace("{n}", "12").replace("{e}", "3"));
  });
  it("returns the key itself for an unknown key", () => {
    render(<LangProvider><Probe k="does.not.exist" /></LangProvider>);
    expect(out()).toBe("does.not.exist");
  });
  it("switches to Chinese and persists the choice", () => {
    render(<LangProvider><Probe k="nav.menu" /></LangProvider>);
    expect(out()).toBe(en.nav.menu);
    act(() => { screen.getByText("switch").click(); });
    expect(screen.getByTestId("lang").textContent).toBe("zh");
    expect(out()).toBe(zh.nav.menu);
    expect(window.localStorage.getItem("dmis_lang")).toBe("zh");
    expect(document.documentElement.lang).toBe("zh-CN");
  });
});
