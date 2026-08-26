import { useEffect, useState } from "react";
import { queues as queuesApi, type Queue, type QueueStats } from "../lib/api";
import { StatusPill } from "../components/ui";
import DashboardLayout from "./DashboardLayout";

function QueuesBody() {
  const projectId = Number(localStorage.getItem("project_id"));
  const [list, setList] = useState<Queue[]>([]);
  const [stats, setStats] = useState<Record<number, QueueStats>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [concurrency, setConcurrency] = useState(5);
  const [priority, setPriority] = useState(0);
  const [strategy, setStrategy] = useState("exponential");
  const [maxAttempts, setMaxAttempts] = useState(5);

  const refresh = async () => {
    const rows = await queuesApi.list(projectId);
    setList(rows);
    const entries = await Promise.all(rows.map((q) => queuesApi.stats(projectId, q.id).then((s) => [q.id, s] as const)));
    setStats(Object.fromEntries(entries));
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 6000);
    return () => clearInterval(id);
  }, [projectId]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    await queuesApi.create(projectId, {
      name, max_concurrency: concurrency, priority,
      retry_policy: { name: `${name}-policy`, strategy, max_attempts: maxAttempts },
    });
    setName("");
    setShowCreate(false);
    refresh();
  };

  const toggle = async (q: Queue) => {
    if (q.is_paused) await queuesApi.resume(projectId, q.id);
    else await queuesApi.pause(projectId, q.id);
    refresh();
  };

  return (
    <DashboardLayout>
      {({ onEvent }) => {
        onEvent((e) => { if (e.event?.toString().startsWith("job.") || e.event === "jobs.claimed") refresh(); });
        return (
          <div>
            <div className="mb-6 flex items-center justify-between">
              <h1 className="text-lg font-semibold">Queues</h1>
              <button onClick={() => setShowCreate(!showCreate)} className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm hover:bg-blue-500">
                {showCreate ? "Cancel" : "+ New queue"}
              </button>
            </div>

            {showCreate && (
              <form onSubmit={create} className="mb-6 grid grid-cols-2 gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4 md:grid-cols-5">
                <input placeholder="Queue name" value={name} onChange={(e) => setName(e.target.value)} required
                  className="col-span-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm md:col-span-1" />
                <input type="number" placeholder="Concurrency" value={concurrency} onChange={(e) => setConcurrency(+e.target.value)}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <input type="number" placeholder="Priority" value={priority} onChange={(e) => setPriority(+e.target.value)}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <select value={strategy} onChange={(e) => setStrategy(e.target.value)}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm">
                  <option value="fixed">fixed backoff</option>
                  <option value="linear">linear backoff</option>
                  <option value="exponential">exponential backoff</option>
                </select>
                <input type="number" placeholder="Max attempts" value={maxAttempts} onChange={(e) => setMaxAttempts(+e.target.value)}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <button type="submit" className="col-span-2 rounded-lg bg-blue-600 py-2 text-sm hover:bg-blue-500 md:col-span-5">Create</button>
              </form>
            )}

            <div className="grid gap-3">
              {list.map((q) => {
                const s = stats[q.id];
                return (
                  <div key={q.id} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
                    <div className="mb-3 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className="font-medium">{q.name}</span>
                        <StatusPill status={q.is_paused ? "draining" : "running"} />
                        <span className="text-xs text-white/40">priority {q.priority} · concurrency {q.max_concurrency}</span>
                      </div>
                      <button onClick={() => toggle(q)} className="rounded-lg border border-white/10 px-3 py-1 text-xs hover:bg-white/5">
                        {q.is_paused ? "Resume" : "Pause"}
                      </button>
                    </div>
                    {s && (
                      <div className="grid grid-cols-3 gap-2 text-xs md:grid-cols-7">
                        <Metric label="Queued" value={s.queued} />
                        <Metric label="Scheduled" value={s.scheduled} />
                        <Metric label="Claimed" value={s.claimed} />
                        <Metric label="Running" value={s.running} />
                        <Metric label="Completed" value={s.completed} />
                        <Metric label="Failed" value={s.failed} />
                        <Metric label="Dead-letter" value={s.dead_letter} />
                      </div>
                    )}
                  </div>
                );
              })}
              {list.length === 0 && <div className="text-sm text-white/40">No queues yet — create one above.</div>}
            </div>
          </div>
        );
      }}
    </DashboardLayout>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-black/20 p-2 text-center">
      <div className="text-white/40">{label}</div>
      <div className="text-sm font-medium">{value}</div>
    </div>
  );
}

export default QueuesBody;
