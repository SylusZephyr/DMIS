"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import type { AlertRow, EventRow } from "@/lib/api";
import { severityColor } from "@/lib/colors";
import { timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useDescribe } from "@/lib/events";
import { useMarketName } from "@/lib/market-name";

/** Bell in the top bar: unread alerts count, and the latest events. */
export function Notifications() {
  const { t, lang } = useI18n();
  const mn = useMarketName();
  const describe = useDescribe();
  const alerts = useApi<AlertRow[]>("/alerts");
  const events = useApi<EventRow[]>("/events?limit=12");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  const unread = (alerts.data ?? []).filter((a) => a.status === "new").length;
  const list = (events.data ?? []).slice(0, 8);
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog"
        aria-label={unread ? t("ia.notificationsN", { n: unread }) : t("ia.notifications")}
        className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-panel-2/60 text-ink-2 hover:border-accent/60 hover:text-ink">
        <Bell className="h-4 w-4" />
        {unread > 0 && <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 font-mono text-[9px] font-bold text-on-accent">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div role="dialog" aria-label={t("ia.notifications")} className="glass fade-up absolute right-0 top-11 z-50 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-line shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-3">{t("ia.latestEvents")}</span>
            {unread > 0 && <span className="text-[11px] text-accent">{t("ia.unread", { n: unread })}</span>}
          </div>
          <ul className="max-h-96 divide-y divide-line overflow-y-auto">
            {list.length === 0 && <li className="px-3 py-6 text-center text-sm text-ink-3">{t("alerts.noEvents")}</li>}
            {list.map((e) => (
              <li key={e.id} className="flex gap-2.5 px-3 py-2.5 text-sm">
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: severityColor(e.severity) }} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-2 text-ink">{describe(e)}</span>
                  <span className="text-[11px] text-ink-3">{e.market_name ? `${mn(e.market_name)} · ` : ""}{timeAgo(e.created_at, lang)}</span>
                </span>
              </li>
            ))}
          </ul>
          <Link href="/alerts" onClick={() => setOpen(false)} className="block border-t border-line px-3 py-2 text-center text-sm text-accent hover:bg-panel-2">{t("ia.allAlerts")} →</Link>
        </div>
      )}
    </div>
  );
}
