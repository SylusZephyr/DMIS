"use client";
import { WorkspaceHub } from "@/components/v2/workspace-hub";
import { ActivityFeed, PipelineFunnel } from "@/components/v2/widgets";

export default function Page() {
  return (
    <WorkspaceHub id="execute">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2"><PipelineFunnel /><ActivityFeed limit={6} /></div>
    </WorkspaceHub>
  );
}
