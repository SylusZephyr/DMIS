"use client";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Input, Meter, Select, Stat } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post, type OrgSummary, type TokenRow } from "@/lib/api";
import { num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

const PLANS = ["starter", "professional", "enterprise"];
const METRICS = ["records_ingested", "datasets_processed", "analyst_questions", "ai_calls"];

function Limit({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const { t } = useI18n();
  return (
    <div className="text-sm">
      <div className="flex justify-between"><span>{label}</span><span className="font-mono text-xs">{num(used)} / {limit == null ? t("org.unlimited") : num(limit)}</span></div>
      <Meter value={limit ? used : 0} max={limit ?? 1} color={limit && used >= limit ? "var(--bad)" : "var(--accent)"} />
    </div>
  );
}

function Tokens() {
  const { t } = useI18n();
  const { data, error, reload } = useApi<TokenRow[]>("/auth/tokens");
  const [name, setName] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); reload(); } catch (e) { setErr((e as Error).message); } };
  return (
    <Card>
      <CardHeader title={t("org.tokens")} subtitle={t("org.tokensSub")}
        right={<div className="flex gap-2"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("org.tokenName")} className="w-40" />
          <Button size="sm" disabled={!name} onClick={() => act(async () => { setShown((await post<{ token: string }>("/auth/tokens", { name })).token); setName(""); })}>{t("org.create")}</Button></div>} />
      <ErrorNote error={error ?? err} />
      {shown && <div className="mx-4 my-2 rounded-lg border border-good/40 bg-good/10 px-3 py-2 text-xs">{t("org.shownOnce")} <code className="break-all">{shown}</code></div>}
      {!data?.length ? <Empty title={t("org.noTokens")}>{t("org.noTokensSub")}</Empty> : (
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("org.name")}</th><th scope="col">{t("org.created")}</th><th scope="col">{t("org.lastUsed")}</th><th scope="col">{t("org.expires")}</th><th scope="col">{t("audit.status")}</th><th scope="col" className="pr-4" /></tr></thead>
          <tbody className="divide-y divide-line">
            {data.map((tk) => {
              const expired = tk.expires_at != null && new Date(tk.expires_at) < new Date();
              return (
                <tr key={tk.id}>
                  <td className="px-4 py-2">{tk.name}</td>
                  <td className="text-xs text-ink-3">{new Date(tk.created_at).toLocaleDateString()}</td>
                  <td className="text-xs text-ink-3">{tk.last_used_at ? new Date(tk.last_used_at).toLocaleString() : "—"}</td>
                  <td className="text-xs text-ink-3">{tk.expires_at ? new Date(tk.expires_at).toLocaleDateString() : t("org.never")}</td>
                  <td>{tk.revoked ? <Badge color="var(--bad)">{t("org.revoked")}</Badge> : expired ? <Badge color="var(--warn)">{t("org.expired")}</Badge> : <Badge color="var(--good)">{t("org.active")}</Badge>}</td>
                  <td className="pr-4 text-right">{!tk.revoked && <Button size="sm" variant="ghost" onClick={() => act(() => post(`/auth/tokens/${tk.id}/revoke`, {}))}>{t("org.revoke")}</Button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function OrgAdmin() {
  const { t } = useI18n();
  // Only admins of the default organization may list organizations; anyone else gets 403 and this card stays hidden.
  const { data, reload } = useApi<OrgSummary[]>("/orgs");
  const [f, setF] = useState({ name: "", plan: "starter" });
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const act = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); reload(); } catch (e) { setErr((e as Error).message); } };
  return (
    <Card>
      <CardHeader title={t("org.orgs")} subtitle={t("org.orgsSub")}
        right={<div className="flex gap-2">
          <Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={t("org.newOrg")} className="w-44" />
          <Select value={f.plan} onChange={(e) => setF({ ...f, plan: e.target.value })}>{PLANS.map((p) => <option key={p}>{p}</option>)}</Select>
          <Button size="sm" disabled={!f.name} onClick={() => act(async () => { await post("/orgs", f); setF({ ...f, name: "" }); })}>{t("org.create")}</Button>
        </div>} />
      <ErrorNote error={err} />
      <table className="w-full text-sm">
        <thead className="text-left text-[11px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("org.org")}</th><th scope="col">{t("org.plan")}</th><th scope="col">{t("audit.status")}</th><th scope="col">{t("home.markets")}</th><th scope="col">{t("pilot.users")}</th><th scope="col" className="pr-4">{t("org.recordsMonth")}</th></tr></thead>
        <tbody className="divide-y divide-line">
          {data.map((o) => (
            <tr key={o.org_id}>
              <td className="px-4 py-2"><div>{o.name}</div><div className="font-mono text-[10px] text-ink-3">{o.org_id}</div></td>
              <td><Select value={o.plan} onChange={(e) => act(() => post(`/orgs/${o.org_id}/plan`, { plan: e.target.value }))}>{PLANS.map((p) => <option key={p}>{p}</option>)}</Select></td>
              <td>{o.status}</td>
              <td className="font-mono text-xs">{o.counts.markets}{o.limits.markets != null ? ` / ${o.limits.markets}` : ""}</td>
              <td className="font-mono text-xs">{o.counts.users}{o.limits.users != null ? ` / ${o.limits.users}` : ""}</td>
              <td className="pr-4 font-mono text-xs">{num(o.usage_this_month.records_ingested ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

export default function OrgPage() {
  const { t } = useI18n();
  const { data: o, error } = useApi<OrgSummary>("/org");
  return (
    <div className="pb-8">
      <PageHeader title={t("org.org")} subtitle={t("org.subtitle")} />
      <ErrorNote error={error} />
      <div className="space-y-4 px-6">
        {o && <>
          <Card className="grid grid-cols-2 divide-x divide-line md:grid-cols-4">
            <Stat label={t("org.org")} value={o.name} sub={o.org_id} />
            <Stat label={t("org.plan")} value={o.plan} sub={o.status} />
            <Stat label={t("home.markets")} value={num(o.counts.markets)} sub={o.limits.markets == null ? t("org.noLimit") : t("org.of", { n: o.limits.markets })} />
            <Stat label={t("pilot.users")} value={num(o.counts.users)} sub={o.limits.users == null ? t("org.noLimit") : t("org.of", { n: o.limits.users })} />
          </Card>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title={t("org.limits")} subtitle={t("org.limitsSub")} />
              <div className="space-y-3 p-4">
                <Limit label={t("home.markets")} used={o.counts.markets} limit={o.limits.markets} />
                <Limit label={t("pilot.users")} used={o.counts.users} limit={o.limits.users} />
                <Limit label={t("org.recordsMonth")} used={o.usage_this_month.records_ingested ?? 0} limit={o.limits.records_per_month} />
                <div className="pt-1 text-xs text-ink-3">{t("org.features")}: {o.features.map((f) => <Badge key={f} className="mr-1">{f}</Badge>)}</div>
              </div>
            </Card>
            <Card>
              <CardHeader title={t("org.usage")} />
              <div className="grid grid-cols-2 gap-3 p-4 text-sm">
                {METRICS.map((k) => <div key={k}>{t(`org.metric.${k}`)}<div className="font-mono">{num(o.usage_this_month[k] ?? 0)}</div></div>)}
              </div>
            </Card>
          </div>
        </>}
        <Tokens />
        <OrgAdmin />
      </div>
    </div>
  );
}
