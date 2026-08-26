import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function LoginPage() {
  const { login, register } = useAuth();
  const nav = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, fullName, orgName);
      nav("/");
    } catch (err: any) {
      setError(err?.response?.data?.error?.message ?? err?.response?.data?.detail ?? "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0b0f14] px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-white/10 bg-white/[0.03] p-6">
        <h1 className="mb-1 text-lg font-semibold">Distributed Job Scheduler</h1>
        <p className="mb-6 text-sm text-white/50">{mode === "login" ? "Sign in to your dashboard" : "Create your account & organization"}</p>

        {mode === "register" && (
          <>
            <label className="mb-1 block text-xs text-white/60">Full name</label>
            <input value={fullName} onChange={(e) => setFullName(e.target.value)} required
              className="mb-3 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-blue-500" />
            <label className="mb-1 block text-xs text-white/60">Organization name</label>
            <input value={orgName} onChange={(e) => setOrgName(e.target.value)} required
              className="mb-3 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-blue-500" />
          </>
        )}

        <label className="mb-1 block text-xs text-white/60">Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
          className="mb-3 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-blue-500" />

        <label className="mb-1 block text-xs text-white/60">Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8}
          className="mb-4 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-blue-500" />

        {error && <div className="mb-3 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}

        <button disabled={busy} type="submit"
          className="w-full rounded-lg bg-blue-600 py-2 text-sm font-medium hover:bg-blue-500 disabled:opacity-50">
          {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}
        </button>

        <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}
          className="mt-3 w-full text-center text-xs text-white/50 hover:text-white/80">
          {mode === "login" ? "Need an account? Register" : "Already have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
