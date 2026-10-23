"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import DicomViewer from "@/components/DicomViewer";
import StockPanel from "@/components/StockPanel";
import { AsyncButton, Badge, Card, Empty, ErrorBox, Json, Tabs, flagTone } from "@/components/ui";
import { api, ApiError, fmtDate, fmtTime, useApi } from "@/lib/api";

const SAMPLE = `Doctor: Good morning, what brings you in today?
Patient: I have had a cough with phlegm and fever for 4 days and I am short of breath. No chest pain.
Doctor: Your temperature is 38.7, pulse 108, respiratory rate 24 and saturation 91%. On examination there are crackles at the left base.
Doctor: This looks like pneumonia. I will start amoxicillin 500 mg three times a day for 5 days. Let's check a CBC and CRP and get a chest x-ray. Follow up in 3 days.`;

export default function DoctorPortal() {
  return <Shell title="Doctor workspace" roles={["doctor"]}><Doctor /></Shell>;
}

function Doctor() {
  const patients = useApi<any[]>("clinical/patients");
  const [pid, setPid] = useState<string>("");
  const [tab, setTab] = useState("summary");
  const [prefill, setPrefill] = useState<any>(null);
  useEffect(() => { if (!pid && patients.data?.[0]) setPid(patients.data[0].id); }, [patients.data, pid]);

  return (
    <div className="grid side">
      <Card title="My patients">
        <ErrorBox error={patients.error} />
        <table>
          <tbody>
            {(patients.data || []).map((p) => (
              <tr key={p.id} className={`click ${p.id === pid ? "sel" : ""}`} onClick={() => setPid(p.id)}>
                <td><b>{p.name}</b><br /><small className="muted">{p.mrn} · {p.age}{p.gender?.[0]?.toUpperCase()} {p.ward ? `· ${p.ward}-${p.bed}` : "· OPD"}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
        <PatientLookup onPick={setPid} />
      </Card>
      <div>
        {pid && (
          <>
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: "summary", label: "Summary" }, { id: "scribe", label: "Ambient scribe" }, { id: "rx", label: "Prescribe" },
              { id: "ddx", label: "Differential" }, { id: "imaging", label: "Imaging" }, { id: "timeline", label: "Timeline" },
              { id: "fhir", label: "FHIR" }, { id: "schedule", label: "Schedule" },
            ]} />
            {tab === "summary" && <Summary pid={pid} />}
            {tab === "scribe" && <Scribe pid={pid} onPrescribe={(m) => { setPrefill(m); setTab("rx"); }} />}
            {tab === "rx" && <Prescribe pid={pid} prefill={prefill} />}
            {tab === "ddx" && <Differential pid={pid} />}
            {tab === "imaging" && <Card title="Imaging (DICOMweb via Orthanc)"><DicomViewer patientId={pid} /></Card>}
            {tab === "timeline" && <Timeline pid={pid} />}
            {tab === "fhir" && <Fhir pid={pid} />}
            {tab === "schedule" && <Schedule />}
          </>
        )}
      </div>
    </div>
  );
}

function PatientLookup({ onPick }: { onPick: (id: string) => void }) {
  const [id, setId] = useState("");
  return (
    <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if (id) onPick(id.trim()); }}>
      <input placeholder="Open patient ID (e.g. P0101)" value={id} onChange={(e) => setId(e.target.value)} style={{ flex: 1 }} />
      <button className="small">Open</button>
    </form>
  );
}

