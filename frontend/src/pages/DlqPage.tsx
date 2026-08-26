import { useEffect, useState } from "react";
import { jobs as jobsApi, type Job } from "../lib/api";
import DashboardLayout from "./DashboardLayout";

function DlqBody() {
  const projectId = Number(localStorage.getItem("project_id"));
  const [rows, setRows] = useState<Job[]>([]);

  const refresh = () => jobsApi.dlq(projectId).then(setRows);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 6000);
    return () => clearInterval(id);
  }, [projectId]);

  const replay = async (jobId: number) => {
    await jobsApi.retry(projectId, jobId);
    refresh();
  };

  return (
    <DashboardLayout>
      {({ onEvent }) => {
        onEvent((e) => { if (e.event === "job.dead_letter" || e.event === "job.retried") refresh(); });
        return (
          <div>
            <h1 className="mb-2 text-lg font-semibold">Dead Letter Queue</h1>
            <p className="mb-6 text-sm text-white/50">Jobs that exhausted all retry attempts. Replay re-queues them for another full attempt cycle.</p>
            <div className="overflow-hidden rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead className="bg-white/5 text-left text-xs text-white/50">
                  <tr>
                    <th className="px-4 py-2">ID</th>
                    <th className="px-4 py-2">Type</th>
                    <th className="px-4 py-2">Attempts</th>
                    <th className="px-4 py-2">Last updated</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((j) => (
                    <tr key={j.id} className="border-t border-white/5">
                      <td className="px-4 py-2">#{j.id}</td>
                      <td className="px-4 py-2">{j.job_type}</td>
                      <td className="px-4 py-2">{j.attempt_count}/{j.max_attempts}</td>
                      <td className="px-4 py-2 text-xs text-white/50">{new Date(j.updated_at).toLocaleString()}</td>
                      <td className="px-4 py-2 text-right">
                        <button onClick={() => replay(j.id)} className="text-xs text-blue-400 hover:underline">Replay</button>
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-6 text-center text-white/40">Nothing in the dead letter queue 🎉</td></tr>
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

export default DlqBody;
