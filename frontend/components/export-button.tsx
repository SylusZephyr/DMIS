"use client";
import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { API, getToken } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

function filenameOf(r: Response, fallback: string): string {
  const cd = r.headers.get("content-disposition") ?? "";
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) { try { return decodeURIComponent(star[1]); } catch { /* fall through */ } }
  const plain = cd.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : fallback;
}

/** Fetch an export with the viewer's token and save it through a temporary link. */
export async function downloadExport(path: string, fallback = "export"): Promise<void> {
  const token = getToken();
  const r = await fetch(`${API}${path}`, { cache: "no-store", headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (r.status === 401) window.dispatchEvent(new Event("dmis-auth-required"));
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filenameOf(r, fallback);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** "Label ↓ CSV · XLSX" (or any formats): one button per format. `path` has no format parameter. */
export function ExportButtons({ path, label, formats = ["csv", "xlsx"], className = "" }:
  { path: string; label?: string; formats?: string[]; className?: string }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const go = async (fmt: string) => {
    setBusy(fmt); setErr(null);
    try { await downloadExport(`${path}${path.includes("?") ? "&" : "?"}format=${fmt}`, `export.${fmt}`); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  };
  return (
    <span className={`inline-flex flex-wrap items-center gap-1 text-xs ${className}`}>
      <span className="inline-flex items-center gap-1 text-ink-3"><Download className="h-3.5 w-3.5" />{label ?? t("exp.download")}</span>
      {formats.map((f) => (
        <button key={f} type="button" disabled={busy !== null} onClick={(e) => { e.stopPropagation(); go(f); }}
          className="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 font-mono text-[11px] uppercase text-ink-2 hover:border-accent hover:text-accent disabled:opacity-50">
          {busy === f && <Loader2 className="h-3 w-3 animate-spin" />}{t(`exp.fmt.${f}`)}
        </button>
      ))}
      {err && <span className="text-bad" title={err}>{t("exp.failed")}</span>}
    </span>
  );
}
