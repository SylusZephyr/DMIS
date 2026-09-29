import { expect, test } from "@playwright/test";
import { en, marketNames, prime, statValue, zh } from "./helpers";

test.beforeEach(async ({ page }) => { await prime(page); });

test("home loads with KPI numbers", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: en.home.title })).toBeVisible();
  for (const label of [en.home.markets, en.home.products]) {
    await expect(statValue(page, label)).toHaveText(/^[\d,]+$/);
  }
  await expect(statValue(page, en.home.markets)).not.toHaveText("0");
  await expect(statValue(page, en.home.revenue)).toHaveText(/^\$[\d.]+[KMB]?$/);
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
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: en.home.title })).toBeVisible();
  await page.getByRole("group", { name: en.a11y.language }).first().getByRole("button", { name: "中文" }).click();
  await expect(page.getByRole("heading", { level: 1, name: zh.home.title })).toBeVisible();
  await expect(page.getByRole("link", { name: zh.nav.markets }).first()).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: zh.home.title })).toBeVisible(); // persisted
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("shows the menu button and opens the navigation", async ({ page }) => {
    await page.goto("/");
    const menu = page.getByRole("button", { name: en.nav.menu });
    await expect(menu).toBeVisible();
    await menu.click();
    await expect(page.getByRole("link", { name: en.nav.launch })).toBeVisible();
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
  expect(bg).toBe("rgb(4, 7, 13)");
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
  await page.getByRole("button", { name: en.search.go }).click();
  await expect(page.getByText(en.search.kind.segment, { exact: true })).toBeVisible();
  await page.locator('a[href*="scope=segment"]').first().click();
  await expect(page).toHaveURL(/\/intelligence\?.*scope=segment/);
  await expect(page.getByRole("button", { name: en.kn.why })).toBeVisible();          // the leaf card of that sub-category
});
