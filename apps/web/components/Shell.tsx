"use client";
import { ReactNode, createContext, useContext, useEffect, useRef, useState } from "react";
import { api, fmtTime } from "@/lib/api";

export type Me = {
  username: string;
  role: string;
  staff?: { display_name: string; facility_id: string; seniority?: string; specialty?: string; ward?: string; on_shift?: boolean; patient_id?: string | null; pharmacy_id?: string | null } | null;
};

export type Alert = { id: number; patient_id: string; kind: string; severity: string; message: string; tier: number; status: string; created_at: string; payload?: any };

const MeCtx = createContext<Me | null>(null);
export const useMe = () => useContext(MeCtx);

export default function Shell({ title, roles, children }: { title: string; roles: string[]; children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [open, setOpen] = useState(false);
  const [live, setLive] = useState(false);
  const [toast, setToast] = useState<Alert | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api<Me>("clinical/me")
      .then((m) => (roles.includes(m.role) ? setMe(m) : setDenied(`This portal is for ${roles.join(" / ")} accounts; you are signed in as ${m.role}.`)))
      .catch((e) => setDenied(e.message));
  }, [roles]);

  useEffect(() => {
    if (!me || ["mr", "patient", "admin"].includes(me.role)) return;
    api<Alert[]>("clinical/alerts").then(setAlerts).catch(() => undefined);
    const es = new EventSource("/api/proxy/clinical/alerts/stream");
    esRef.current = es;
    es.addEventListener("ready", () => setLive(true));
    es.addEventListener("alert", (ev) => {
      const a = JSON.parse((ev as MessageEvent).data) as Alert;
      setAlerts((xs) => [a, ...xs.filter((x) => x.id !== a.id)]);
      setToast(a);
      window.dispatchEvent(new CustomEvent("omnimed:alert", { detail: a }));
    });
    es.onerror = () => setLive(false);
    return () => es.close();
  }, [me]);

  const openAlerts = alerts.filter((a) => a.status === "open" || a.status === "escalated");

  async function ack(id: number) {
    await api(`clinical/alerts/${id}/ack`, { method: "POST" });
    setAlerts((xs) => xs.map((x) => (x.id === id ? { ...x, status: "ack" } : x)));
    if (toast?.id === id) setToast(null);
  }

  async function signOut() {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/";
  }

  return (
    <MeCtx.Provider value={me}>
      <div className="banner">Synthetic demonstration data. Not for clinical use.</div>
      <header className="topbar">
        <a href="/" className="brand">OmniMed<span>AI</span></a>
        <h1>{title}</h1>
        <div className="spacer" />
        {me && !["mr", "patient", "admin"].includes(me.role) && (
          <div className="alerts-wrap">
            <button className={`bell ${openAlerts.length ? "hot" : ""}`} onClick={() => setOpen(!open)} aria-label="alerts">
              Alerts <b>{openAlerts.length}</b> <i className={`dot ${live ? "on" : ""}`} title={live ? "live" : "reconnecting"} />
            </button>
            {open && (
              <div className="alert-panel">
                {alerts.length === 0 && <p className="muted">No alerts.</p>}
                {alerts.slice(0, 30).map((a) => (
                  <div key={a.id} className={`alert-item ${a.severity}`}>
                    <div className="row between"><b>{a.kind}</b><small>{fmtTime(a.created_at)}</small></div>
                    <p>{a.message}</p>
                    <div className="row between">
                      <small>{a.status}{a.tier ? ` · tier ${a.tier}` : ""}</small>
                      {(a.status === "open" || a.status === "escalated") && <button className="small" onClick={() => ack(a.id)}>Acknowledge</button>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {me && (
          <div className="who">
            <b>{me.staff?.display_name || me.username}</b>
            <small>{me.role}{me.staff?.facility_id ? ` · ${me.staff.facility_id}` : ""}{me.staff?.on_shift === false ? " · off shift" : ""}</small>
          </div>
        )}
        <button className="ghost" onClick={signOut}>Sign out</button>
      </header>
      {toast && (
        <div className={`toast ${toast.severity}`} role="alert">
          <b>{toast.kind}</b>
          <p>{toast.message}</p>
          <div className="row">
            <button onClick={() => ack(toast.id)}>Acknowledge</button>
            <button className="ghost" onClick={() => setToast(null)}>Dismiss</button>
          </div>
        </div>
      )}
      <main className="page">
        {denied ? <div className="error">{denied} <a href="/">Sign in with another account</a></div> : me ? children : <p className="muted">Loading...</p>}
      </main>
    </MeCtx.Provider>
  );
}