function Summary({ pid }: { pid: string }) {
  const p = useApi<any>(`clinical/patients/${pid}`, [pid]);
  const meds = useApi<any[]>(`clinical/prescriptions?patient_id=${pid}`, [pid]);
  useEffect(() => {
    const h = (e: Event) => { if ((e as CustomEvent).detail?.patient_id === pid) { p.reload(); meds.reload(); } };
    window.addEventListener("omnimed:alert", h);
    return () => window.removeEventListener("omnimed:alert", h);
  }, [pid, p, meds]);
  if (p.error) return <ErrorBox error={p.error} />;
  if (!p.data) return <p className="muted">Loading...</p>;
  const d = p.data;
  const pt = d.patient;
  const lv = d.latest_vitals;
  return (
    <div className="grid g2">
      <Card title={`${pt.name} · ${pt.mrn}`} actions={<Badge tone="info">ABHA {pt.abha_number || "not linked"}</Badge>}>
        <dl className="kv">
          <dt>Age / sex</dt><dd>{pt.age} / {pt.gender}</dd>
          <dt>Location</dt><dd>{pt.ward ? `${pt.ward} bed ${pt.bed}` : "Outpatient"} · {pt.facility_id}</dd>
          <dt>Attending</dt><dd>{pt.attending}</dd>
          <dt>Phone</dt><dd>{pt.phone}</dd>
        </dl>
        <h3>Allergies</h3>
        {d.allergies.length ? d.allergies.map((a: any) => <div key={a.substance} className="error"><b>{a.substance}</b> ({a.severity}) · {a.reaction}</div>) : <Empty>No known allergies.</Empty>}
        <h3>Active problems</h3>
        {d.conditions.map((c: any) => <div key={c.icd10}>{c.display} <small className="muted">ICD-10 {c.icd10} · SNOMED {c.snomed} · since {fmtDate(c.onset)}</small></div>)}
      </Card>
      <Card title="Latest results">
        <table>
          <thead><tr><th>Test</th><th>Value</th><th>Flag</th><th>When</th></tr></thead>
          <tbody>
            {Object.values(d.latest_labs || {}).filter((l: any) => l.source === "lab").map((l: any) => (
              <tr key={l.loinc}><td>{l.display}</td><td>{l.value} {l.unit}</td><td><Badge tone={flagTone(l.flag)}>{l.flag || "N"}</Badge></td><td><small>{fmtTime(l.effective_at)}</small></td></tr>
            ))}
          </tbody>
        </table>
        {lv && <><h3>Latest vitals <Badge tone={lv.red_alert ? "bad" : "ok"}>MEWS {lv.mews} · qSOFA {lv.qsofa}</Badge></h3>
          <p>BP {lv.sbp}/{lv.dbp} · HR {lv.hr} · RR {lv.rr} · T {lv.temp} · SpO2 {lv.spo2}% · GCS {lv.gcs} <small className="muted">{fmtTime(lv.recorded_at)}</small></p></>}
      </Card>
      <Card title="Medications" className="span2" actions={<button className="small ghost" style={{ color: "var(--brand)" }} onClick={meds.reload}>Refresh</button>}>
        <ErrorBox error={meds.error} />
        <MedTable meds={meds.data || []} reload={meds.reload} />
      </Card>
      <Card title="Care team">
        {d.care_team.length ? d.care_team.map((c: any) => <div key={c.username}>{c.name} <small className="muted">{c.role}</small></div>) : <Empty>Attending only.</Empty>}
      </Card>
    </div>
  );
}

