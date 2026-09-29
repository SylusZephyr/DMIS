import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/v2/shell";
import { AuthGate } from "@/components/auth-gate";
import { FloatingReport, PageViewTracker } from "@/components/pilot";
import { LangProvider } from "@/lib/i18n";
import { QueryProvider } from "@/lib/query";
import { THEME_INIT_SCRIPT } from "@/lib/theme-script";
import { SkipLink } from "@/components/ui/skip-link";

export const metadata: Metadata = {
  title: "DMIS v2 · Dental Market Intelligence OS",
  description: "Global dental product intelligence & supply-chain discovery command center",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is set by the inline script before paint, so the server markup differs by design.
    <html lang="en" className="h-full antialiased" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-bg text-ink">
        <QueryProvider>
          <LangProvider>
            <SkipLink />
            <AppShell>{children}</AppShell>
            <AuthGate />
            <PageViewTracker />
            <FloatingReport />
          </LangProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
