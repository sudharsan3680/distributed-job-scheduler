import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { projects as projectsApi, type Project } from "../lib/api";

export default function ProjectSetupPage() {
  const nav = useNavigate();
  const [list, setList] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const orgId = 1; // registering creates exactly one org (id 1 for the first user); a full build would let users pick among orgs.

  useEffect(() => {
    projectsApi.list(orgId).then((r) => setList(r)).catch(() => setError("Could not load projects")).finally(() => setLoading(false));
  }, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const p = await projectsApi.create(orgId, { name, slug });
    setNewKey(p.api_key ?? null);
    setList([...list, p]);
  };

  const select = (id: number) => {
    localStorage.setItem("project_id", String(id));
    nav("/dashboard");
  };

  return (
    <div className="mx-auto max-w-2xl px-4 py-12">
      <h1 className="mb-6 text-xl font-semibold">Select or create a project</h1>

      {loading && <div className="text-white/50">Loading...</div>}
      {error && <div className="text-red-400">{error}</div>}

      <div className="mb-8 grid gap-3">
        {list.map((p) => (
          <button key={p.id} onClick={() => select(p.id)}
            className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-left hover:border-blue-500">
            <span>{p.name}</span>
            <span className="text-xs text-white/40">{p.slug}</span>
          </button>
        ))}
        {!loading && list.length === 0 && <div className="text-white/50">No projects yet — create your first one below.</div>}
      </div>

      <form onSubmit={create} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <h2 className="mb-3 text-sm font-medium">New project</h2>
        <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required
          className="mb-2 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-blue-500" />
        <input placeholder="slug-like-this" value={slug} onChange={(e) => setSlug(e.target.value)} required pattern="[a-z0-9-]+"
          className="mb-3 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-blue-500" />
        <button type="submit" className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium hover:bg-blue-500">Create project</button>
      </form>

      {newKey && (
        <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
          <div className="mb-1 font-medium text-amber-300">Save this API key now — it won't be shown again</div>
          <code className="block break-all rounded bg-black/40 p-2 text-xs">{newKey}</code>
          <p className="mt-2 text-xs text-white/50">Workers authenticate with this key via the <code>X-API-Key</code> header.</p>
        </div>
      )}
    </div>
  );
}
