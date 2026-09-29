"use client";
import { useMarket } from "@/components/market-switcher";
import { WorkspaceHub } from "@/components/v2/workspace-hub";
import { OpportunityLandscape, OpportunityList, useMarkets, useOpportunities } from "@/components/v2/widgets";

export default function Page() {
  const [market] = useMarket();
  const mk = useMarkets();
  const opp = useOpportunities(mk.names);
  return (
    <WorkspaceHub id="decide">
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <OpportunityList rows={opp.rows} loading={opp.loading} colors={mk.colors} market={market || undefined} limit={6} />
        <OpportunityLandscape rows={opp.rows} loading={opp.loading} names={mk.names} colors={mk.colors} />
      </div>
    </WorkspaceHub>
  );
}
