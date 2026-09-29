"use client";
import type { MarketRow } from "@/lib/api";
import { moneyShort } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

/** Revenue fields of a market as the API returns them (markets list, overview). */
export type SizeFields = {
  revenue_headline?: number | null; monthly_revenue?: number | null; revenue_est?: number | null;
  revenue_lo?: number | null; revenue_hi?: number | null; model_validated?: boolean | null;
};

/** The market size shown everywhere: what the data certainly shows (observed floor), as on the market page.
 *  Markets processed before metrics v3 fall back to their older observed sum. */
export function headlineOf(m: SizeFields): number | null {
  return m.revenue_headline ?? m.monthly_revenue ?? null;
}

/** "modelled $X ($lo–$hi) · not validated" -- the demand-model estimate beside the headline, never instead of it. */
export function ModelledNote({ m, className }: { m: SizeFields; className?: string }) {
  const { t } = useI18n();
  if (m.revenue_est == null) return null;
  const range = m.revenue_lo != null && m.revenue_hi != null ? ` (${moneyShort(m.revenue_lo)}–${moneyShort(m.revenue_hi)})` : "";
  return (
    <span className={className} title={t("size.modelledTip")}>
      {t("size.modelled", { v: moneyShort(m.revenue_est) })}{range}
      {m.model_validated === false && <span className="text-warn"> · {t("size.notValidated")}</span>}
    </span>
  );
}

/** Whether the market's demand model passed hold-out validation (null: unknown / processed before the check). */
export function useModelValidated(market: string | null | undefined): boolean | null {
  const mk = useApi<MarketRow[]>(market ? "/markets" : null);            // one cached list shared by every caller
  const row = (mk.data ?? []).find((m) => m.name === market);
  return row?.model_validated ?? null;
}

/** "model-based · not validated" beside any number the demand model produced, when the model failed its check. */
export function ModelFlag({ market, className }: { market: string | null | undefined; className?: string }) {
  const { t } = useI18n();
  const ok = useModelValidated(market);
  if (ok !== false) return null;
  return <span className={`block text-warn ${className ?? ""}`}>{t("market.modelBased")}</span>;
}
