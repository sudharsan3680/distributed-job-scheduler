import { useEffect, useState } from "react";
import { queues as queuesApi, schedules as schedulesApi, type Queue, type ScheduledJob } from "../lib/api";
import DashboardLayout from "./DashboardLayout";

function SchedulesBody() {
  const projectId = Number(localStorage.getItem("project_id"));
  const [queueList, setQueueList] = useState<Queue[]>([]);
  const [queueId, setQueueId] = useState<number | null>(null);
  const [rows, setRows] = useState<ScheduledJob[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [jobType, setJobType] = useState("noop");
  const [cron, setCron] = useState("*/5 * * * *");
  const [payload, setPayload] = useState("{}");

  useEffect(() => {
    queuesApi.list(projectId).then((qs) => {
      setQueueList(qs);
      if (qs.length && !queueId) setQueueId(qs[0].id);
    });
  }, [projectId]);

  const refresh = () => {
    if (!queueId) return;
    schedulesApi.list(projectId, queueId).then(setRows).catch(() => setRows([]));
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 6000);
    return () => clearInterval(id);
  }, [projectId, queueId]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!queueId) return;
    let parsed = {};
    try { parsed = JSON.parse(payload); } catch { /* ignore bad JSON */ }
    await schedulesApi.create(projectId, queueId, { name, job_type: jobType, cron_expression: cron, payload: parsed });
    setShowCreate(false);
    setName(""); setPayload("{}");
    refresh();
  };

  const pause = async (s: ScheduledJob) => {
    await schedulesApi.pause(projectId, s.id);
    refresh();
  };

  return (
    <DashboardLayout>
      {({ onEvent }) => {
        onEvent(() => refresh());
        return (
          <div>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <h1 className="text-lg font-semibold">Schedules</h1>
              <select value={queueId ?? ""} onChange={(e) => setQueueId(Number(e.target.value))}
                className="rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-sm">
                {queueList.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
              </select>
              <button onClick={() => setShowCreate(!showCreate)} className="ml-auto rounded-lg bg-blue-600 px-3 py-1.5 text-sm hover:bg-blue-500">
                {showCreate ? "Cancel" : "+ New schedule"}
              </button>
            </div>

            <p className="mb-4 text-sm text-white/50">Recurring (cron) jobs fire automatically and materialize one Job instance per occurrence into the selected queue.</p>

            {showCreate && (
              <form onSubmit={create} className="mb-4 grid grid-cols-1 gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4 md:grid-cols-4">
                <input placeholder="Schedule name" value={name} onChange={(e) => setName(e.target.value)} required
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <input placeholder="job_type" value={jobType} onChange={(e) => setJobType(e.target.value)}
                  className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm" />
                <input placeholder="*/5 * * * *" value={cron} onChange={(e) => setCron(e.target.value)} required
                  className="col-span-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs" />
                <textarea placeholder='payload JSON, e.g. {"a":1}' value={payload} onChange={(e) => setPayload(e.target.value)}
                  className="col-span-4 rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs" rows={2} />
                <button type="submit" className="col-span-full rounded-lg bg-blue-600 py-2 text-sm hover:bg-blue-500">Create schedule</button>
              </form>
            )}

            <div className="overflow-hidden rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead className="bg-white/5 text-left text-xs text-white/50">
                  <tr>
                    <th className="px-4 py-2">Name</th>
                    <th className="px-4 py-2">Type</th>
                    <th className="px-4 py-2">Cron</th>
                    <th className="px-4 py-2">Next run</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id} className="border-t border-white/5">
                      <td className="px-4 py-2">{s.name}</td>
                      <td className="px-4 py-2">{s.job_type}</td>
                      <td className="px-4 py-2 font-mono text-xs">{s.cron_expression}</td>
                      <td className="px-4 py-2 text-xs text-white/50">{s.next_run_at ? new Date(s.next_run_at).toLocaleString() : "—"}</td>
                      <td className="px-4 py-2 text-xs">{s.is_active ? "active" : "paused"}</td>
                      <td className="px-4 py-2 text-right">
                        <button onClick={() => pause(s)} className="text-xs text-blue-400 hover:underline">
                          {s.is_active ? "Pause" : "Paused"}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-6 text-center text-white/40">No schedules for this queue yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        );
      }}
    </DashboardLayout>
  );
}

export default SchedulesBody;
