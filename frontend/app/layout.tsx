import type { Metadata } from "next";
import "./globals.css";
import { MobileNav, Nav } from "@/components/nav";
import { AuthGate } from "@/components/auth-gate";
import { FloatingReport, PageViewTracker } from "@/components/pilot";
import { LangProvider } from "@/lib/i18n";
import { QueryProvider } from "@/lib/query";
import { THEME_INIT_SCRIPT } from "@/lib/theme-script";
import { SkipLink } from "@/components/ui/skip-link";

export const metadata: Metadata = {
  title: "DMIS · Dental Market Intelligence OS",
  description: "Global dental product intelligence & supply-chain discovery command center",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme is set by the inline script before paint, so the server markup differs by design.
    <html lang="en" className="h-full antialiased" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="flex h-full min-h-screen bg-bg text-ink">
        <QueryProvider>
          <LangProvider>
            <SkipLink />
            <Nav />
            <main id="main" tabIndex={-1} className="focus:outline-none hud-grid relative min-w-0 flex-1 overflow-x-hidden pb-16"><MobileNav />{children}</main>
            <AuthGate />
            <PageViewTracker />
            <FloatingReport />
          </LangProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
