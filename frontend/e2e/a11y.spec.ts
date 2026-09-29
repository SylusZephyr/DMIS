import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { marketNames, prime } from "./helpers";

// WCAG 2.1 A/AA automated checks (axe-core) on the key pages, in both themes.
const PAGES = ["/", "/markets", "/opportunities", "/launch", "/competitors", "/analyst", "/data", "/alerts", "/methodology", "/universe",
  "/intelligence", "/review", "/import", "/search?q=denture"];

for (const theme of ["dark", "light"] as const) {
  test.describe(`axe (${theme})`, () => {
    test.beforeEach(async ({ page }) => { await prime(page, { theme }); });
    for (const path of [...PAGES, "MARKET"]) {
      test(path === "MARKET" ? "/markets/[market]" : path, async ({ page, request }) => {
        const url = path === "MARKET" ? `/markets/${encodeURIComponent((await marketNames(request))[0])}` : path;
        await page.goto(url);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(800);
        const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        const summary = res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} × ${v.nodes[0]?.target.join(" ")}`);
        expect(summary, `axe violations on ${url} [${theme}]`).toEqual([]);
      });
    }
  });
}
