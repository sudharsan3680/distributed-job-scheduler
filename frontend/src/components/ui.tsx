export function StatCard({ label, value, tone }: { label: string; value: string | number; tone?: "ok" | "warn" | "bad" }) {
  const toneClasses = tone === "bad" ? "text-red-400" : tone === "warn" ? "text-amber-400" : "text-emerald-400";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="text-xs uppercase tracking-wide text-white/50">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${toneClasses}`}>{value}</div>
    </div>
  );
}

const STATUS_COLORS: Record<string, string> = {
  queued: "bg-slate-500/20 text-slate-300",
  scheduled: "bg-sky-500/20 text-sky-300",
  claimed: "bg-amber-500/20 text-amber-300",
  running: "bg-blue-500/20 text-blue-300",
  completed: "bg-emerald-500/20 text-emerald-300",
  failed: "bg-orange-500/20 text-orange-300",
  dead_letter: "bg-red-500/20 text-red-300",
  cancelled: "bg-white/10 text-white/50",
  idle: "bg-slate-500/20 text-slate-300",
  busy: "bg-blue-500/20 text-blue-300",
  draining: "bg-amber-500/20 text-amber-300",
  offline: "bg-white/10 text-white/40",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_COLORS[status] ?? "bg-white/10 text-white/60"}`}>
      {status.replace("_", " ")}
    </span>
  );
}