function MedTable({ meds, reload }: { meds: any[]; reload: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  async function act(id: number, action: "hold" | "release") {
    const reason = window.prompt(action === "hold" ? "Reason for hold" : "Reason for release (documented in audit)");
    if (!reason) return;
    try { await api(`clinical/prescriptions/${id}/${action}`, { json: { reason } }); reload(); } catch (e) { setErr((e as Error).message); }
  }
  if (!meds.length) return <Empty>No medications.</Empty>;
  return (
    <>
      <ErrorBox error={err} />
      <table>
        <thead><tr><th>Drug</th><th>Sig</th><th>Status</th><th>Prescribed</th><th /></tr></thead>
        <tbody>
          {meds.map((m) => (
            <tr key={m.id}>
              <td><b>{m.drug}</b> {m.strength_mg} mg<br /><small className="muted">RxNorm {m.rxcui}</small></td>
              <td>{m.dose_units} x {m.route} {m.frequency} · {m.duration_days} d</td>
              <td><Badge tone={m.status === "active" ? "ok" : m.status === "on-hold" ? "bad" : "neutral"}>{m.status}</Badge>{m.hold_reason && <><br /><small>{m.hold_reason}</small></>}</td>
              <td><small>{m.prescriber} · {fmtDate(m.created_at)}</small></td>
              <td>{m.status === "active" && <button className="small warn" onClick={() => act(m.id, "hold")}>Hold</button>}
                {m.status === "on-hold" && <button className="small" onClick={() => act(m.id, "release")}>Release</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Scribe({ pid, onPrescribe }: { pid: string; onPrescribe: (m: any) => void }) {
  const [text, setText] = useState(SAMPLE);
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [rec, setRec] = useState<MediaRecorder | null>(null);

  async function run() {
    setErr(null); setSaved(null);
    try { setOut(await api("ai/scribe", { json: { transcript: text, patient_id: pid } })); } catch (e) { setErr((e as Error).message); }
  }
  async function save() {
    try {
      const r = await api<any>(`clinical/patients/${pid}/encounters`, { json: { reason: out.soap.subjective.chief_complaint || "Consultation", note: out.soap } });
      setSaved(`Encounter ${r.encounter_id} saved to the record.`);
    } catch (e) { setErr((e as Error).message); }
  }
  async function record() {
    if (rec) { rec.stop(); setRec(null); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const fd = new FormData();
        fd.append("file", new Blob(chunks, { type: "audio/webm" }), "consult.webm");
        try {
          const r = await api<any>(`ai/scribe/audio?patient_id=${pid}`, { body: fd });
          setText(r.transcript); setOut(r);
        } catch (e) { setErr((e as Error).message); }
      };
      mr.start();
      setRec(mr);
    } catch (e) { setErr((e as Error).message); }
  }

  const g = out?.soap?.grounding;
  return (
    <div className="grid g2">
      <Card title="Consultation transcript" actions={<><button className="small ghost" style={{ color: "var(--brand)" }} onClick={record}>{rec ? "Stop recording" : "Record audio"}</button><AsyncButton className="small" onClick={run}>Generate note</AsyncButton></>}>
        <textarea rows={16} value={text} onChange={(e) => setText(e.target.value)} />
        <p className="muted">Speaker-labelled lines (Doctor:/Patient:) improve attribution. Audio needs a Whisper-compatible server (WHISPER_URL).</p>
        <ErrorBox error={err} />
      </Card>
      <Card title="Structured SOAP note" actions={out && <><Badge tone={g?.accepted ? "ok" : "bad"}>{g?.accepted ? "grounded" : "rejected"}</Badge><Badge tone="info">{out.generator}</Badge></>}>
        {!out && <Empty>Generate a note to see the SOAP draft. Every diagnosis, drug and test is mapped to SNOMED-CT, ICD-10, RxNorm or LOINC before it is shown.</Empty>}
        {out && (
          <div className="stack">
            {(["S", "O", "A", "P"] as const).map((k) => <div key={k}><b>{k}</b> <span>{out.soap.narrative[k]}</span></div>)}
            <div className="row">
              {out.soap.codes.icd10.map((c: string) => <Badge key={c} tone="info">ICD-10 {c}</Badge>)}
              {out.soap.codes.rxnorm.map((c: string) => <Badge key={c} tone="info">RxNorm {c}</Badge>)}
              {out.soap.codes.loinc.map((c: string) => <Badge key={c} tone="info">LOINC {c}</Badge>)}
            </div>
            {out.soap.plan.medications.filter((m: any) => m.action === "start").map((m: any) => (
              <div key={m.rxcui} className="row between okbox">
                <span>{m.drug} {m.strength_mg} mg {m.frequency} {m.duration_days ? `x ${m.duration_days} d` : ""}</span>
                <button className="small" onClick={() => onPrescribe(m)}>Prescribe with safety check</button>
              </div>
            ))}
            {out.soap.incomplete?.length > 0 && <div className="warnbox">Needs completion: {out.soap.incomplete.map((i: any) => `${i.item} (${i.reason})`).join("; ")}</div>}
            {out.rejected_draft && <div className="error">The {out.rejected_draft.source} draft was rejected by the grounding engine ({out.rejected_draft.rejected.map((r: any) => r.text).join(", ")}). Showing the rule-based note instead.</div>}
            <p className="muted">Grounding: {g.checked} checks, {g.rejected.length} rejected · {out.elapsed_ms} ms</p>
            <AsyncButton onClick={save} disabled={!g?.accepted}>Sign and save to record</AsyncButton>
            {saved && <div className="okbox">{saved}</div>}
          </div>
        )}
      </Card>
    </div>
  );
}

function Prescribe({ pid, prefill }: { pid: string; prefill: any }) {
  const drugs = useApi<any[]>("clinical/drugs");
  const [f, setF] = useState({ rxcui: "", strength_mg: 500, dose_units: 1, route: "oral", frequency: "OD", duration_days: 5 });
  const [check, setCheck] = useState<any>(null);
  const [override, setOverride] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    if (prefill) setF((x) => ({ ...x, rxcui: prefill.rxcui, strength_mg: prefill.strength_mg || x.strength_mg, frequency: prefill.frequency || x.frequency, duration_days: prefill.duration_days || x.duration_days }));
  }, [prefill]);
  const body = { ...f, patient_id: pid, strength_mg: Number(f.strength_mg), dose_units: Number(f.dose_units), duration_days: Number(f.duration_days) };

  async function runCheck() {
    setErr(null); setDone(null);
    try { setCheck(await api("clinical/prescriptions/check", { json: body })); } catch (e) { setErr((e as Error).message); }
  }
  async function submit() {
    setErr(null);
    try {
      const r = await api<any>("clinical/prescriptions", { json: { ...body, override_reason: override } });
      setDone(`Prescription #${r.id} created and sent to pharmacy.`); setCheck(null); setOverride("");
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.message);
      if (ae.body?.alerts) setCheck((c: any) => ({ ...(c || {}), alerts: ae.body.alerts, blocking: true }));
    }
  }
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="grid g2">
      <Card title="New prescription">
        <div className="stack">
          <label>Drug (RxNorm)
            <select value={f.rxcui} onChange={set("rxcui")}>
              <option value="">Select...</option>
              {(drugs.data || []).map((d) => <option key={d.rxcui} value={d.rxcui}>{d.ingredient} ({d.classes.join(", ")})</option>)}
            </select>
          </label>
          <div className="row">
            <label>Strength (mg)<input type="number" value={f.strength_mg} onChange={set("strength_mg")} style={{ width: 110 }} /></label>
            <label>Units/dose<input type="number" value={f.dose_units} onChange={set("dose_units")} style={{ width: 80 }} /></label>
            <label>Route<select value={f.route} onChange={set("route")}>{["oral", "iv", "im", "sc", "inhaled", "topical"].map((r) => <option key={r}>{r}</option>)}</select></label>
            <label>Frequency<select value={f.frequency} onChange={set("frequency")}>{["OD", "BD", "TDS", "QID", "HS", "PRN", "STAT"].map((r) => <option key={r}>{r}</option>)}</select></label>
            <label>Days<input type="number" value={f.duration_days} onChange={set("duration_days")} style={{ width: 70 }} /></label>
          </div>
          <div className="row">
            <AsyncButton className="ghost" onClick={runCheck} disabled={!f.rxcui}>Run safety check</AsyncButton>
            <AsyncButton onClick={submit} disabled={!f.rxcui}>Prescribe</AsyncButton>
          </div>
          {check?.blocking && <label>Override reason (required for hard alerts; audited)<input value={override} onChange={(e) => setOverride(e.target.value)} /></label>}
          <ErrorBox error={err} />
          {done && <div className="okbox">{done}</div>}
        </div>
      </Card>
      <Card title="Safety and availability">
        {!check && <Empty>Run the safety check to see allergy, interaction, renal, potassium, dose and duplicate alerts, plus stock across pharmacies within 5 miles.</Empty>}
        {check && (
          <div className="stack">
            {check.alerts?.length === 0 && <div className="okbox">No safety alerts.</div>}
            {check.alerts?.map((a: any, i: number) => <div key={i} className={a.severity === "hard" ? "error" : "warnbox"}><b>{a.severity.toUpperCase()} · {a.kind}</b> {a.message}</div>)}
            {check.stock && <StockPanel s={check.stock} />}
          </div>
        )}
      </Card>
    </div>
  );
}

function Differential({ pid }: { pid: string }) {
  const [text, setText] = useState("palpitations and generalised weakness for 2 days, no chest pain");
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  async function run() {
    setErr(null);
    try { setOut(await api("ai/differential", { json: { patient_id: pid, symptoms_text: text } })); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid g2">
      <Card title="Presenting features" actions={<AsyncButton className="small" onClick={run}>Rank differential</AsyncButton>}>
        <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} />
        <p className="muted">Combined with the record's latest vitals, labs and active problems.</p>
        <ErrorBox error={err} />
        {out && <><h3>Inputs used</h3><Json value={out.inputs} /></>}
      </Card>
      <Card title="Differential diagnosis" actions={out && <Badge tone="info">{out.engine}</Badge>}>
        {!out && <Empty>Rank to see a transparent, evidence-weighted differential with linked guidelines.</Empty>}
        {out?.differential.map((d: any) => (
          <div key={d.icd10} style={{ marginBottom: 12 }}>
            <div className="row between"><b>{d.display}</b><small>ICD-10 {d.icd10}</small></div>
            <div className="bar"><i style={{ width: `${Math.round(d.score * 100)}%` }} /></div>
            <small>For: {d.evidence.join(", ")}{d.against.length ? ` · Against: ${d.against.join(", ")}` : ""}</small>
            {d.guidelines.map((g: any) => <div key={g.url}><a href={g.url} target="_blank" rel="noreferrer">{g.source}: {g.title}</a></div>)}
          </div>
        ))}
        {out && <p className="muted">{out.disclaimer}</p>}
      </Card>
    </div>
  );
}

function Timeline({ pid }: { pid: string }) {
  const t = useApi<any>(`clinical/patients/${pid}/timeline`, [pid]);
  if (t.error) return <ErrorBox error={t.error} />;
  return (
    <Card title="Longitudinal timeline (all consented facilities)">
      <table>
        <tbody>
          {(t.data?.events || []).map((e: any, i: number) => (
            <tr key={i}>
              <td style={{ width: 170 }}><small>{fmtTime(e.at)}</small></td>
              <td><Badge tone={e.kind === "alert" ? "bad" : e.kind === "lab" ? flagTone(e.flag) : "info"}>{e.kind}</Badge></td>
              <td><b>{e.title}</b> {e.detail}{e.facility && <small className="muted"> · {e.facility}</small>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Fhir({ pid }: { pid: string }) {
  const f = useApi<any>(`clinical/patients/${pid}/fhir`, [pid]);
  return (
    <Card title="FHIR R4 Bundle" actions={f.data && <Badge tone="info">{f.data.entry?.length} resources</Badge>}>
      <ErrorBox error={f.error} />
      {f.data && <Json value={f.data} />}
    </Card>
  );
}

function Schedule() {
  return <div className="grid g2"><Appointments /><RepVisits /></div>;
}

function RepVisits() {
  const prof = useApi<any>("life/doctor/profile");
  const slots = useApi<any[]>("life/slots");
  const [err, setErr] = useState<string | null>(null);
  async function consent(v: boolean) {
    try { await api("life/doctor/consent", { json: { consent: v } }); prof.reload(); } catch (e) { setErr((e as Error).message); }
  }
  async function decide(id: number, approve: boolean) {
    try { await api(`life/slots/${id}/decide`, { json: { approve } }); slots.reload(); } catch (e) { setErr((e as Error).message); }
  }
  const pending = (slots.data || []).filter((s) => s.status !== "open");
  return (
    <Card title="Medical representative visits" actions={prof.data && (
      <label className="row" style={{ flexDirection: "row" }}><input type="checkbox" checked={!!prof.data.detailing_consent} onChange={(e) => consent(e.target.checked)} /> Accept detailing visits</label>
    )}>
      <ErrorBox error={err || slots.error} />
      {!pending.length && <Empty>No visit requests.</Empty>}
      {pending.map((s) => (
        <div key={s.id} className="alert-item row between">
          <span><b>{fmtTime(s.starts_at)}</b> · {s.rep} · {s.topic}</span>
          {s.status === "requested" ? <span className="row"><button className="small" onClick={() => decide(s.id, true)}>Approve</button><button className="small ghost" style={{ color: "var(--brand)" }} onClick={() => decide(s.id, false)}>Decline</button></span> : <Badge tone="ok">{s.status}</Badge>}
        </div>
      ))}
    </Card>
  );
}

function Appointments() {
  const a = useApi<any[]>("clinical/appointments");
  return (
    <Card title="Upcoming appointments">
      <ErrorBox error={a.error} />
      {!a.data?.length && <Empty>No booked appointments.</Empty>}
      <table>
        <tbody>
          {(a.data || []).map((x) => (
            <tr key={x.id}><td>{fmtTime(x.starts_at)}</td><td>{x.patient_id}</td><td><Badge>{x.mode}</Badge></td><td>{x.reason}</td><td>{x.video_url && <a href={x.video_url} target="_blank" rel="noreferrer">Join video</a>}</td><td><Badge tone={x.status === "booked" ? "ok" : "neutral"}>{x.status}</Badge></td></tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
