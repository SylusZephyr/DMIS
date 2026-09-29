"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Calculator } from "lucide-react";
import { Card, CardHeader, Input, Select } from "@/components/ui/primitives";
import { PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { post } from "@/lib/api";
import { money, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Result = { per_unit: Record<string, number>; margin: number; contribution_margin: number; break_even_acos: number;
  max_unit_cost_for_target: number; first_order_cost: number; cash_to_launch: number; sources: Record<string, string>;
  monthly?: { units: number; profit: number; payback_months: number | null } };
type Defaults = { referral_fee: number; freight_usd_per_kg: number; target_margin: number; duty_scenarios: Record<string, number> };

const FIELDS = [["price", "20"], ["unit_cost", "2"], ["fba_fee", "3.5"], ["weight_g", "150"], ["qty", "500"], ["ad_share", "10"],
  ["launch_costs", "800"], ["units_per_month", ""]] as const;

function Row({ label, value, strong, tone }: { label: string; value: string; strong?: boolean; tone?: string }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 py-1.5 ${strong ? "border-t border-line pt-2" : ""}`}>
      <span className={strong ? "font-medium text-ink" : "text-ink-2"}>{label}</span>
      <span className={`font-mono ${strong ? "text-lg" : "text-sm"}`} style={tone ? { color: tone } : undefined}>{value}</span>
    </div>
  );
}

function Calc() {
  const { t } = useI18n();
  const sp = useSearchParams();
  const [market] = useMarket();
  const defaults = useApi<Defaults>("/economics/defaults");
  const [v, setV] = useState<Record<string, string>>(() => Object.fromEntries(FIELDS.map(([k, d]) => [k, sp.get(k) ?? d])));
  const [duty, setDuty] = useState("");
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const h = setTimeout(async () => {
      const n = (k: string) => (v[k] === "" ? null : Number(v[k]));
      if ([n("price"), n("unit_cost"), n("fba_fee"), n("weight_g"), n("qty")].some((x) => x == null || Number.isNaN(x))) return;
      try {
        setErr(null);
        setRes(await post<Result>("/economics/unit", { price: n("price"), unit_cost: n("unit_cost"), fba_fee: n("fba_fee"),
          weight_g: n("weight_g"), qty: n("qty"), ad_share: (n("ad_share") ?? 0) / 100, launch_costs: n("launch_costs") ?? 0,
          units_per_month: n("units_per_month"), duty_scenario: duty || null, market: market || null }));
      } catch (e) { setErr((e as Error).message); }
    }, 250);                                   // recompute as you type, once typing pauses
    return () => clearTimeout(h);
  }, [v, duty, market]);
  const pu = res?.per_unit;
  const tone = (x: number | undefined) => (x == null ? undefined : x > 0 ? "var(--good)" : "var(--bad)");
  return (
    <div className="pb-8">
      <PageHeader title={t("econ.title")} subtitle={t("econ.subtitle")} />
      <div className="grid grid-cols-1 gap-5 px-4 md:px-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <Card className="self-start">
          <CardHeader title={t("econ.inputs")} />
          <div className="grid grid-cols-2 gap-3 p-4 text-sm">
            {FIELDS.map(([k]) => (
              <label key={k} className="block">{t(`econ.f.${k}`)}
                <Input className="mt-1" type="number" min={0} step="any" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label>
            ))}
            <label className="col-span-2 block">{t("econ.duty")}
              <Select className="mt-1 w-full" value={duty} onChange={(e) => setDuty(e.target.value)}>
                <option value="">{t("econ.dutyMarket")}</option>
                {Object.entries(defaults.data?.duty_scenarios ?? {}).map(([k, r]) => <option key={k} value={k}>{k.replace(/_/g, " ")} ({pct(r, 1)})</option>)}
              </Select></label>
          </div>
          {err && <p role="alert" className="px-4 pb-3 text-sm text-bad">{err}</p>}
        </Card>
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[["econ.profit", pu ? money(pu.profit, 2) : "—", tone(pu?.profit)], ["econ.margin", res ? pct(res.margin, 1) : "—", tone(res?.margin)],
              ["econ.beAcos", res ? pct(res.break_even_acos, 0) : "—", undefined], ["econ.maxCost", res ? money(res.max_unit_cost_for_target, 2) : "—", undefined]]
              .map(([k, val, c]) => (
                <Card key={k} className="p-4"><div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{t(k as string)}</div>
                  <div className="mt-1 font-mono text-2xl" style={c ? { color: c } : undefined}>{val}</div></Card>))}
          </div>
          <Card>
            <CardHeader title={t("econ.perUnit")} subtitle={t("econ.perUnitSub")} />
            <div className="px-4 pb-4 text-sm">
              {pu ? <>
                <Row label={t("econ.price")} value={money(Number(v.price), 2)} />
                <Row label={t("econ.referral")} value={`− ${money(pu.referral, 2)}`} />
                <Row label={t("econ.fba")} value={`− ${money(pu.fba_fee, 2)}`} />
                <Row label={t("econ.unitCost")} value={`− ${money(Number(v.unit_cost), 2)}`} />
                <Row label={t("econ.freight")} value={`− ${money(pu.freight, 2)}`} />
                <Row label={t("econ.dutyRow")} value={`− ${money(pu.duty, 2)}`} />
                <Row label={t("econ.contribution")} value={money(pu.contribution, 2)} strong tone={tone(pu.contribution)} />
                <Row label={t("econ.ads")} value={`− ${money(pu.ad_cost, 2)}`} />
                <Row label={t("econ.profit")} value={money(pu.profit, 2)} strong tone={tone(pu.profit)} />
              </> : <p className="py-6 text-center text-ink-3"><Calculator className="mx-auto mb-2 h-6 w-6" aria-hidden />{t("econ.fill")}</p>}
            </div>
          </Card>
          {res && (
            <Card>
              <CardHeader title={t("econ.launch")} />
              <div className="grid grid-cols-2 gap-3 p-4 text-sm md:grid-cols-4">
                <div><div className="text-ink-3">{t("econ.firstOrder")}</div><div className="font-mono text-lg">{money(res.first_order_cost, 0)}</div></div>
                <div><div className="text-ink-3">{t("econ.cash")}</div><div className="font-mono text-lg">{money(res.cash_to_launch, 0)}</div></div>
                {res.monthly && <>
                  <div><div className="text-ink-3">{t("econ.monthlyProfit")}</div><div className="font-mono text-lg" style={{ color: tone(res.monthly.profit) }}>{money(res.monthly.profit, 0)}</div></div>
                  <div><div className="text-ink-3">{t("econ.payback")}</div><div className="font-mono text-lg">{res.monthly.payback_months != null ? t("econ.months", { n: num(res.monthly.payback_months, 1) }) : "—"}</div></div>
                </>}
              </div>
              <ul className="border-t border-line px-4 py-3 text-[11px] text-ink-3">
                {Object.entries(res.sources).map(([k, s]) => <li key={k}>{t(`econ.src.${k}`)}: {s}</li>)}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default function EconomicsPage() {
  return <Suspense><Calc /></Suspense>;
}
