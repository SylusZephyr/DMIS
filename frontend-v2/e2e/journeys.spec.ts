import { expect, test } from "@playwright/test";
import { en, marketNames, prime, zh } from "./helpers";

test.beforeEach(async ({ page }) => { await prime(page); });

test("mission control loads with key figures, opportunities and the attention queue", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(new RegExp(`${en.mc.morning}|${en.mc.afternoon}|${en.mc.evening}`));
  const kpis = page.getByRole("region", { name: en.mc.kpis });
  await expect(kpis.getByText(en.home.markets, { exact: true })).toBeVisible();
  await expect(kpis.locator("a[href='/markets'] .font-mono")).toHaveText(/^[1-9][\d,]*$/);
  await expect(page.getByText(en.mc.topOpps, { exact: true })).toBeVisible();
  await expect.poll(async () => page.locator("a[href*='/intelligence?market=']").count()).toBeGreaterThan(0);
  await expect(page.getByText(en.mc.attention, { exact: true })).toBeVisible();
});

test("market page shows market size and segments", async ({ page, request }) => {
  const [market] = await marketNames(request);
  await page.goto(`/markets/${encodeURIComponent(market)}`);
  await expect(page.getByText(en.market.size, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/^\$[\d.,]+[KMB]?$/).first()).toBeVisible();
  const segments = page.getByText(en.market.segmentsTitle, { exact: true });
  await expect(segments).toBeVisible();
  // the segments card lists at least one ranked segment with an opportunity level
  await expect(page.getByText(/Very high|High|Moderate|Low/).first()).toBeVisible();
});

test("opportunity board lists concepts", async ({ page }) => {
  await page.goto("/opportunities");
  await expect(page.getByRole("heading", { level: 1, name: en.board.title })).toBeVisible();
  const table = page.getByRole("table");
  await expect(table.getByRole("columnheader", { name: en.board.concept })).toBeVisible();
  await expect.poll(async () => table.locator("tbody tr").count()).toBeGreaterThan(0);
});

test("launch simulator runs a simulation", async ({ page }) => {
  await page.goto("/launch");
  await page.getByLabel(en.launch.product).fill("Soft denture reline kit");
  await page.getByLabel(`${en.launch.price} ($)`).fill("24.99");
  await page.getByRole("button", { name: en.launch.run }).click();
  await expect(page.getByText(en.launch.placedIn).first()).toBeVisible({ timeout: 45_000 });
  await expect(page.getByRole("status").filter({ hasText: en.a11y.loaded })).toHaveCount(1);
});

test("language switch EN -> 中文 changes the interface", async ({ page }) => {
  await page.goto("/opportunities");
  await expect(page.getByRole("heading", { level: 1, name: en.board.title })).toBeVisible();
  await page.getByRole("group", { name: en.a11y.language }).first().getByRole("button", { name: "中文" }).click();
  await expect(page.getByRole("heading", { level: 1, name: zh.board.title })).toBeVisible();
  await expect(page.getByRole("link", { name: zh.ia.decide }).first()).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: zh.board.title })).toBeVisible(); // persisted
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("bottom workspace bar and the menu drawer navigate", async ({ page }) => {
    await page.goto("/");
    const bar = page.getByRole("navigation", { name: en.ia.workspaces }).last();
    await bar.getByRole("link", { name: en.ia.decide }).click();
    await expect(page).toHaveURL(/\/decide$/);
    const menu = page.getByRole("button", { name: en.nav.menu });
    await menu.click();
    await expect(page.getByRole("dialog", { name: en.nav.menu }).getByRole("link", { name: en.nav.launch })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

test("theme toggle switches to light, persists and follows the system by default", async ({ browser }) => {
  const ctx = await browser.newContext({ colorScheme: "light" });
  const page = await ctx.newPage();
  await page.addInitScript(() => { try { window.localStorage.setItem("dmis_lang", "en"); } catch { /* ignore */ } });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light"); // no stored choice -> system
  await page.getByRole("button", { name: en.theme.switchTo.dark }).first().click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).toBe("rgb(3, 6, 12)");
  await ctx.close();
});

test("revisiting a market page is served from the request cache", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const api: string[] = [];
  page.on("request", (r) => { if (r.url().includes("/api/v2/") && !r.url().includes("/telemetry/")) api.push(r.url()); });
  await page.goto(`/markets/${encodeURIComponent(market)}`);
  await expect(page.getByText(en.market.segmentsTitle, { exact: true })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.getByRole("link", { name: en.nav.launch }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: en.launch.title })).toBeVisible();
  await page.goBack();
  await expect(page.getByText(en.market.segmentsTitle, { exact: true })).toBeVisible();
  await page.waitForLoadState("networkidle");
  api.length = 0;
  await page.getByRole("link", { name: en.nav.launch }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: en.launch.title })).toBeVisible();
  await page.goBack();
  await expect(page.getByText(en.market.segmentsTitle, { exact: true })).toBeVisible();
  await page.waitForTimeout(1000);
  expect(api.filter((u) => u.includes(`/markets/${encodeURIComponent(market)}/`)), "market data re-fetched on revisit").toEqual([]);
});

