"use client";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Select } from "@/components/ui/primitives";
import { ErrorNote } from "@/components/page";
import { post } from "@/lib/api";
import type { Schemas } from "@/lib/api-typed";
import { num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Upload = Schemas["OfflineUploadResponse"];
type Listing = Schemas["OfflineEvidenceResponse"];

/** Load offline evidence (exhibitions, catalogs, trade data...) for a market (spec 31-34). */
export function OfflineEvidenceCard() {
  const mn = useMarketName();
  const { t } = useI18n();
  const markets = useApi<{ name: string }[]>("/markets");
  const [market, setMarket] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [res, setRes] = useState<Upload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const m = market || markets.data?.[0]?.name || "";
  const list = useApi<Listing>(m ? `/markets/${encodeURIComponent(m)}/offline-evidence` : null, [m, res]);

  const upload = async () => {
    if (!file || !m) return;
    setBusy(true); setError(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("replace", String(replace));
    try {
      setRes(await post<Upload>(`/markets/${encodeURIComponent(m)}/offline-evidence`, fd));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader title={t("kn.offlineTitle")} subtitle={t("kn.offlineSub")} />
      <div className="flex flex-wrap items-end gap-3 p-4 pt-0">
        <label className="space-y-1 text-xs text-ink-3">
          <span>{t("kn.marketName")}</span>
          <Select aria-label={t("kn.marketName")} value={m} onChange={(e) => setMarket(e.target.value)} className="w-56">
            {(markets.data ?? []).map((x) => <option key={x.name} value={x.name}>{mn(x.name)}</option>)}
          </Select>
        </label>
        <label className="space-y-1 text-xs text-ink-3">
          <span>{t("kn.offlineFile")}</span>
          <input type="file" accept=".csv,.xlsx,.xls" aria-label={t("kn.offlineFile")} className="block text-sm text-ink-2"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="flex items-center gap-2 text-xs text-ink-2">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />{t("kn.offlineReplace")}
        </label>
        <Button disabled={busy || !file || !m} onClick={upload}>{t("kn.offlineUpload")}</Button>
      </div>
      <ErrorNote error={error} />
      {res && (
        <div className="space-y-1 px-4 pb-3 text-sm">
          <div>{t("kn.offlineResult", { a: res.accepted, r: res.rejected.length, n: res.total })}</div>
          {res.rejected.slice(0, 5).map((x, i) => <div key={i} className="text-xs text-ink-3">#{String(x.row)}: {String(x.reason)}</div>)}
          <div className="text-xs text-ink-3">{t("kn.offlineNext")}</div>
        </div>
      )}
      {list.data && list.data.rows.length > 0 && (
        <div className="overflow-x-auto border-t border-line" tabIndex={0}>
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase text-ink-3">
              <tr><th scope="col" className="px-4 py-2">{t("kn.offlineType")}</th><th scope="col" className="px-2 py-2">{t("kn.source")}</th>
                <th scope="col" className="px-2 py-2">{t("kn.offlineAppliesTo")}</th><th scope="col" className="px-2 py-2 text-right">{t("kn.offlineValue")}</th></tr>
            </thead>
            <tbody className="divide-y divide-line">
              {list.data.rows.map((r, i) => (
                <tr key={i}>
                  <td className="px-4 py-1.5"><Badge color="var(--accent)">{r.source_type}</Badge></td>
                  <td className="px-2 py-1.5">{r.source_name}</td>
                  <td className="px-2 py-1.5 text-ink-2">{r.applies_to ?? t("kn.category")}</td>
                  <td className="px-2 py-1.5 text-right font-mono">{r.value != null ? num(r.value) : "—"}{r.signal ? ` ${r.signal}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
