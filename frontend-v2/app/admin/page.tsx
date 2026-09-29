"use client";
import Link from "next/link";
import { Card, CardHeader, Empty, Skeleton } from "@/components/ui/primitives";
import { WorkspaceHub } from "@/components/v2/workspace-hub";
import { timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type AuditRow = { id: string; at: string; user: string | null; role: string | null; action: string; resource: string | null; status: number };

export default function Page() {
  const { t, lang } = useI18n();
  const audit = useApi<AuditRow[]>("/audit?limit=8");
  return (
    <WorkspaceHub id="admin">
      <Card>
        <CardHeader title={t("hub.recentAudit")} subtitle={t("ia.auditDesc")}
          right={<Link href="/audit" className="whitespace-nowrap text-xs text-accent hover:underline">{t("nav.audit")} →</Link>} />
        <ul className="divide-y divide-line">
          {!audit.data && [0, 1, 2].map((i) => <li key={i} className="px-4 py-3"><Skeleton className="h-4 w-full" /></li>)}
          {audit.data?.length === 0 && <li><Empty title={t("common.notAvailable")} /></li>}
          {(audit.data ?? []).slice(0, 8).map((r) => (
            <li key={r.id} className="flex items-center gap-3 px-4 py-2 text-sm">
              <span className={`h-2 w-2 shrink-0 rounded-full ${r.status < 400 ? "bg-good" : "bg-bad"}`} aria-hidden />
              <code className="min-w-0 flex-1 truncate text-xs text-ink-2">{r.action}</code>
              <span className="hidden text-xs text-ink-3 sm:inline">{r.user ?? "—"}</span>
              <span className="shrink-0 text-[11px] text-ink-3">{timeAgo(r.at, lang)}</span>
            </li>
          ))}
        </ul>
      </Card>
    </WorkspaceHub>
  );
}