test("intelligence map shows leaf intelligence with value kinds and an explained opportunity", async ({ page, request }) => {
  const [market] = await marketNames(request);
  await page.goto(`/intelligence?market=${encodeURIComponent(market)}`);
  await expect(page.getByRole("heading", { level: 1, name: en.kn.mapTitle })).toBeVisible();
  await expect(page.getByText(en.kn.kindModeled).first()).toBeVisible();
  await page.getByRole("button", { name: en.kn.why }).click();
  await expect(page.getByRole("dialog", { name: en.kn.whyTitle })).toBeVisible();
  await expect(page.getByText(en.kn.current, { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");                          // keyboard users can close the drawer
  await expect(page.getByRole("dialog", { name: en.kn.whyTitle })).toHaveCount(0);
  await page.getByRole("tab", { name: en.kn.heatmap }).click();
  await expect.poll(async () => page.getByRole("table").locator("tbody tr").count()).toBeGreaterThan(0);
});

test("review queues show every queue, including anomalies", async ({ page, request }) => {
  const [market] = await marketNames(request);
  await page.goto(`/review?market=${encodeURIComponent(market)}`);
  await expect(page.getByRole("heading", { level: 1, name: en.kn.reviewTitle })).toBeVisible();
  for (const tab of [en.kn.tabs.taxonomy, en.kn.tabs.relevance, en.kn.tabs.anomalies, en.kn.tabs.duplicates]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByRole("tab", { name: tab })).toHaveAttribute("aria-selected", "true");
  }
});

test("import preview maps columns with confidence and reports data quality", async ({ page }) => {
  await page.goto("/import");
  const csv = "Item Code,Product Name,Maker,Cost to customer,Units/Month\n"
    + "B0SYN00001,Brushless Micromotor 50000 RPM Dental Lab,Acme,149.9,40\n"
    + "B0SYN00002,Brushed Micromotor 35000 RPM N3 Control Box,Beta,-5,12\n"
    + "B0SYN00003,Dental Lab Micromotor 45000 RPM H37L1 Handpiece,Acme,99.5,25\n";
  await page.getByLabel(en.kn.chooseFile).setInputFiles({ name: "micromotors.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: en.kn.previewBtn }).click();
  await expect(page.getByLabel(`${en.kn.detected} title`)).toHaveValue("Product Name");
  await expect(page.getByLabel(`${en.kn.detected} sales`)).toHaveValue("Units/Month");
  await expect(page.getByText(en.kn.sample)).toBeVisible();
});

test("global search finds sub-categories and links into the intelligence map", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const segs = await (await request.get(`/api/v2/markets/${encodeURIComponent(market)}/capacity?scope=segment`)).json();
  const word = String(segs.rows[0].label).split(/\s+/)[0];
  await page.goto("/search");
  await page.getByLabel(en.search.label).fill(word);
  await page.getByRole("main").getByRole("button", { name: en.search.go, exact: true }).click();   // not the top bar's search
  await expect(page.getByText(en.search.kind.segment, { exact: true })).toBeVisible();
  await page.locator('a[href*="scope=segment"]').first().click();
  await expect(page).toHaveURL(/\/intelligence\?.*scope=segment/);
  await expect(page.getByRole("button", { name: en.kn.why })).toBeVisible();          // the leaf card of that sub-category
});

test("command palette finds a page and opens it from the keyboard", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const dialog = page.getByRole("dialog", { name: en.ia.palette });
  // the heading is server-rendered; the shortcut listener attaches on hydration, so press until the palette opens
  await expect(async () => {
    await page.keyboard.press("Control+k");
    await expect(dialog).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15000 });
  await dialog.getByRole("combobox").fill("opportunity board");
  await expect(dialog.getByRole("option").first()).toContainText(en.nav.opportunities);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: en.board.title })).toBeVisible();
  await expect(dialog).toHaveCount(0);
});

test("command palette searches markets and switches the market context", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const rows = (await (await request.get("/api/v2/markets")).json()) as { name: string; display_name?: string | null }[];
  const name = rows.find((r) => r.name === market)?.display_name ?? market;
  await page.goto("/decide");
  await page.getByRole("button", { name: en.ia.searchEverything }).first().click();
  const dialog = page.getByRole("dialog", { name: en.ia.palette });
  await dialog.getByRole("combobox").fill(name);
  await dialog.getByRole("option", { name: new RegExp(en.ia.switchTo.replace("{m}", ".*")) }).first().click();
  await expect(page.getByRole("button", { name: en.mkt.label })).toContainText(new RegExp(name, "i"));
});

