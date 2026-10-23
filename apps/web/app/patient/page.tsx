"use client";
import { useState } from "react";
import Shell, { useMe } from "@/components/Shell";
import { AsyncButton, Badge, Card, Empty, ErrorBox, Tabs, flagTone } from "@/components/ui";
import { api, fmtDate, fmtTime, useApi } from "@/lib/api";

export default function PatientPortal() {
  return <Shell title="My health" roles={["patient"]}><Patient /></Shell>;
}

function Patient() {
  const me = useMe();
  const pid = me?.staff?.patient_id || "";
  const [tab, setTab] = useState("results");
  if (!pid) return <ErrorBox error="This account is not linked to a patient record." />;
  return (
    <>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "results", label: "My results" }, { id: "meds", label: "Medicines" }, { id: "appts", label: "Appointments" }, { id: "locker", label: "Health locker (ABDM)" }, { id: "consents", label: "Consents" }]} />
      {tab === "results" && <Results pid={pid} />}
      {tab === "meds" && <Meds pid={pid} />}
      {tab === "appts" && <Appointments pid={pid} />}
      {tab === "locker" && <Locker />}
      {tab === "consents" && <Consents />}
    </>
  );
}

function Results({ pid }: { pid: string }) {
  const [lang, setLang] = useState("en");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const reports = useApi<any[]>(`diag/reports?patient_id=${pid}`, [pid]);
  async function load(l: string) {
    setLang(l); setErr(null);
    try { setData(await api("ai/translate", { json: { patient_id: pid, lang: l } })); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid g2">
      <Card title="My results in plain language" actions={<div className="row"><button className={`small ${lang === "en" ? "" : "ghost"}`} style={lang === "en" ? {} : { color: "var(--brand)" }} onClick={() => load("en")}>English</button><button className={`small ${lang === "hi" ? "" : "ghost"}`} style={lang === "hi" ? {} : { color: "var(--brand)" }} onClick={() => load("hi")}>हिन्दी</button></div>}>
        <ErrorBox error={err} />
        {!data && <div className="row"><Empty>Explain my latest results.</Empty><AsyncButton className="small" onClick={() => load(lang)}>Explain</AsyncButton></div>}
        {data && (
          <div className="stack">
            {data.results.urgent && <div className="error">{lang === "hi" ? "एक या अधिक परिणाम तत्काल ध्यान देने योग्य हैं।" : "One or more results need urgent attention. Your care team has been notified."}</div>}
            {data.results.lines.map((l: any) => <div key={l.analyte} className="row"><Badge tone={flagTone(l.flag)}>{l.flag}</Badge><span>{l.text}</span></div>)}
            <h3>{lang === "hi" ? "मेरी स्थितियां" : "My conditions"}</h3>
            {data.conditions.map((c: any, i: number) => <div key={i}>{c.text}</div>)}
            <small className="muted">{data.results.disclaimer}</small>
          </div>
        )}
      </Card>
      <Card title="Signed reports">
        <ErrorBox error={reports.error} />
        {(reports.data || []).filter((r) => r.status === "signed").map((r) => (
          <div key={r.id} className="row between alert-item"><span><b>{r.title}</b> <small className="muted">{fmtTime(r.signed_at)} · {r.signed_by}</small></span><a href={`/api/proxy/diag/reports/${r.id}/pdf`} target="_blank" rel="noreferrer">Download PDF</a></div>
        ))}
        {!reports.data?.some((r) => r.status === "signed") && <Empty>No signed reports yet.</Empty>}
        <p className="muted">Each PDF has a QR code that anyone can scan to verify it was issued by the lab and has not been altered.</p>
      </Card>
    </div>
  );
}

function Meds({ pid }: { pid: string }) {
  const rx = useApi<any[]>(`clinical/prescriptions?patient_id=${pid}`, [pid]);
  const outbox = useApi<any[]>("clinical/outbox");
  return (
    <div className="grid g2">
      <Card title="My medicines">
        <ErrorBox error={rx.error} />
        {(rx.data || []).map((m) => (
          <div key={m.id} className="alert-item">
            <div className="row between"><b>{m.drug} {m.strength_mg} mg</b><Badge tone={m.status === "active" ? "ok" : "bad"}>{m.status === "on-hold" ? "paused by your doctor" : m.status}</Badge></div>
            <small>{m.frequency} for {m.duration_days} days · prescribed {fmtDate(m.created_at)}</small>
            {m.hold_reason && <p className="muted">Please do not take this until your doctor reviews it.</p>}
          </div>
        ))}
      </Card>
      <Card title="Reminders sent to me">
        {(outbox.data || []).map((o) => <div key={o.id} className="alert-item"><small>{fmtTime(o.created_at)} · {o.channel}</small><p>{o.message}</p></div>)}
        {!outbox.data?.length && <Empty>No reminders.</Empty>}
      </Card>
    </div>
  );
}

function Appointments({ pid }: { pid: string }) {
  const slots = useApi<any[]>("clinical/availability");
  const appts = useApi<any[]>("clinical/appointments");
  const [mode, setMode] = useState("in-person");
  const [reason, setReason] = useState("Follow-up");
  const [err, setErr] = useState<string | null>(null);
  async function book(id: number) {
    setErr(null);
    try { await api("clinical/appointments", { json: { slot_id: id, patient_id: pid, mode, reason } }); appts.reload(); slots.reload(); } catch (e) { setErr((e as Error).message); }
  }
  async function cancel(id: number) {
    try { await api(`clinical/appointments/${id}`, { method: "DELETE" }); appts.reload(); slots.reload(); } catch (e) { setErr((e as Error).message); }
  }
  const byDoc: Record<string, any[]> = {};
  for (const s of slots.data || []) (byDoc[s.name] ||= []).push(s);
  return (
    <div className="grid g2">
      <Card title="Book an appointment" actions={<div className="row"><select value={mode} onChange={(e) => setMode(e.target.value)}><option value="in-person">In person</option><option value="video">Video</option></select><input value={reason} onChange={(e) => setReason(e.target.value)} /></div>}>
        <ErrorBox error={err || slots.error} />
        {Object.entries(byDoc).map(([doc, xs]) => (
          <div key={doc} style={{ marginBottom: 10 }}>
            <b>{doc}</b> <small className="muted">{xs[0].specialty}</small>
            <div className="row" style={{ marginTop: 4 }}>{xs.slice(0, 10).map((s) => <button key={s.id} className="small ghost" style={{ color: "var(--brand)" }} onClick={() => book(s.id)}>{fmtTime(s.starts_at)}</button>)}</div>
          </div>
        ))}
      </Card>
      <Card title="My appointments">
        {(appts.data || []).map((a) => (
          <div key={a.id} className="alert-item row between">
            <span><b>{fmtTime(a.starts_at)}</b> with {a.practitioner_name}<br /><small>{a.mode} · {a.reason} · {a.status}</small></span>
            <span className="row">{a.video_url && a.status === "booked" && <a href={a.video_url} target="_blank" rel="noreferrer">Join video</a>}{a.status === "booked" && <button className="small danger" onClick={() => cancel(a.id)}>Cancel</button>}</span>
          </div>
        ))}
        {!appts.data?.length && <Empty>No appointments.</Empty>}
      </Card>
    </div>
  );
}

function Locker() {
  const locker = useApi<any>("clinical/locker");
  const hips = useApi<any[]>("clinical/abdm/registry/facilities");
  const [abha, setAbha] = useState("91-1111-2222-3333");
  const [ver, setVer] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  async function verify() {
    setErr(null);
    try { setVer(await api("clinical/abdm/abha/verify", { json: { abha_number: abha } })); } catch (e) { setErr((e as Error).message); }
  }
  async function link(hip: string) {
    setErr(null);
    try { await api("clinical/locker/link", { json: { hip_id: hip } }); locker.reload(); } catch (e) { setErr((e as Error).message); }
  }
  const entries = locker.data?.bundle?.entry || [];
  const counts: Record<string, number> = {};
  for (const e of entries) counts[e.resource.resourceType] = (counts[e.resource.resourceType] || 0) + 1;
  return (
    <div className="grid g2">
      <Card title="ABHA (Ayushman Bharat Health Account)">
        <div className="row"><input value={abha} onChange={(e) => setAbha(e.target.value)} /><AsyncButton className="small" onClick={verify}>Verify (M1)</AsyncButton></div>
        {ver && <div className={ver.verified ? "okbox" : "error"}>{ver.verified ? `Verified: ${ver.name || ""} ${ver.abha_address || ""}` : ver.reason || "Not verified"}</div>}
        <ErrorBox error={err || locker.error} />
        <h3>Link records from a facility (M2)</h3>
        {(hips.data || []).map((h) => <div key={h.id} className="row between"><span>{h.name} <small className="muted">{h.id}</small></span><button className="small" onClick={() => link(h.id)}>Link</button></div>)}
        <h3>Linked care contexts</h3>
        {(locker.data?.links || []).map((l: any) => <div key={l.id}>{l.hip_name || l.hip_id} · {l.care_context} <small className="muted">{fmtDate(l.linked_at)}</small></div>)}
        {!locker.data?.links?.length && <Empty>Nothing linked yet.</Empty>}
      </Card>
      <Card title="My health locker (FHIR R4)">
        <div className="row">{Object.entries(counts).map(([k, v]) => <Badge key={k} tone="info">{v} {k}</Badge>)}</div>
        <p className="muted">Sandbox ABDM gateway. In production this bundle is exchanged with consented HIUs through the national gateway.</p>
      </Card>
    </div>
  );
}

function Consents() {
  const c = useApi<any[]>("clinical/consents");
  const fac = useApi<any[]>("clinical/facilities");
  const [grantee, setGrantee] = useState("F2");
  const [days, setDays] = useState(30);
  const [err, setErr] = useState<string | null>(null);
  async function grant() {
    setErr(null);
    try { await api("clinical/consents", { json: { grantee_facility: grantee, purpose: "CAREMGT", days, hi_types: ["OPConsultation", "DiagnosticReport", "Prescription"] } }); c.reload(); } catch (e) { setErr((e as Error).message); }
  }
  async function revoke(id: number) {
    try { await api(`clinical/consents/${id}/revoke`, { method: "POST" }); c.reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid g2">
      <Card title="Share my records (M3 consent)">
        <div className="row">
          <label>Facility<select value={grantee} onChange={(e) => setGrantee(e.target.value)}>{(fac.data || []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
          <label>Days<input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: 80 }} /></label>
          <AsyncButton onClick={grant}>Grant</AsyncButton>
        </div>
        <ErrorBox error={err || c.error} />
        <p className="muted">Without consent, doctors at other facilities cannot open your record. You can revoke at any time; every access is logged.</p>
      </Card>
      <Card title="My consents">
        {(c.data || []).map((x) => (
          <div key={x.id} className="alert-item row between">
            <span><b>{x.facility_name}</b><br /><small>{x.purpose} · {x.artefact?.hiTypes?.join(", ")} · until {fmtDate(x.expires_at)}</small></span>
            <span className="row"><Badge tone={x.status === "granted" ? "ok" : "neutral"}>{x.status}</Badge>{x.status === "granted" && <button className="small danger" onClick={() => revoke(x.id)}>Revoke</button>}</span>
          </div>
        ))}
      </Card>
    </div>
  );
}
