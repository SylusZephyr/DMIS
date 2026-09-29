"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Input, Select, Stat } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post, type Interaction, type MarketRow } from "@/lib/api";
import { money, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Match = { market?: string; segment_id: string; segment_label?: string; match_score: number };
type Detail = { id: string; name: string; country: string | null; city: string | null; website: string | null; business_type: string | null;
  oem: boolean; odm: boolean; certifications: string | null; product_categories: string | null; score: number | null;
  price_level: string | null; contact: string | null; matches: Match[] };

const KINDS = ["inquiry", "quote", "sample", "order", "audit", "issue"];
const EMPTY = { kind: "inquiry", product: "", market_name: "", unit_price: "", moq: "", lead_time_days: "", rating: "", note: "" };

export default function SupplierPage() {
  const mn = useMarketName();
  const { t } = useI18n();
  const id = String(useParams().id);
  const sup = useApi<Detail>(`/suppliers/${id}`);
  const hist = useApi<Interaction[]>(`/suppliers/${id}/interactions`);
  const markets = useApi<MarketRow[]>("/markets");
  const [f, setF] = useState(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const n = (v: string) => (v === "" ? null : Number(v));
  const save = async () => {
    setErr(null);
    try {
      await post(`/suppliers/${id}/interactions`, { kind: f.kind, product: f.product || null, market_name: f.market_name || null,
        unit_price: n(f.unit_price), moq: n(f.moq), lead_time_days: n(f.lead_time_days), rating: n(f.rating), note: f.note || null });
      setF(EMPTY); hist.reload(); sup.reload();
    } catch (e) { setErr((e as Error).message); }
  };
  const s = sup.data;
  const rows = hist.data ?? [];
  const rated = rows.filter((r) => r.rating != null);
  return (
    <div className="pb-8">
      <PageHeader title={s?.name ?? t("sup.supplier")} subtitle={s ? [s.city, s.country].filter(Boolean).join(", ") + (s.business_type ? ` · ${s.business_type}` : "") : undefined}
        right={<Link href="/suppliers" className="text-sm text-accent">← {t("supd.all")}</Link>} />
      <ErrorNote error={sup.error ?? hist.error} />
      {s && (
        <div className="space-y-4 px-6">
          <Card className="grid grid-cols-2 divide-x divide-line md:grid-cols-5">
            <Stat label={t("supd.catalogue")} value={s.score?.toFixed(0) ?? "—"} sub={<span className="space-x-1">{s.oem && <Badge>OEM</Badge>}{s.odm && <Badge>ODM</Badge>}</span>} />
            <Stat label={t("supd.priceLevel")} value={s.price_level ?? "—"} sub={s.price_level ? t("supd.fromQuotes") : t("supd.noQuotes")} />
            <Stat label={t("supd.interactions")} value={num(rows.length)} sub={t("supd.ordersIssues", { o: rows.filter((r) => r.kind === "order").length, i: rows.filter((r) => r.kind === "issue").length })} />
            <Stat label={t("supd.meanRating")} value={rated.length ? (rated.reduce((a, r) => a + (r.rating ?? 0), 0) / rated.length).toFixed(1) : "—"} sub={t("supd.rated", { n: rated.length })} />
            <Stat label={t("sup.certs")} value={<span className="text-sm">{s.certifications ?? "—"}</span>} />
          </Card>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[380px_1fr]">
            <Card className="h-fit">
              <CardHeader title={t("supd.record")} subtitle={t("supd.recordSub")} />
              <div className="space-y-2 p-4 text-sm">
                <div className="grid grid-cols-2 gap-2">
                  <label>{t("supd.kind")}<Select value={f.kind} onChange={set("kind")} className="w-full">{KINDS.map((k) => <option key={k} value={k}>{t(`supd.kinds.${k}`)}</option>)}</Select></label>
                  <label>{t("launch.market")}<Select value={f.market_name} onChange={set("market_name")} className="w-full">
                    <option value="">—</option>{(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}</Select></label>
                </div>
                <label className="block">{t("launch.product")}<Input value={f.product} onChange={set("product")} /></label>
                <div className="grid grid-cols-2 gap-2">
                  <label>{t("supd.unitPrice")} ($)<Input type="number" value={f.unit_price} onChange={set("unit_price")} /></label>
                  <label>MOQ<Input type="number" value={f.moq} onChange={set("moq")} /></label>
                  <label>{t("supd.leadTime")}<Input type="number" value={f.lead_time_days} onChange={set("lead_time_days")} /></label>
                  <label>{t("supd.rating")}<Input type="number" min={1} max={5} value={f.rating} onChange={set("rating")} /></label>
                </div>
                <label className="block">{t("supd.note")}<Input value={f.note} onChange={set("note")} /></label>
                <Button className="w-full" onClick={save}>{t("supd.save")}</Button>
                {err && <div className="text-xs text-bad">{err}</div>}
              </div>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader title={t("supd.history")} />
                {!rows.length ? <Empty title={t("supd.noHistory")}>{t("supd.noHistorySub")}</Empty> : (
                  <table className="w-full text-sm">
                    <thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("audit.when")}</th><th scope="col">{t("supd.kind")}</th><th scope="col">{t("supd.productMarket")}</th><th scope="col">{t("launch.price")}</th><th scope="col">MOQ</th><th scope="col">{t("supd.lead")}</th><th scope="col">{t("supd.rating")}</th><th scope="col" className="pr-4">{t("supd.note")}</th></tr></thead>
                    <tbody className="divide-y divide-line">
                      {rows.map((r) => (
                        <tr key={r.id}>
                          <td className="px-4 py-1.5 text-xs text-ink-3">{new Date(r.at).toLocaleDateString()}</td>
                          <td><Badge color={r.kind === "issue" ? "var(--bad)" : undefined}>{t(`supd.kinds.${r.kind}`)}</Badge></td>
                          <td className="text-xs">{r.product ?? "—"}{r.market_name ? <span className="text-ink-3"> · {r.market_name}</span> : null}</td>
                          <td className="font-mono text-xs">{r.unit_price != null ? money(r.unit_price, 2) : "—"}</td>
                          <td className="font-mono text-xs">{r.moq ?? "—"}</td>
                          <td className="font-mono text-xs">{r.lead_time_days != null ? `${r.lead_time_days}d` : "—"}</td>
                          <td className="font-mono text-xs">{r.rating ?? "—"}</td>
                          <td className="pr-4 text-xs text-ink-3">{r.note ?? ""} <span className="text-[10px]">{r.by}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
              <Card>
                <CardHeader title={t("supd.matches")} subtitle={t("supd.matchesSub")} />
                {!s.matches?.length ? <Empty title={t("supd.noMatches")}>{t("supd.noMatchesSub")}</Empty> : (
                  <div className="divide-y divide-line">
                    {s.matches.slice(0, 15).map((m, i) => (
                      <div key={i} className="flex justify-between px-4 py-2 text-sm">
                        <span>{m.segment_label ?? m.segment_id}{m.market ? <span className="text-ink-3"> · {mn(m.market)}</span> : null}</span>
                        <span className="font-mono text-xs">{m.match_score.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
