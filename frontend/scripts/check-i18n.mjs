// Every literal t("key") used in the app must exist in both en and zh, and both dictionaries must have the
// same keys. Dynamic keys (template literals) are checked by their static prefix only.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
function load(file, name) {
  const src = fs.readFileSync(path.join(root, file), "utf8");
  const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const m = { exports: {} };
  new Function("module", "exports", js)(m, m.exports);
  return m.exports[name];
}
function flatten(o, pre = "", out = new Set()) {
  for (const [k, v] of Object.entries(o)) {
    const key = pre ? `${pre}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, key, out);
    else if (Array.isArray(v)) { out.add(key); v.forEach((_, i) => out.add(`${key}.${i}`)); }
    else out.add(key);
  }
  return out;
}
const en = flatten(load("messages/en.ts", "en"));
const zh = flatten(load("messages/zh.ts", "zh"));
const problems = [];
for (const k of en) if (!zh.has(k)) problems.push(`zh missing: ${k}`);
for (const k of zh) if (!en.has(k)) problems.push(`en missing: ${k}`);
const files = [];
(function walk(d) {
  for (const f of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, f.name);
    if (f.isDirectory() && !["node_modules", ".next"].includes(f.name)) walk(p);
    else if (/\.tsx?$/.test(f.name)) files.push(p);
  }
})(path.join(root, "app")); 
(function walk(d) {
  for (const f of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, f.name);
    if (f.isDirectory()) walk(p); else if (/\.tsx?$/.test(f.name)) files.push(p);
  }
})(path.join(root, "components"));
const prefixes = new Set([...en].map((k) => k.split(".").slice(0, -1).join(".")));
for (const f of files) {
  const src = fs.readFileSync(f, "utf8");
  for (const m of src.matchAll(/\bt\(\s*"([a-zA-Z0-9_.]+)"/g)) {
    if (!en.has(m[1])) problems.push(`${path.relative(root, f)}: unknown key ${m[1]}`);
  }
  for (const m of src.matchAll(/\bt\(\s*`([a-zA-Z0-9_.]+)\.\$\{/g)) {
    if (![...prefixes].some((p) => p === m[1] || p.startsWith(m[1] + "."))) problems.push(`${path.relative(root, f)}: unknown key prefix ${m[1]}`);
  }
}
// user-visible English written straight into JSX (text between tags, or a title / subtitle / placeholder /
// label prop): two or more words starting with a capital letter must come from the dictionaries instead.
const ALLOW = new Set(["Intelligence OS"]);
for (const f of files) {
  if (!f.endsWith(".tsx")) continue;
  const src = fs.readFileSync(f, "utf8");
  const hits = [...src.matchAll(/>\s*([A-Z][a-z]+(?: [A-Za-z()]+)+[.:]?)\s*</g), ...src.matchAll(/\b(?:title|subtitle|placeholder|label)="([A-Z][a-z]+ [^"]*)"/g)];
  for (const m of hits) if (!ALLOW.has(m[1].trim())) problems.push(`${path.relative(root, f)}: hard-coded text "${m[1].trim()}"`);
}
if (problems.length) { console.error(problems.join("\n")); process.exit(1); }
console.log(`i18n ok: ${en.size} keys in both languages, ${files.length} files checked`);
