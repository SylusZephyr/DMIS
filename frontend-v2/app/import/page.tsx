"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Input, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post } from "@/lib/api";
import { pct } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { OfflineEvidenceCard } from "@/components/offline-evidence";

type Field = { field: string; column: string | null; confidence: number | null; level: "confident" | "confirm" | "uncertain" | "unmapped";
  alternatives: { column: string; score: number }[]; pinned: boolean };
type Quality = { column: string; missing_share: number; invalid: number; examples: string[]; rule?: string; duplicates?: number };
type Preview = { file: string; adapter: string; rows_sampled: number; columns: { name: string; non_null: number; examples: string[] }[];
  fields: Field[]; unmapped_columns: string[]; warnings: string[]; quality: Record<string, Quality>;
  sample: Record<string, unknown>[]; needs_confirmation: string[] };

const LEVEL_COLOR: Record<Field["level"], string> = { confident: "var(--good)", confirm: "var(--warn)", uncertain: "var(--bad)", unmapped: "var(--ink-3)" };

/** A date in a file name: 2026-09-15, 2026_09_15, 20260915 or 2026-09 (-> the 1st), as the pipeline reads it. */
function dateInName(name: string): string | null {
  const m = name.match(/(20\d\d)[-_.]?(0[1-9]|1[0-2])(?:[-_.]?(0[1-9]|[12]\d|3[01]))?/);
  return m ? `${m[1]}-${m[2]}-${m[3] ?? "01"}` : null;
}

export default function ImportPage() {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [pv, setPv] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [market, setMarket] = useState("");
  // the export date: every number of the upload is "as of" this day (from the file name, else today; editable)
  const [snapshot, setSnapshot] = useState(() => new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<string | null>(null);

  const runPreview = async (override?: Record<string, string>) => {
    if (!file) return;
    setBusy(true); setError(null); setJob(null);
    const fd = new FormData();
    fd.append("file", file);
    if (override && Object.keys(override).length) fd.append("mapping", JSON.stringify(override));
    try {
      const r = await post<Preview>("/imports/preview", fd);
      setPv(r);
      setMapping(Object.fromEntries(r.fields.filter((f) => f.column).map((f) => [f.field, f.column as string])));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!file || !market.trim()) return;
    setBusy(true); setError(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("market", market.trim());
    if (snapshot) fd.append("snapshot_date", snapshot);
    fd.append("mapping", JSON.stringify(mapping));
    try {
      const r = await post<{ job_id: string }>("/datasets", fd);
      setJob(r.job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const setField = (field: string, column: string) => {
    const next = { ...mapping };
    if (column) next[field] = column; else delete next[field];
    setMapping(next);
  };

  return (
    <div className="space-y-5 p-4 md:p-6">
      <PageHeader title={t("kn.importTitle")} subtitle={t("kn.importSub")} />
      <ErrorNote error={error} />
      <Card>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <label className="text-sm text-ink-2" htmlFor="import-file">{t("kn.chooseFile")}</label>
          <input id="import-file" type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.json,.jsonl" className="text-sm"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f); setPv(null); setJob(null);
              const d = f && dateInName(f.name);                     // "..._2026-09.xlsx" -> that month's first day
              if (d) setSnapshot(d);
            }} />
          <Button size="sm" disabled={!file || busy} onClick={() => runPreview()}>{t("kn.previewBtn")}</Button>
        </div>
      </Card>

      {pv && (
        <>
          <Card>
            <CardHeader title={pv.file} subtitle={`${t("kn.adapter")}: ${pv.adapter} · ${pv.rows_sampled} ${t("kn.rows")}`} />
            {pv.warnings.length > 0 && <ul className="px-4 text-xs text-warn">{pv.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
            <div className="overflow-x-auto p-2">
              <table className="w-full min-w-[48rem] text-sm">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wide text-ink-3">
                    <th scope="col" className="px-2 py-2">{t("kn.field")}</th><th scope="col" className="px-2">{t("kn.detected")}</th>
                    <th scope="col" className="px-2">{t("kn.confidence")}</th><th scope="col" className="px-2">{t("kn.alternatives")}</th>
                    <th scope="col" className="px-2">{t("kn.quality")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {pv.fields.map((f) => {
                    const q = pv.quality[f.field];
                    return (
                      <tr key={f.field}>
                        <th scope="row" className="px-2 py-1.5 text-left font-mono text-xs font-normal">{f.field}</th>
                        <td className="px-2">
                          <Select aria-label={`${t("kn.detected")} ${f.field}`} value={mapping[f.field] ?? ""} onChange={(e) => setField(f.field, e.target.value)}>
                            <option value="">{t("kn.notMapped")}</option>
                            {pv.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                          </Select>
                        </td>
                        <td className="px-2"><Badge color={LEVEL_COLOR[f.level]}>{t(`kn.lvl.${f.level}`)}{f.confidence != null ? ` · ${pct(f.confidence)}` : ""}</Badge></td>
                        <td className="px-2 text-xs text-ink-3">{f.alternatives.map((a) => `${a.column} (${pct(a.score)})`).join(", ")}</td>
                        <td className="px-2 text-xs">
                          {q ? <span className={q.invalid ? "text-bad" : "text-ink-3"}>
                            {pct(q.missing_share)} {t("kn.missing")} · {q.invalid} {t("kn.invalid")}{q.examples.length ? ` (${q.examples.join(", ")})` : ""}
                          </span> : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <Card>
            <CardHeader title={t("kn.sample")} />
            <div className="overflow-x-auto p-2">
              <table className="w-full text-xs">
                <thead><tr>{Object.keys(pv.sample[0] ?? {}).map((k) => <th key={k} scope="col" className="px-2 py-1 text-left font-mono text-ink-3">{k}</th>)}</tr></thead>
                <tbody className="divide-y divide-line">
                  {pv.sample.map((r, i) => (
                    <tr key={i}>{Object.values(r).map((v, j) => <td key={j} className="max-w-[18rem] truncate px-2 py-1">{v == null ? "—" : String(v)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card>
            <div className="flex flex-wrap items-end gap-3 p-4">
              <label className="space-y-1 text-xs text-ink-3">
                <span>{t("kn.marketName")}</span>
                <Input value={market} onChange={(e) => setMarket(e.target.value)} className="w-56" />
              </label>
              <label className="space-y-1 text-xs text-ink-3">
                <span>{t("kn.snapshot")}</span>
                <Input type="date" required value={snapshot} onChange={(e) => setSnapshot(e.target.value)} className="w-44" />
                <span className="block max-w-[16rem] text-[11px]">{t("kn.snapshotHint")}</span>
              </label>
              <Button disabled={busy || !market.trim() || !snapshot} onClick={confirm}>{t("kn.importBtn")}</Button>
              {job && <Link href="/data" className="text-sm text-accent underline">{t("kn.started", { id: job })}</Link>}
            </div>
          </Card>
        </>
      )}
      <OfflineEvidenceCard />
    </div>
  );
}
