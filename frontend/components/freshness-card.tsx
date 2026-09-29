"use client";
import { Badge, Card, CardHeader, Empty } from "@/components/ui/primitives";
import { num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Row = { market: string; latest: string | null; age_days: number | null; stale: boolean | null; snapshots: number; dated: number;
  growth: { needed: number; have: number; ready: boolean }; seasonality: { needed: number; have: number; ready: boolean };
  inbox: string; next: string | null };

/** How current each market's data is, and how far it is from the history growth and seasonality need. */
export function FreshnessCard() {
  const mn = useMarketName();
  const { t } = useI18n();
  const d = useApi<Row[]>("/freshness");
  return (
    <Card>
      <CardHeader title={t("fresh.title")} subtitle={t("fresh.sub")} />
      {!d.data ? <div className="h-16 animate-pulse" /> : d.data.length === 0 ? <Empty title={t("fresh.none")} /> : (
        <div className="overflow-x-auto" tabIndex={0}>
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase text-ink-3"><tr>
              <th scope="col" className="px-4 py-2">{t("fresh.market")}</th><th scope="col" className="px-2">{t("fresh.latest")}</th>
              <th scope="col" className="px-2 text-right">{t("fresh.snapshots")}</th><th scope="col" className="px-2">{t("fresh.growth")}</th>
              <th scope="col" className="px-2">{t("fresh.seasonality")}</th><th scope="col" className="px-4">{t("fresh.inbox")}</th></tr></thead>
            <tbody className="divide-y divide-line">
              {d.data.map((r) => (
                <tr key={r.market}>
                  <td className="px-4 py-2 font-medium">{mn(r.market)}</td>
                  <td className="px-2">{/* undated uploads: the date shown is the upload date, so say so rather than an age */}
                    {r.stale == null ? <>{r.latest ? t("fresh.uploaded", { d: r.latest }) : "—"} <Badge color="var(--ink-3)">{t("fresh.unknown")}</Badge></>
                      : <>{r.latest ?? "—"} {r.age_days != null && <span className="text-ink-3">({t("fresh.days", { n: r.age_days })})</span>}{" "}</>}
                    {r.stale == null ? null
                      : r.stale ? <Badge color="var(--warn)">{t("fresh.stale")}</Badge> : <Badge color="var(--good)">{t("fresh.current")}</Badge>}</td>
                  <td className="px-2 text-right font-mono">{num(r.snapshots)}{r.dated < r.snapshots && <span className="text-ink-3"> ({t("fresh.undated", { n: r.snapshots - r.dated })})</span>}</td>
                  <td className="px-2">{r.growth.ready ? <Badge color="var(--good)">{t("fresh.ready")}</Badge> : <span className="font-mono">{r.growth.have}/{r.growth.needed}</span>}</td>
                  <td className="px-2">{r.seasonality.ready ? <Badge color="var(--good)">{t("fresh.ready")}</Badge> : <span className="font-mono">{r.seasonality.have}/{r.seasonality.needed}</span>}</td>
                  <td className="px-4 font-mono text-[10px] text-ink-3">{r.inbox}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="px-4 py-2 text-[11px] text-ink-3">{t("fresh.note")}</p>
    </Card>
  );
}
