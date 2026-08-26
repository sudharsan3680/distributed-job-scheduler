import { useEffect, useState } from "react";
import { dashboard, type WorkerOut } from "../lib/api";
import { StatusPill } from "../components/ui";
import DashboardLayout from "./DashboardLayout";

function timeAgo(iso: string | null) {
  if (!iso) return "never";
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function WorkersBody() {
  const projectId = Number(localStorage.getItem("project_id"));
  const [list, setList] = useState<WorkerOut[]>([]);

  const refresh = () => dashboard.workers(projectId).then(setList);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [projectId]);

  return (
    <DashboardLayout>
      {({ onEvent }) => {
        onEvent((e) => { if (e.event?.toString().startsWith("worker.")) refresh(); });
        return (
          <div>
            <h1 className="mb-6 text-lg font-semibold">Workers</h1>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {list.map((w) => {
                const stale = w.last_heartbeat_at && Date.now() - new Date(w.last_heartbeat_at).getTime() > 20000;
                return (
                  <div key={w.id} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="font-medium">{w.label}</span>
                      <StatusPill status={stale && w.status !== "offline" ? "offline" : w.status} />
                    </div>
                    <div className="space-y-1 text-xs text-white/50">
                      <div>{w.hostname}</div>
                      <div>Load: {w.current_load}/{w.concurrency_capacity}</div>
                      <div>Last heartbeat: {timeAgo(w.last_heartbeat_at)}</div>
                      <div>Started: {new Date(w.started_at).toLocaleString()}</div>
                    </div>
                  </div>
                );
              })}
              {list.length === 0 && <div className="text-sm text-white/40">No workers registered yet.</div>}
            </div>
          </div>
        );
      }}
    </DashboardLayout>
  );
}

export default WorkersBody;
