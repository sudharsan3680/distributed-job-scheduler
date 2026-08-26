import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { dashboard, type SystemHealth, type QueueStats } from "../lib/api";
import { StatCard } from "../components/ui";
import DashboardLayout from "./DashboardLayout";

function OverviewBody() {
  const projectId = Number(localStorage.getItem("project_id"));
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [queueStats, setQueueStats] = useState<QueueStats[]>([]);

  const refresh = () => {
    dashboard.health(projectId).then(setHealth).catch(() => {});
    dashboard.queueStats(projectId).then(setQueueStats).catch(() => {});
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [projectId]);

  return (
    <DashboardLayout>
      {({ onEvent }) => {
        onEvent(() => refresh());
        return (
          <div>
            <h1 className="mb-6 text-lg font-semibold">System health</h1>
            {health && (
              <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard label="Queues" value={health.total_queues} />
                <StatCard label="Workers online" value={health.total_workers_online} />
                <StatCard label="Jobs queued" value={health.jobs_queued} />
                <StatCard label="Jobs running" value={health.jobs_running} />
                <StatCard label="Completed (1h)" value={health.jobs_completed_last_hour} tone="ok" />
                <StatCard label="Dead-lettered (1h)" value={health.jobs_failed_last_hour} tone={health.jobs_failed_last_hour > 0 ? "bad" : "ok"} />
                <StatCard label="DLQ total" value={health.dead_letter_count} tone={health.dead_letter_count > 0 ? "warn" : "ok"} />
              </div>
            )}

            <h2 className="mb-3 text-sm font-medium text-white/70">Throughput by queue (last hour)</h2>
            <div className="h-72 rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={queueStats}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                  <XAxis dataKey="name" stroke="#ffffff60" fontSize={12} />
                  <YAxis stroke="#ffffff60" fontSize={12} />
                  <Tooltip contentStyle={{ background: "#11161c", border: "1px solid #ffffff20" }} />
                  <Bar dataKey="throughput_last_hour" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Completed/hr" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        );
      }}
    </DashboardLayout>
  );
}

export default OverviewBody;
