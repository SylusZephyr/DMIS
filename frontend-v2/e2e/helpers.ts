import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { en } from "../messages/en";
import { zh } from "../messages/zh";

export { en, zh };

/** Start every test from a known interface state (English, dark theme unless told otherwise). */
export async function prime(page: Page, opts: { lang?: "en" | "zh"; theme?: "light" | "dark" } = {}) {
  await page.addInitScript(({ lang, theme }) => {
    try {
      if (window.sessionStorage.getItem("e2e_primed")) return; // only once per tab, so reloads keep user choices
      window.localStorage.setItem("dmis_lang", lang);
      window.localStorage.setItem("dmis_theme", theme);
      window.sessionStorage.setItem("e2e_primed", "1");
    } catch { /* storage blocked: defaults apply */ }
  }, { lang: opts.lang ?? "en", theme: opts.theme ?? "dark" });
}

/** Markets present in the seeded data (the tests do not assume particular names). */
export async function marketNames(request: APIRequestContext): Promise<string[]> {
  const r = await request.get("/api/v2/markets");
  expect(r.ok(), "API reachable through the web app's /api/v2 proxy").toBeTruthy();
  const rows = (await r.json()) as { name: string }[];
  expect(rows.length, "seeded data has at least one market (run `python scripts/dmis.py bootstrap`)").toBeGreaterThan(0);
  return rows.map((m) => m.name);
}

/** The value shown under a <Stat label="..."> tile. */
export function statValue(page: Page, label: string) {
  return page.getByText(label, { exact: true }).first().locator("xpath=following-sibling::div[1]");
}