test("workspace hubs list their tools and breadcrumbs lead back", async ({ page }) => {
  await page.goto("/decide");
  await expect(page.getByRole("heading", { level: 1, name: en.ia.decide })).toBeVisible();
  await page.getByRole("main").getByRole("link", { name: new RegExp(en.nav.launch) }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: en.launch.title })).toBeVisible();
  const crumbs = page.getByRole("navigation", { name: en.ia.breadcrumb });
  await expect(crumbs).toContainText(en.nav.launch);
  await crumbs.getByRole("link", { name: en.ia.decide }).click();
  await expect(page).toHaveURL(/\/decide$/);
});

test("the global view keeps the globe and the leading brands", async ({ page }) => {
  await page.goto("/command");
  await expect(page.getByRole("heading", { level: 1, name: en.nav.command })).toBeVisible();
  await expect(page.getByText(en.home.brands, { exact: true })).toBeVisible();
});

test("live data page lists the Amazon providers and the Chinese marketplaces", async ({ page }) => {
  await page.goto("/connectors");
  await expect(page.getByRole("heading", { level: 1, name: en.conn.title })).toBeVisible();
  await expect(page.getByText("Keepa", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(en.conn.marketplaces, { exact: true })).toBeVisible();
  await expect(page.getByText("1688", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/USD\/CNY \d/)).toBeVisible();                   // the FX rate used, with its date
});

test("unit economics recomputes profit from the inputs on the server", async ({ page }) => {
  await page.goto("/economics");
  await expect(page.getByRole("heading", { level: 1, name: en.econ.title })).toBeVisible();
  await expect(page.getByText(en.econ.contribution, { exact: true })).toBeVisible();
  const profit = page.getByText(en.econ.profit, { exact: true }).first().locator("xpath=following-sibling::div[1]");
  await expect(profit).toHaveText(/^\$\d+\.\d\d$/);
  const before = await profit.textContent();
  await page.getByLabel(en.econ.f.price).fill("30");
  await expect(profit).not.toHaveText(before ?? "");                         // a higher price earns more per unit
});

test("supplier sourcing previews a concept from a product idea", async ({ page, request }) => {
  const [market] = await marketNames(request);
  await page.goto(`/sourcing?market=${encodeURIComponent(market)}`);
  await expect(page.getByRole("heading", { level: 1, name: en.src.title })).toBeVisible();
  await page.getByLabel(en.src.idea).fill("soft denture reline kit");
  await page.getByRole("button", { name: en.src.preview }).click();
  await expect(page.getByText(en.src.maxFob, { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(en.src.conceptSub)).toBeVisible();
});

test("review queue: keyboard triage moves, selects and records a relevance decision", async ({ page, request }) => {
  const [market] = await marketNames(request);
  await page.goto(`/review?market=${encodeURIComponent(market)}`);
  await page.getByRole("tab", { name: en.kn.tabs.relevance }).click();
  const list = page.getByRole("list", { name: en.kn.tabs.relevance });
  await expect(list.locator("li").first()).toBeVisible();
  const before = await list.locator("li").count();
  await expect(list.locator("li[aria-current='true']")).toHaveCount(1);
  const firstTitle = await list.locator("li").first().locator(".text-sm.text-ink").first().textContent();
  await page.keyboard.press("j");                                              // focus moves to the second row
  await expect(list.locator("li").nth(1)).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("x");                                              // select it
  await expect(page.getByText(en.triage.selected.replace("{n}", "1"))).toBeVisible();
  await page.keyboard.press("k");
  await page.keyboard.press("y");                                              // decide the first row: dental
  await expect(page.getByRole("status").filter({ hasText: en.triage.relevanceSaved.replace("{n}", "1").slice(0, 8) })).toBeVisible();
  await expect(list.locator("li")).toHaveCount(before - 1);                    // decided rows leave the queue view
  await expect(list.locator("li").first().locator(".text-sm.text-ink").first()).not.toHaveText(firstTitle ?? "");
});

test("competitor watchlist: watch a listing from its snapshot data, then stop watching", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const prods = await (await request.get(`/api/v2/markets/${encodeURIComponent(market)}/products?limit=5&sort=revenue`)).json();
  const watched = new Set(((await (await request.get("/api/v2/watchlist")).json()).items as { asin: string }[]).map((i) => i.asin));
  const asin = (prods.items as { best_listing: string | null }[]).map((p) => p.best_listing).find((a) => a && a.length === 10 && !watched.has(a));
  expect(asin, "a listing that is not watched yet").toBeTruthy();
  await page.goto("/watchlist");
  await expect(page.getByRole("heading", { level: 1, name: en.watch.title })).toBeVisible();
  const form = page.locator("form");
  await form.getByLabel(en.watch.asin).fill(asin!);
  await form.getByLabel(en.watch.market).selectOption(market);
  await form.getByRole("button", { name: en.watch.addBtn, exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: asin! });
  await expect(row).toBeVisible();
  await expect(row.getByText(/^\$\d+\.\d\d$/)).toBeVisible();                      // latest price from the processed snapshot
  await expect(row.getByText(en.watch.observations.replace("{n}", "1"))).toBeVisible();
  await row.getByRole("button", { name: new RegExp(en.watch.remove) }).click();
  await expect(row).toHaveCount(0);
});

test("keyword demand: import a keyword export and read demand by sub-category", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const csv = ["Keyword,Searches,Purchase Rate,Title Density,Products,PPC Bid",
    "dental kit,12000,6%,4,300,$1.20", "denture care,8000,5%,9,500,$0.90", "zzqx unmatched phrase,300,1%,1,10,$0.30"].join("\n");
  await page.goto(`/keywords?market=${encodeURIComponent(market)}`);
  await expect(page.getByRole("heading", { level: 1, name: en.kw.title })).toBeVisible();
  await page.locator("input[type=file]").setInputFiles({ name: "kw.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: en.kw.import, exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: en.kw.mapping })).toBeVisible();
  const table = page.getByRole("region", { name: en.kw.table });
  await expect(table.getByRole("row")).toHaveCount(4);                          // header + 3 keywords, none dropped
  await expect(table.getByRole("row").filter({ hasText: "zzqx unmatched phrase" }).getByText(en.kw.unassigned)).toBeVisible();
  await table.getByRole("button", { name: en.kw.col.searches }).click();        // sort by searches, largest first
  await expect(table.getByRole("row").nth(1)).toContainText("dental kit");
});

test("own sales: import a Business Report for a period and see it listed", async ({ page }) => {
  const csv = ["(Child) ASIN,Title,Units Ordered,Ordered Product Sales,Sessions - Total",
    "B0E2ETEST1,E2E own listing,62,\"$1,860.00\",900", "Total,,62,,900"].join("\n");
  await page.goto("/own-sales");
  await expect(page.getByRole("heading", { level: 1, name: en.own.title })).toBeVisible();
  await page.locator("input[type=file]").setInputFiles({ name: "BusinessReport.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByLabel(en.own.start).fill("2026-08-01");
  await page.getByLabel(en.own.end).fill("2026-08-31");
  await page.getByRole("button", { name: en.own.import, exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "2026-08-01" })).toBeVisible();
  const reports = page.getByRole("region", { name: en.own.reports });
  const row = reports.getByRole("row").filter({ hasText: "B0E2ETEST1" });
  await expect(row).toBeVisible();
  await expect(row).toContainText("(31d)");                                    // the period's length, for per-month numbers
  await expect(reports.getByRole("row").filter({ hasText: "Total" })).toHaveCount(0);   // the totals line is not a listing
});

test("market page shows the number-integrity check with what failed", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const rep = await (await request.get(`/api/v2/markets/${encodeURIComponent(market)}/integrity`)).json();
  await page.goto(`/markets/${encodeURIComponent(market)}`);
  const card = page.getByRole("region", { name: en.integ.title });
  await expect(card.getByText(en.integ.status[rep.status as "pass" | "warn" | "fail"])).toBeVisible();
  const issues = (rep.checks as { status: string; id: string }[]).filter((c) => c.status !== "pass");
  if (issues.length) {
    await card.getByRole("button", { name: en.integ.show }).click();
    await expect(card.getByText(en.integ.check[issues[0].id as keyof typeof en.integ.check], { exact: true })).toBeVisible();
  }
});

test("product page translates the listing title into the interface language, labelled as machine output", async ({ page, request }) => {
  const [market] = await marketNames(request);
  const prods = await (await request.get(`/api/v2/markets/${encodeURIComponent(market)}/products?limit=1&sort=revenue`)).json();
  const id = (prods.items as { product_id: string }[])[0].product_id;
  await page.goto(`/products/${encodeURIComponent(id)}`);
  await page.getByRole("group", { name: en.a11y.language }).first().getByRole("button", { name: "中文" }).click();
  await page.getByRole("button", { name: zh.translate.button, exact: true }).click();
  // without an AI key on the server the page says so; with one it shows the labelled translation
  await expect(page.getByText(zh.translate.status.unavailable).or(page.getByText(zh.translate.machine))).toBeVisible();
});

test("analyst answers a complaints question and names the intent in words", async ({ page }) => {
  await page.goto("/analyst");
  const box = page.getByRole("textbox").first();
  await box.fill("What do customers complain about?");
  await box.press("Enter");
  await expect(page.getByText(en.analyst.intents.pain, { exact: true })).toBeVisible();
  await expect(page.getByText("analyst.intents.", { exact: false })).toHaveCount(0);
});
