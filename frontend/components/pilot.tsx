"use client";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { MessageSquareWarning } from "lucide-react";
import { Button, Card, Input } from "@/components/ui/primitives";
import { post } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** Records which page was opened (path only, no content) for pilot usage statistics. */
export function PageViewTracker() {
  const path = usePathname();
  useEffect(() => {
    post("/telemetry/view", { path }).catch(() => { /* usage is best effort */ });
  }, [path]);
  return null;
}

type Target = { kind: string; id?: string | null; market?: string | null; field?: string; shown?: string };

function targetFromPath(path: string): Target {
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "products" && parts[1]) return { kind: "product", id: parts[1] };
  if (parts[0] === "markets" && parts[1]) return { kind: "market", id: parts[1], market: parts[1] };
  return { kind: "page", id: path };
}

/** "This looks wrong": a report on the current page (or an explicit target), triaged by the pilot team. */
export function ReportProblem({ target, label, className, iconOnly }: { target?: Target; label?: string; className?: string; iconOnly?: boolean }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { t } = useI18n();
  const [comment, setComment] = useState("");
  const [field, setField] = useState(target?.field ?? "");
  const [state, setState] = useState<"idle" | "sent" | "error">("idle");
  const tg = target ?? targetFromPath(path);
  const send = async () => {
    try {
      await post("/feedback", { target_kind: tg.kind, target_id: tg.id ?? null, market: tg.market ?? null, field: field || null,
        shown_value: tg.shown ?? null, comment, page: path });
      setState("sent"); setComment("");
      setTimeout(() => { setOpen(false); setState("idle"); }, 1200);
    } catch { setState("error"); }
  };
  return (
    <>
      <button onClick={() => setOpen(true)} className={className ?? "inline-flex items-center gap-1 text-xs text-ink-3 hover:text-warn"}
        aria-label={iconOnly ? label ?? t("report.button") : undefined} title={iconOnly ? label ?? t("report.button") : undefined}>
        <MessageSquareWarning className="h-3.5 w-3.5 shrink-0" />
        {/* icon-only buttons show their label while hovered or focused, so they never cover the page */}
        <span className={iconOnly ? "hidden group-hover:inline group-focus-within:inline" : undefined}>{label ?? t("report.button")}</span>
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setOpen(false)}>
          <Card className="w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold">{t("report.title")}</div>
            <p className="mt-1 text-xs text-ink-3">{t("report.about", { k: tg.kind, id: tg.id ? ` · ${tg.id}` : "" })}</p>
            <Input className="mt-3" placeholder={t("report.field")} value={field} onChange={(e) => setField(e.target.value)} />
            <textarea className="mt-2 h-28 w-full rounded-lg border border-line bg-panel-2 p-2 text-sm focus:border-accent focus:outline-none"
              placeholder={t("report.commentPh")} value={comment} onChange={(e) => setComment(e.target.value)} />
            {state === "error" && <div className="text-xs text-bad">{t("report.error")}</div>}
            {state === "sent" && <div className="text-xs text-good">{t("report.sent")}</div>}
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>{t("report.cancel")}</Button>
              <Button disabled={!comment.trim()} onClick={send}>{t("report.send")}</Button>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

/** Floating entry point on every page. */
export function FloatingReport() {
  return (
    <div className="group fixed bottom-4 right-4 z-40">
      <ReportProblem iconOnly className="inline-flex h-9 min-w-9 items-center justify-center gap-1.5 rounded-full border border-line bg-panel/90 px-2.5 text-xs text-ink-2 shadow-lg backdrop-blur hover:border-warn hover:text-warn" />
    </div>
  );
}
