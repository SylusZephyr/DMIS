// Bundle report for a finished `next build` (Turbopack does not print per-route sizes).
//
//   npm run build && npm run report:bundle            -> table on stdout
//   npm run report:bundle -- --json > bundle.json     -> machine-readable
//   npm run report:bundle -- --max-first-load=300     -> exit 1 if a non-3D route exceeds 300 KB (gzip)
//
// First-load JS of a route = the root main files + every chunk in the route's entryJSFiles (layout + page),
// read from .next/server/app/**/page_client-reference-manifest.js. Chunks pulled in later by dynamic
// import() (echarts, three.js, sigma, mapbox) are listed separately as "largest chunks": they only download
// on the pages that render them.
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const next = path.join(root, ".next");
const args = process.argv.slice(2);
const asJson = args.includes("--json");
const maxArg = args.find((a) => a.startsWith("--max-first-load="));
const maxKb = maxArg ? Number(maxArg.split("=")[1]) : null;
// Routes whose purpose is a 3D / WebGL / map view are allowed to be heavy.
const HEAVY_OK = [/^\/$/, /^\/universe$/, /^\/galaxy\/\[market\]$/, /^\/graph$/];

if (!fs.existsSync(path.join(next, "build-manifest.json"))) {
  console.error("No .next build found; run `npm run build` first.");
  process.exit(2);
}
const build = JSON.parse(fs.readFileSync(path.join(next, "build-manifest.json"), "utf8"));
const sizeCache = new Map();
function size(rel) {
  if (!sizeCache.has(rel)) {
    const buf = fs.readFileSync(path.join(next, rel));
    sizeCache.set(rel, { raw: buf.length, gz: zlib.gzipSync(buf, { level: 9 }).length });
  }
  return sizeCache.get(rel);
}
const sum = (files) => files.reduce((a, f) => ({ raw: a.raw + size(f).raw, gz: a.gz + size(f).gz }), { raw: 0, gz: 0 });

const manifests = [];
(function walk(d) {
  for (const f of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, f.name);
    if (f.isDirectory()) walk(p);
    else if (f.name === "page_client-reference-manifest.js") manifests.push(p);
  }
})(path.join(next, "server", "app"));

const rootMain = build.rootMainFiles ?? [];
const routes = [];
for (const file of manifests) {
  const ctx = { globalThis: { __RSC_MANIFEST: {} } };
  new Function("globalThis", fs.readFileSync(file, "utf8"))(ctx.globalThis);
  const [name, m] = Object.entries(ctx.globalThis.__RSC_MANIFEST)[0];
  const route = name.replace(/\/page$/, "") || "/";
  if (route.startsWith("/_")) continue;
  const files = new Set(rootMain);
  for (const [entry, chunks] of Object.entries(m.entryJSFiles ?? {})) {
    if (entry.includes("global-error")) continue;
    for (const c of chunks) if (c.endsWith(".js")) files.add(c);
  }
  const s = sum([...files]);
  routes.push({ route, chunks: files.size, rawKB: +(s.raw / 1024).toFixed(1), gzipKB: +(s.gz / 1024).toFixed(1),
    heavyOk: HEAVY_OK.some((r) => r.test(route)) });
}
routes.sort((a, b) => a.route.localeCompare(b.route));

const chunkDir = path.join(next, "static", "chunks");
const all = fs.readdirSync(chunkDir).filter((f) => f.endsWith(".js")).map((f) => `static/chunks/${f}`);
const largest = all.map((f) => ({ file: f, rawKB: +(size(f).raw / 1024).toFixed(1), gzipKB: +(size(f).gz / 1024).toFixed(1) }))
  .sort((a, b) => b.rawKB - a.rawKB).slice(0, 10);
const total = sum(all);
const report = { routes, largestChunks: largest, totalJs: { rawKB: +(total.raw / 1024).toFixed(1), gzipKB: +(total.gz / 1024).toFixed(1), files: all.length } };

if (asJson) console.log(JSON.stringify(report, null, 2));
else {
  console.log("First-load JS per route (KB)            raw      gzip");
  for (const r of routes) console.log(`  ${r.route.padEnd(34)} ${String(r.rawKB).padStart(8)} ${String(r.gzipKB).padStart(9)}${r.heavyOk ? "  (3D/map)" : ""}`);
  console.log("\nLargest chunks (KB)                     raw      gzip");
  for (const c of largest) console.log(`  ${c.file.padEnd(34)} ${String(c.rawKB).padStart(8)} ${String(c.gzipKB).padStart(9)}`);
  console.log(`\nAll JS: ${report.totalJs.files} files, ${report.totalJs.rawKB} KB raw, ${report.totalJs.gzipKB} KB gzip`);
}
if (maxKb != null) {
  const over = routes.filter((r) => !r.heavyOk && r.gzipKB > maxKb);
  if (over.length) { console.error(`\nOver ${maxKb} KB gzip first-load: ${over.map((r) => `${r.route} (${r.gzipKB})`).join(", ")}`); process.exit(1); }
}
