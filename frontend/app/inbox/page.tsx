"use client";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post, type MessageRow, type Summary } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Emp = { id: string; name: string; markets: number };

export default function Inbox() {
  const { t } = useI18n();
  const emps = useApi<Emp[]>("/employees");
  const [picked, setPicked] = useState("");
  const who = picked || emps.data?.find((e) => e.markets > 0)?.id || "";
  const [nonce, setNonce] = useState(0);
  const [hours, setHours] = useState(24);
  const sum = useApi<Summary>(who ? `/employees/${who}/summary?hours=${hours}` : null, [who, hours]);
  const msgs = useApi<MessageRow[]>(who ? `/messages?employee_id=${who}` : null, [who, nonce]);
  const [open, setOpen] = useState<string | null>(null);
  const read = async (id: string) => { setOpen(open === id ? null : id); await post(`/messages/${id}/read`, {}); setNonce((n) => n + 1); };
  const s = sum.data;
  return (
    <div className="pb-8">
      <PageHeader title={t("inbox.title")} subtitle={t("inbox.subtitle")}
        right={<div className="flex gap-2">
          <Select value={String(hours)} onChange={(e) => setHours(Number(e.target.value))}><option value="24">{t("inbox.h24")}</option><option value="168">{t("inbox.d7")}</option><option value="720">{t("inbox.d30")}</option></Select>
          <Select value={who} onChange={(e) => setPicked(e.target.value)} className="w-56">
            {(emps.data ?? []).filter((e) => e.markets > 0).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </Select></div>} />
      <ErrorNote error={emps.error} />
      <div className="grid grid-cols-1 gap-4 px-6 xl:grid-cols-2">
        <Card>
          <CardHeader title={t("inbox.summary")} subtitle={s ? `${s.markets.join(", ") || t("inbox.noMarkets")} · ${t("inbox.changes", { n: s.events })}` : ""} />
          {!s ? <Empty title={t("common.loading")} /> : (
            <div className="space-y-3 p-4 text-sm">
              {s.headline.length === 0 && s.items.length === 0 ? <div className="text-ink-3">{t("inbox.noChanges")}</div> : null}
              {s.headline.map((h) => <div key={h} className="font-medium text-ink">• {h}</div>)}
              <ul className="space-y-1 text-ink-2">{s.items.map((i) => <li key={i}>– {i}</li>)}</ul>
              {s.projects.length > 0 && <div className="text-xs text-ink-3">{t("inbox.yourProjects")}: {s.projects.map((p) => `${p.title} (${p.stage}, ${p.status})`).join(" · ")}</div>}
            </div>
          )}
        </Card>
        <Card>
          <CardHeader title={t("inbox.messages")} subtitle={t("inbox.messagesSub")} />
          {(msgs.data ?? []).length === 0 ? <Empty title={t("inbox.noMessages")} /> : (
            <div className="max-h-[640px] divide-y divide-line overflow-y-auto scrollbar-thin">
              {(msgs.data ?? []).map((m) => (
                <div key={m.id} className="px-4 py-2.5 text-sm">
                  <button className="flex w-full items-center gap-2 text-left" onClick={() => read(m.id)}>
                    <Badge color={m.read ? undefined : "var(--accent)"}>{m.kind}</Badge>
                    <span className={`min-w-0 flex-1 truncate ${m.read ? "text-ink-3" : "text-ink"}`}>{m.subject}</span>
                    <span className="text-[11px] text-ink-3">{new Date(m.created_at).toLocaleString()}</span>
                  </button>
                  {open === m.id && <pre className="mt-2 whitespace-pre-wrap font-sans text-xs text-ink-2">{m.body}</pre>}
                </div>
              ))}
            </div>
          )}
          <div className="border-t border-line p-3"><Button size="sm" variant="ghost" onClick={() => setNonce((n) => n + 1)}>{t("inbox.refresh")}</Button></div>
        </Card>
      </div>
    </div>
  );
}
