"use client";
import { useState } from "react";

const PERSONAS = [
  { u: "dr.rao", label: "Doctor", who: "Dr. Anand Rao, attending, F1" },
  { u: "dr.sen", label: "Doctor (resident)", who: "Dr. Kavya Sen, F1" },
  { u: "nurse.anita", label: "Nurse / Ward", who: "Anita, ward W3" },
  { u: "pharm.kiran", label: "Pharmacist", who: "Kiran, hospital pharmacy PH1" },
  { u: "lab.suresh", label: "Pathology / Lab", who: "Suresh, central lab" },
  { u: "mr.vikram", label: "Medical Rep", who: "Vikram, field force" },
  { u: "patient.lakshmi", label: "Patient", who: "Lakshmi Iyer (P0001)" },
  { u: "admin.audit", label: "Audit / Admin", who: "Compliance officer" },
  { u: "dr.mehta", label: "Doctor (other facility)", who: "Dr. Rohit Mehta, F2" },
];

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(u = username, p = password) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "sign-in failed");
      window.location.href = data.portal;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="card">
        <div className="row between">
          <div>
            <h1 style={{ fontSize: 24 }}>OmniMed <span style={{ color: "#127a8a" }}>AI</span></h1>
            <p className="muted">Clinical, diagnostic, pharmacy and life-sciences portals over one clinical synapse.</p>
          </div>
          <span className="badge warn">Synthetic data. Not for clinical use.</span>
        </div>
        <form className="row" style={{ margin: "18px 0" }} onSubmit={(e) => { e.preventDefault(); signIn(); }}>
          <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
          <button type="submit" disabled={busy} style={{ alignSelf: "flex-end" }}>Sign in</button>
        </form>
        {error && <div className="error">{error}</div>}
        <h3>Demo personas (password <code>omnimed</code>)</h3>
        <div className="personas">
          {PERSONAS.map((p) => (
            <button key={p.u} className="persona" disabled={busy} onClick={() => signIn(p.u, "omnimed")}>
              <b>{p.label}</b>
              <small>{p.who}</small>
            </button>
          ))}
        </div>
        <p className="muted" style={{ marginTop: 14 }}>Authentication is OpenID Connect via Keycloak; tokens are held in httpOnly cookies and every API call is authorised by RBAC + ABAC and written to the hash-chained audit ledger.</p>
      </div>
    </div>
  );
}
