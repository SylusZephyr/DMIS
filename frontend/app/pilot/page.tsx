"use client";
import { useState } from "react";
import { Badge, Card, CardHeader, Empty, Input, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post } from "@/lib/api";
import { num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Fb = { id: string; target_kind: string; target_id: string | null; market_name: string | null; field: string | null; shown_value: string | null;
  comment: string; page: string | null; user: string | null; status: string; triage: string | null; resolution: string | null; created_at: string };
type Usage = { days: number; views: number; pages: { page: string; views: number; users: number }[];
  users: { user: string; role: string | null; views: number; pages: number; last: string }[] };

const TRIAGE = ["data_bug", "rule_fix", "ui_confusion", "feature_request", "not_a_bug"];
const STATUS = ["new", "triaged", "fixed", "closed"];
const statusColor = (s: string) => (s === "new" ? "var(--warn)" : s === "fixed" ? "var(--good)" : undefined);

function Resolution({ fb, onSaved }: { fb: Fb; onSaved: () => void }) {
  const { t } = useI18n();
  const [v, setV] = useState(fb.resolution ?? "");
  return <Input value={v} onChange={(e) => setV(e.target.value)} placeholder={t("pilot.resolutionPh")}
    onBlur={() => v !== (fb.resolution ?? "") && post(`/feedback/${fb.id}`, { resolution: v }).then(onSaved)} className="h-8 text-xs" />;
}

export default function Pilot() {
  const { t } = useI18n();
  const [status, setStatus] = useState("");
  const fb = useApi<Fb[]>(`/feedback${status ? `?status=${status}` : ""}`, [status]);
  const [days, setDays] = useState("28");
  const usage = useApi<Usage>(`/usage/pages?days=${days}`, [days]);
  const set = (id: string, body: Record<string, string>) => post(`/feedback/${id}`, body).then(() => fb.reload());
  const rows = fb.data ?? [];
  const counts = STATUS.map((s) => `${t(`pilot.status.${s}`)} ${rows.filter((r) => r.status === s).length}`).join(" · ");
  return (
    <div className="pb-16">
      <PageHeader title={t("pilot.title")} subtitle={t("pilot.subtitle")} />
      <ErrorNote error={fb.error ?? usage.error} />
      <div className="space-y-4 px-6">
        <Card>
          <CardHeader title={t("pilot.feedback", { n: rows.length })} subtitle={counts}
            right={<Select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">{t("common.all")}</option>{STATUS.map((s) => <option key={s} value={s}>{t(`pilot.status.${s}`)}</option>)}</Select>} />
          {!rows.length ? <Empty title={t("pilot.none")}>{t("pilot.noneSub")}</Empty> : (
            <div className="overflow-x-auto" tabIndex={0}>
              <table className="w-full text-sm">
                <thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("pilot.whenWho")}</th><th scope="col">{t("pilot.about")}</th><th scope="col">{t("pilot.report")}</th><th scope="col">{t("pilot.triage")}</th><th scope="col">{t("pilot.statusH")}</th><th scope="col" className="w-64 pr-4">{t("pilot.resolution")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {rows.map((r) => (
                    <tr key={r.id} className="align-top">
                      <td className="px-4 py-2 text-xs text-ink-3">{new Date(r.created_at).toLocaleString()}<div>{r.user}</div></td>
                      <td className="text-xs">{r.target_kind}{r.target_id ? ` · ${r.target_id}` : ""}{r.field ? <div className="text-ink-3">{t("pilot.field")}: {r.field}</div> : null}
                        {r.page && <a className="text-accent" href={r.page}>{t("pilot.openPage")}</a>}</td>
                      <td className="max-w-[360px] py-2 text-xs">{r.comment}{r.shown_value && <div className="text-ink-3">{t("pilot.shown")}: {r.shown_value}</div>}</td>
                      <td><Select value={r.triage ?? ""} onChange={(e) => set(r.id, { triage: e.target.value })} className="h-8 text-xs">
                        <option value="">—</option>{TRIAGE.map((x) => <option key={x} value={x}>{t(`pilot.triages.${x}`)}</option>)}</Select></td>
                      <td><Select value={r.status} onChange={(e) => set(r.id, { status: e.target.value })} className="h-8 text-xs" style={{ color: statusColor(r.status) }}>
                        {STATUS.map((x) => <option key={x} value={x}>{t(`pilot.status.${x}`)}</option>)}</Select></td>
                      <td className="pr-4"><Resolution fb={r} onSaved={fb.reload} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader title={t("pilot.pages", { n: num(usage.data?.views ?? 0) })} right={<Select value={days} onChange={(e) => setDays(e.target.value)}>
              {["7", "28", "90"].map((d) => <option key={d} value={d}>{t("pilot.lastDays", { d })}</option>)}</Select>} />
            {!usage.data?.pages.length ? <Empty title={t("pilot.noViews")} /> : (
              <table className="w-full text-sm"><thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("pilot.page")}</th><th scope="col">{t("pilot.views")}</th><th scope="col" className="pr-4">{t("pilot.users")}</th></tr></thead>
                <tbody className="divide-y divide-line">{usage.data.pages.map((p) => (
                  <tr key={p.page}><td className="px-4 py-1.5 font-mono text-xs">{p.page}</td><td className="font-mono text-xs">{p.views}</td><td className="pr-4 font-mono text-xs">{p.users}</td></tr>))}</tbody></table>
            )}
          </Card>
          <Card>
            <CardHeader title={t("pilot.users")} />
            {!usage.data?.users.length ? <Empty title={t("pilot.noUsers")} /> : (
              <table className="w-full text-sm"><thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("audit.user")}</th><th scope="col">{t("audit.role")}</th><th scope="col">{t("pilot.views")}</th><th scope="col">{t("pilot.pagesH")}</th><th scope="col" className="pr-4">{t("pilot.lastSeen")}</th></tr></thead>
                <tbody className="divide-y divide-line">{usage.data.users.map((u) => (
                  <tr key={u.user}><td className="px-4 py-1.5 text-xs">{u.user}</td><td className="text-xs">{u.role ? <Badge>{u.role}</Badge> : "—"}</td>
                    <td className="font-mono text-xs">{u.views}</td><td className="font-mono text-xs">{u.pages}</td>
                    <td className="pr-4 text-xs text-ink-3">{new Date(u.last).toLocaleString()}</td></tr>))}</tbody></table>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
