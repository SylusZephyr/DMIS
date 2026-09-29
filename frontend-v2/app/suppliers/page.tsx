"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { UploadCloud } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, Meter, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post, type MarketRow, type RankedSupplier } from "@/lib/api";
import { truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Supplier = { id: string; name: string; country: string | null; city: string | null; website: string | null; business_type: string | null;
  oem: boolean; odm: boolean; certifications: string | null; product_categories: string | null; score: number | null; source: string | null;
  score_breakdown: { category_match: number; certifications: number } | null };

const COMPONENTS = ["fit", "cooperation", "price", "lead_time", "reliability"];

function Ranking() {
  const mn = useMarketName();
  const { t } = useI18n();
  const markets = useApi<MarketRow[]>("/markets");
  const [market, setMarket] = useState("");
  const { data } = useApi<RankedSupplier[]>(`/sourcing/ranking?limit=15${market ? `&market=${encodeURIComponent(market)}` : ""}`, [market]);
  return (
    <Card className="mx-6 mb-4">
      <CardHeader title={t("sup.ranking")} subtitle={t("sup.rankingSub")}
        right={<Select value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="">{t("sup.allMarkets")}</option>{(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}</Select>} />
      {!data?.length ? <Empty title={t("sup.nothing")}>{t("sup.nothingSub")}</Empty> : (
        <div className="overflow-x-auto" tabIndex={0}>
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">#</th><th scope="col">{t("sup.supplier")}</th><th scope="col">{t("sup.score")}</th>
              {COMPONENTS.map((c) => <th scope="col" key={c}>{t(`sup.comp.${c}`)}</th>)}<th scope="col" className="pr-4">{t("sup.evidence")}</th></tr></thead>
            <tbody className="divide-y divide-line">
              {data.map((r) => (
                <tr key={r.supplier_id} className="hover:bg-panel-2">
                  <td className="px-4 py-2 font-mono text-xs">{r.rank}</td>
                  <td><Link href={`/suppliers/${r.supplier_id}`} className="hover:text-accent">{r.name}</Link> <span className="text-xs text-ink-3">{r.country ?? ""}</span></td>
                  <td className="font-mono">{r.rank_score.toFixed(0)}</td>
                  {COMPONENTS.map((c) => <td key={c} className="font-mono text-xs">{r.components[c] ?? <span className="text-ink-3">—</span>}</td>)}
                  <td className="max-w-[320px] pr-4 text-xs text-ink-3">{r.evidence.join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function SuppliersView() {
  const { t } = useI18n();
  const [country, setCountry] = useState(useSearchParams().get("country") ?? "");
  const [q, setQ] = useState("");
  const [tick, setTick] = useState(0);
  const params = new URLSearchParams({ ...(q ? { q } : {}), ...(country ? { country } : {}) }).toString();
  const { data, error } = useApi<Supplier[]>(`/suppliers${params ? `?${params}` : ""}`, [tick]);
  const [msg, setMsg] = useState<string | null>(null);
  const upload = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    const r = await post<{ imported: number }>("/suppliers/import", fd);
    setMsg(t("sup.imported", { n: r.imported }));
    setTick((t) => t + 1);
  };
  return (
    <div className="pb-8">
      <PageHeader title={t("sup.title")} subtitle={t("sup.subtitle")}
        right={<label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm hover:border-accent">
          <UploadCloud className="h-4 w-4 text-accent" /> {t("sup.import")}
          <input type="file" className="hidden" accept=".csv,.xlsx" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>} />
      <ErrorNote error={error} />
      {msg && <div className="mx-6 mb-3 rounded-lg border border-good/40 bg-good/10 px-4 py-2 text-sm text-good">{msg}</div>}
      <div className="mx-6 mb-4 flex gap-2">
        <Input placeholder={t("sup.search")} value={q} onChange={(e) => setQ(e.target.value)} className="max-w-sm" />
        <Input placeholder={t("sup.country")} value={country} onChange={(e) => setCountry(e.target.value)} className="max-w-[180px]" />
        {(q || country) && <Button variant="ghost" onClick={() => { setQ(""); setCountry(""); }}>{t("sup.clear")}</Button>}
      </div>
      <Ranking />
      <Card className="mx-6">
        <CardHeader title={t("sup.count", { n: data?.length ?? 0 })} subtitle={t("sup.template")} />
        {data && data.length === 0 ? <Empty title={t("sup.none")}>{t("sup.noneSub")}</Empty> : (
          <div className="overflow-x-auto" tabIndex={0}>
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase text-ink-3"><tr>
                <th scope="col" className="px-4 py-2">{t("sup.supplier")}</th><th scope="col">{t("sup.location")}</th><th scope="col">{t("sup.type")}</th><th scope="col">{t("sup.capability")}</th><th scope="col">{t("sup.certs")}</th><th scope="col">{t("sup.products")}</th><th scope="col" className="w-40 pr-4">{t("sup.score")}</th></tr></thead>
              <tbody className="divide-y divide-line">
                {(data ?? []).map((s) => (
                  <tr key={s.id} className="hover:bg-panel-2">
                    <td className="px-4 py-2"><Link href={`/suppliers/${s.id}`} className="font-medium hover:text-accent">{s.name}</Link>{s.website && <a className="text-xs text-accent" href={s.website} target="_blank">{truncate(s.website, 32)}</a>}</td>
                    <td className="text-ink-2">{[s.city, s.country].filter(Boolean).join(", ") || "—"}</td>
                    <td className="text-ink-2">{s.business_type ?? "—"}</td>
                    <td className="space-x-1">{s.oem && <Badge>OEM</Badge>}{s.odm && <Badge>ODM</Badge>}</td>
                    <td className="max-w-[160px] truncate text-xs text-ink-2">{s.certifications ?? "—"}</td>
                    <td className="max-w-[240px] truncate text-xs text-ink-3">{s.product_categories ?? "—"}</td>
                    <td className="pr-4"><div className="flex items-center gap-2"><Meter value={s.score ?? 0} max={100} /><span className="w-8 font-mono text-xs">{s.score?.toFixed(0) ?? "—"}</span></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export default function SuppliersPage() {
  return <Suspense><SuppliersView /></Suspense>;
}
