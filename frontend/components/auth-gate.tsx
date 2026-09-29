"use client";
import { useEffect, useState } from "react";
import { KeyRound, LogOut } from "lucide-react";
import { Button, Card, Input } from "@/components/ui/primitives";
import { get, getToken, setToken } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Me = { auth_enabled: boolean; authenticated: boolean; name: string; role: string; email: string | null };

/** Sign-in dialog shown whenever the API answers 401, plus the "signed in as" line in the sidebar. */
export function AuthGate() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const onRequired = () => setOpen(true);
    window.addEventListener("dmis-auth-required", onRequired);
    return () => window.removeEventListener("dmis-auth-required", onRequired);
  }, []);
  const signIn = async () => {
    setToken(value.trim());
    try {
      const me = await get<Me>("/auth/me");
      if (me.auth_enabled && !me.authenticated) throw new Error("token not accepted");
      setOpen(false);
      window.location.reload();
    } catch {
      setToken(null);
      setError(t("auth.rejected"));
    }
  };
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <Card className="w-full max-w-md p-5">
        <div className="flex items-center gap-2 text-sm font-semibold"><KeyRound className="h-4 w-4 text-accent" /> {t("auth.title")}</div>
        <p className="mt-1 text-xs text-ink-3">{t("auth.body")}</p>
        <Input className="mt-3" type="password" placeholder="dmis_..." value={value} onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && signIn()} autoFocus />
        {error && <div className="mt-2 text-xs text-bad">{error}</div>}
        <div className="mt-4 flex justify-end"><Button onClick={signIn} disabled={!value.trim()}>{t("auth.signIn")}</Button></div>
      </Card>
    </div>
  );
}

export function WhoAmI() {
  const { t } = useI18n();
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => { get<Me>("/auth/me").then(setMe).catch(() => setMe(null)); }, []);
  if (!me) return null;
  if (!me.auth_enabled) return <div className="text-[10px] text-ink-3">{t("auth.off")}</div>;
  return (
    <div className="flex items-center justify-between gap-2 text-[11px] text-ink-2">
      <span className="truncate">{me.authenticated ? `${me.name} · ${me.role}` : t("auth.notSigned")}</span>
      {getToken() && (
        <button title={t("auth.signOut")} aria-label={t("auth.signOut")} onClick={() => { setToken(null); window.location.reload(); }}><LogOut className="h-3.5 w-3.5" /></button>
      )}
    </div>
  );
}
