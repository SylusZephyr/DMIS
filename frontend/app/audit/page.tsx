"use client";
import { useState } from "react";
import { Card, CardHeader, Empty, Input } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Row = { id: string; at: string; user: string | null; role: string | null; action: string; resource: string | null;
  resource_id: string | null; status: number | null };

export default function Audit() {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const { data, error } = useApi<Row[]>(`/audit?limit=500${q ? `&action=${encodeURIComponent(q)}` : ""}`, [q]);
  return (
    <div className="pb-8">
      <PageHeader title={t("audit.title")} subtitle={t("audit.subtitle")} />
      <ErrorNote error={error} />
      <Card className="mx-6">
        <CardHeader title={t("audit.actions")} right={<Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("audit.filter")} className="w-64" />} />
        {!data?.length ? <Empty title={t("audit.none")} /> : (
          <div className="max-h-[720px] overflow-auto scrollbar-thin" tabIndex={0}>
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel text-left text-[11px] uppercase text-ink-3">
                <tr><th scope="col" className="px-4 py-2">{t("audit.when")}</th><th scope="col">{t("audit.user")}</th><th scope="col">{t("audit.role")}</th><th scope="col">{t("audit.action")}</th><th scope="col">{t("audit.resource")}</th><th scope="col" className="pr-4">{t("audit.status")}</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.map((r) => (
                  <tr key={r.id}><td className="px-4 py-1.5 font-mono text-xs text-ink-3">{new Date(r.at).toLocaleString()}</td>
                    <td className="text-xs">{r.user ?? t("audit.local")}</td><td className="text-xs text-ink-3">{r.role ?? "—"}</td>
                    <td className="font-mono text-xs">{r.action}</td><td className="text-xs">{r.resource}{r.resource_id ? `/${r.resource_id}` : ""}</td>
                    <td className="pr-4 font-mono text-xs">{r.status ?? ""}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
