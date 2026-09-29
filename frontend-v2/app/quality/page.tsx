"use client";
import { FreshnessCard } from "@/components/freshness-card";
import { WorkspaceHub } from "@/components/v2/workspace-hub";
import { AttentionList, useAttention, useMarkets } from "@/components/v2/widgets";

export default function Page() {
  const mk = useMarkets();
  const att = useAttention(mk.names);
  return (
    <WorkspaceHub id="quality">
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <AttentionList items={att.items} loading={att.loading} filter={(a) => a.href.startsWith("/review") || a.href === "/data"} limit={10} />
        <FreshnessCard />
      </div>
    </WorkspaceHub>
  );
}
