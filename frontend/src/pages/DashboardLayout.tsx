import { useRef, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useProjectEvents, type LiveEvent } from "../lib/useProjectEvents";

const NAV = [
  { to: "/dashboard", label: "Overview", end: true },
  { to: "/dashboard/queues", label: "Queues" },
  { to: "/dashboard/jobs", label: "Job Explorer" },
   { to: "/dashboard/workers", label: "Workers" },
   { to: "/dashboard/schedules", label: "Schedules" },
   { to: "/dashboard/dlq", label: "Dead Letter Queue" },
];

export default function DashboardLayout({ children }: { children: (opts: { onEvent: (h: (e: LiveEvent) => void) => void }) => ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const projectId = Number(localStorage.getItem("project_id"));
  const handlersRef = useRef<((e: LiveEvent) => void)[]>([]);

  const { connected } = useProjectEvents(projectId || null, (e) => handlersRef.current.forEach((h) => h(e)));

  const onEvent = (h: (e: LiveEvent) => void) => {
    if (!handlersRef.current.includes(h)) handlersRef.current.push(h);
  };

  if (!projectId) {
    nav("/select-project");
    return null;
  }

  return (
    <div className="flex min-h-screen bg-[#0b0f14] text-white">
      <aside className="w-56 shrink-0 border-r border-white/10 p-4">
        <div className="mb-6 text-sm font-semibold">Job Scheduler</div>
        <nav className="space-y-1">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) =>
                `block rounded-lg px-3 py-2 text-sm ${isActive ? "bg-blue-600/20 text-blue-300" : "text-white/60 hover:bg-white/5"}`
              }>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <button onClick={() => { localStorage.removeItem("project_id"); nav("/select-project"); }}
          className="mt-6 block w-full rounded-lg px-3 py-2 text-left text-sm text-white/40 hover:bg-white/5">
          Switch project
        </button>
        <button onClick={() => { logout(); nav("/login"); }}
          className="mt-1 block w-full rounded-lg px-3 py-2 text-left text-sm text-white/40 hover:bg-white/5">
          Sign out
        </button>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-3">
          <div className="text-sm text-white/50">{user?.full_name}</div>
          <div className="flex items-center gap-2 text-xs">
            <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-white/20"}`} />
            {connected ? "Live" : "Connecting..."}
          </div>
        </div>
        <div className="p-6">{children({ onEvent })}</div>
      </main>
    </div>
  );
}
