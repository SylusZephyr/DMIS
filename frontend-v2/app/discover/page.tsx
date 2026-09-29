"use client";
import { WorkspaceHub } from "@/components/v2/workspace-hub";
import { MarketCards, useMarkets } from "@/components/v2/widgets";

export default function Page() {
  const mk = useMarkets();
  return <WorkspaceHub id="discover"><MarketCards markets={mk.data ?? []} loading={!mk.data} /></WorkspaceHub>;
}
