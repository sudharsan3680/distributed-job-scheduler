import { useEffect, useState } from "react";
import { queues as queuesApi, jobs as jobsApi, type Queue, type Job, type JobDetail } from "../lib/api";
import { StatusPill } from "../components/ui";
import DashboardLayout from "./DashboardLayout";

function JobsBody() {
  const projectId = Number(localStorage.getItem("project_id"));
  const [queueList, setQueueList] = useState<Queue[]>([]);
  const [queueId, setQueueId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [rows, setRows] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<JobDetail | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [jobType, setJobType] = useState("noop");
  const [payload, setPayload] = useState("{}");
  const [delaySeconds, setDelaySeconds] = useState<number | "">("");

  useEffect(() => {
    queuesApi.list(projectId).then((qs) => {
      setQueueList(qs);
      if (qs.length && !queueId) setQueueId(qs[0].id);
    });
  }, [projectId]);

  const refresh = () => {
    if (!queueId) return;
    const params: Record<string, unknown> = {};
    if (statusFilter) params.status = statusFilter;
    jobsApi.list(projectId, queueId, params).then((page) => {
      setRows(page.items);
      setTotal(page.total);
    });
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [projectId, queueId, statusFilter]);

  const openDetail = async (jobId: number) => {
    const d = await jobsApi.get(projectId, jobId);
    setSelected(d);
  };

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!queueId) return;
    let parsed = {};
    try { parsed = JSON.parse(payload); } catch { /* leave as {} on bad JSON */ }
    await jobsApi.create(projectId, queueId, {
      job_type: jobType, payload: parsed,
      ...(delaySeconds !== "" ? { delay_seconds: Number(delaySeconds) } : {}),
    });
    setShowCreate(false);
    refresh();
  };

  const retry = async (jobId: number) => { await jobsApi.retry(projectId, jobId); refresh(); };
  const cancel = async (jobId: number) => { await jobsApi.cancel(projectId, jobId); refresh(); };

  return (
    <DashboardLayout>
      {({ onEvent }) => {
        onEvent(() => refresh());
        return (
          <div>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <h1 className="text-lg font-semibold">Job Explorer</h1>
              <select value={queueId ?? ""} onChange={(e) => setQueueId(Number(e.target.value))}
                className="rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-sm">
                {queueList.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
              </select>
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                className="rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-sm">
                <option value="">All statuses</option>
                {["queued", "scheduled", "claimed", "running", "completed", "failed", "dead_letter", "cancelled"].map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
              <span className="text-xs text-white/40">{total} job(s)</span>
              <button onClick={() => setShowCreate(!showCreate)} className="ml-auto rounded-lg bg-blue-600 px-3 py-1.5 text-sm hover:bg-blue-500">
                {showCreate ? "Cancel" : "+ Submit job"}
              </button>
            </div>

            {showCreate && (
              <form onSubmit={create} className="mb-4 grid grid-cols-1 gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4 md:grid-cols-4">
                <input placeholder="job_type (e.g. noop, sleep)" value={jobType} onChange={(e) => setJobType(e.target.value)}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <input placeholder="delay seconds (optional)" type="number" value={delaySeconds}
                  onChange={(e) => setDelaySeconds(e.target.value === "" ? "" : Number(e.target.value))}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <textarea placeholder='payload JSON, e.g. {"seconds": 3}' value={payload} onChange={(e) => setPayload(e.target.value)}
                  className="col-span-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs" rows={1} />
                <button type="submit" className="col-span-full rounded-lg bg-blue-600 py-2 text-sm hover:bg-blue-500">Submit</button>
              </form>
            )}

            <div className="overflow-hidden rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead className="bg-white/5 text-left text-xs text-white/50">
                  <tr>
                    <th className="px-4 py-2">ID</th>
                    <th className="px-4 py-2">Type</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Attempts</th>
                    <th className="px-4 py-2">Run at</th>
                    <th className="px-4 py-2">Updated</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((j) => (
                    <tr key={j.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="cursor-pointer px-4 py-2" onClick={() => openDetail(j.id)}>#{j.id}</td>
                      <td className="px-4 py-2">{j.job_type}</td>
                      <td className="px-4 py-2"><StatusPill status={j.status} /></td>
                      <td className="px-4 py-2">{j.attempt_count}/{j.max_attempts}</td>
                      <td className="px-4 py-2 text-xs text-white/50">{new Date(j.run_at).toLocaleString()}</td>
                      <td className="px-4 py-2 text-xs text-white/50">{new Date(j.updated_at).toLocaleString()}</td>
                      <td className="space-x-2 px-4 py-2 text-right">
                        {(j.status === "failed" || j.status === "dead_letter") && (
                          <button onClick={() => retry(j.id)} className="text-xs text-blue-400 hover:underline">Retry</button>
                        )}
                        {!["completed", "dead_letter", "cancelled"].includes(j.status) && (
                          <button onClick={() => cancel(j.id)} className="text-xs text-red-400 hover:underline">Cancel</button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={7} className="px-4 py-6 text-center text-white/40">No jobs match this filter.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {selected && (
              <div className="fixed inset-0 z-10 flex justify-end bg-black/50" onClick={() => setSelected(null)}>
                <div className="h-full w-full max-w-md overflow-y-auto border-l border-white/10 bg-[#0e131a] p-5" onClick={(e) => e.stopPropagation()}>
                  <div className="mb-4 flex items-center justify-between">
                    <h2 className="text-base font-semibold">Job #{selected.id}</h2>
                    <button onClick={() => setSelected(null)} className="text-white/40 hover:text-white">✕</button>
                  </div>
                  <div className="mb-4 space-y-1 text-sm">
                    <div><span className="text-white/40">Type:</span> {selected.job_type}</div>
                    <div><span className="text-white/40">Status:</span> <StatusPill status={selected.status} /></div>
                    <div><span className="text-white/40">Attempts:</span> {selected.attempt_count}/{selected.max_attempts}</div>
                    {selected.claimed_by_worker_id && <div><span className="text-white/40">Claimed by worker:</span> #{selected.claimed_by_worker_id}</div>}
                  </div>
                  <div className="mb-4">
                    <div className="mb-1 text-xs uppercase text-white/40">Payload</div>
                    <pre className="overflow-x-auto rounded-lg bg-black/30 p-3 text-xs">{JSON.stringify(selected.payload, null, 2)}</pre>
                  </div>
                  <div>
                    <div className="mb-1 text-xs uppercase text-white/40">Execution history</div>
                    <div className="space-y-2">
                      {selected.executions.map((ex) => (
                        <div key={ex.id} className="rounded-lg border border-white/10 p-2 text-xs">
                          <div className="flex justify-between">
                            <span>Attempt {ex.attempt_number}</span>
                            <StatusPill status={ex.status} />
                          </div>
                          {ex.duration_ms != null && <div className="text-white/40">{ex.duration_ms}ms</div>}
                          {ex.error_message && <div className="mt-1 text-red-300">{ex.error_message}</div>}
                        </div>
                      ))}
                      {selected.executions.length === 0 && <div className="text-xs text-white/40">No attempts yet.</div>}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      }}
    </DashboardLayout>
  );
}

export default JobsBody;
