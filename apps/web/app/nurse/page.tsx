"use client";
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import Scanner from "@/components/Scanner";
import { AsyncButton, Badge, Card, Empty, ErrorBox, Tabs } from "@/components/ui";
import { api, fmtTime, useApi } from "@/lib/api";

const DEMO_BARCODES = [
  ["Lisinopril 10 (generic)", "8901000000012"], ["Spironolactone 25 (generic)", "8901000000022"], ["Metformin 500 (generic)", "8901000000032"],
  ["Monocef 1g", "8901000000041"], ["Azithral 500", "8901000000052"], ["Lasix 40", "8901000000071"], ["Warfarin 5", "8901000000081"],
];

export default function NursePortal() {
  return <Shell title="Ward station" roles={["nurse"]}><Nurse /></Shell>;
}

function Nurse() {
  const [tab, setTab] = useState("ward");
  const grid = useApi<any>("clinical/ward/grid");
  const tasks = useApi<any[]>("clinical/tasks");
  useEffect(() => {
    const h = () => { grid.reload(); tasks.reload(); };
    window.addEventListener("omnimed:alert", h);
    const t = setInterval(h, 30_000);
    return () => { window.removeEventListener("omnimed:alert", h); clearInterval(t); };
  }, [grid, tasks]);
  const openTasks = (tasks.data || []).filter((t) => t.status === "open");
  return (
    <>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "ward", label: "Ward grid" }, { id: "tasks", label: <>Tasks <Badge tone={openTasks.length ? "bad" : "neutral"}>{openTasks.length}</Badge></> }, { id: "emar", label: "eMAR" }]} />
      {tab === "ward" && <WardGrid data={grid.data} error={grid.error} reload={grid.reload} />}
      {tab === "tasks" && <Tasks tasks={tasks.data || []} error={tasks.error} reload={tasks.reload} />}
      {tab === "emar" && <Emar />}
    </>
  );
}

function WardGrid({ data, error, reload }: { data: any; error: string | null; reload: () => void }) {
  const [sel, setSel] = useState<any>(null);
  return (
    <div className="grid side" style={{ gridTemplateColumns: "minmax(0,1fr) 360px" }}>
      <Card title="Early-warning grid (MEWS / qSOFA)">
        <ErrorBox error={error} />
        <div className="vitals-grid">
          {(data?.patients || []).map((row: any) => {
            const v = row.latest || {};
            const tr = row.trend || {};
            return (
              <div key={row.patient.id} className={`bed ${v.red_alert ? "red" : ""}`} onClick={() => setSel(row.patient)} style={{ cursor: "pointer" }}>
                <div className="row between"><b>Bed {row.patient.bed || "-"}</b>{v.red_alert && <Badge tone="bad">RED ALERT</Badge>}</div>
                <div>{row.patient.name} <small className="muted">{row.patient.mrn}</small></div>
                <div className="row between">
                  <span className="score" style={{ color: v.mews >= 5 ? "var(--bad)" : v.mews >= 3 ? "var(--warn)" : "var(--ok)" }}>MEWS {v.mews ?? "-"}</span>
                  <span>qSOFA {v.qsofa ?? "-"}</span>
                </div>
                <small>BP {v.sbp}/{v.dbp} · HR {v.hr}{tr.hr_rising ? " ↑" : ""} · RR {v.rr} · SpO2 {v.spo2}%{tr.spo2_falling ? " ↓" : ""} · T {v.temp}</small>
                <div className="row" style={{ marginTop: 4 }}>
                  {row.open_tasks > 0 && <Badge tone="warn">{row.open_tasks} task(s)</Badge>}
                  {row.open_alerts > 0 && <Badge tone="bad">{row.open_alerts} alert(s)</Badge>}
                  <small className="muted">{fmtTime(v.recorded_at)}</small>
                </div>
              </div>
            );
          })}
        </div>
      </Card>
      <VitalsForm patient={sel} onSaved={reload} />
    </div>
  );
}

function VitalsForm({ patient, onSaved }: { patient: any; onSaved: () => void }) {
  const [v, setV] = useState<Record<string, string>>({ sbp: "", dbp: "", hr: "", rr: "", temp: "", spo2: "", gcs: "15" });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!patient) return <Card title="Record vitals"><Empty>Select a bed to record observations.</Empty></Card>;
  async function save() {
    setErr(null);
    const body: Record<string, number> = {};
    for (const [k, x] of Object.entries(v)) if (x !== "") body[k] = Number(x);
    try { setRes(await api(`clinical/patients/${patient.id}/vitals`, { json: body })); onSaved(); } catch (e) { setErr((e as Error).message); }
  }
  const field = (k: string, label: string) => <label key={k}>{label}<input inputMode="decimal" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} style={{ width: 90 }} /></label>;
  return (
    <Card title={`Record vitals · ${patient.name}`}>
      <div className="row">{field("sbp", "Systolic")}{field("dbp", "Diastolic")}{field("hr", "Heart rate")}{field("rr", "Resp rate")}{field("temp", "Temp °C")}{field("spo2", "SpO2 %")}{field("gcs", "GCS")}</div>
      <div style={{ marginTop: 10 }}><AsyncButton onClick={save}>Save and score</AsyncButton></div>
      <ErrorBox error={err} />
      {res && (
        <div className={res.score.red_alert ? "error" : "okbox"}>
          MEWS {res.score.mews} · qSOFA {res.score.qsofa}. {res.score.red_alert ? "Red alert raised: the on-call doctor has been paged and escalation is running." : "No escalation needed."}
        </div>
      )}
    </Card>
  );
}

function Tasks({ tasks, error, reload }: { tasks: any[]; error: string | null; reload: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  async function done(id: number) {
    try { await api(`clinical/tasks/${id}/complete`, { method: "POST" }); reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <Card title="Ward tasks">
      <ErrorBox error={error || err} />
      {!tasks.length && <Empty>No tasks.</Empty>}
      <table>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td><Badge tone={t.kind === "stat_ecg" ? "bad" : "info"}>{t.kind}</Badge></td>
              <td><b>{t.patient_id}</b> · {t.detail}<br /><small className="muted">{fmtTime(t.created_at)} · ward {t.ward}</small></td>
              <td>{t.status === "open" ? <button className="small" onClick={() => done(t.id)}>Mark done</button> : <Badge tone="ok">done by {t.done_by}</Badge>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Emar() {
  const due = useApi<any[]>("clinical/emar");
  const [wrist, setWrist] = useState("");
  const [med, setMed] = useState("");
  const [override, setOverride] = useState("");
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  const setWristCb = useCallback((x: string) => setWrist(x), []);
  const setMedCb = useCallback((x: string) => setMed(x), []);
  async function give() {
    setRes(null);
    try {
      const r = await api<any>("clinical/emar/administer", { json: { wristband: wrist, med_barcode: med, override_reason: override } });
      setRes({ ok: true, text: `Recorded: ${r.drug} ${r.strength_mg} mg for ${r.patient_id} (dose due ${fmtTime(r.scheduled_at)})${r.override ? " with documented override" : ""}.` });
      setMed(""); setOverride(""); due.reload();
    } catch (e) { setRes({ ok: false, text: (e as Error).message }); }
  }
  return (
    <div className="grid g2">
      <Card title="Two-barcode administration check">
        <div className="stack">
          <Scanner label="1. Patient wristband (MRN)" value={wrist} onChange={setWristCb} />
          <Scanner label="2. Medication barcode" value={med} onChange={setMedCb} />
          <label>Override reason (only if outside the ±60 min window)<input value={override} onChange={(e) => setOverride(e.target.value)} /></label>
          <AsyncButton onClick={give} disabled={!wrist || !med}>Verify and record</AsyncButton>
          {res && <div className={res.ok ? "okbox" : "error"}>{res.text}</div>}
          <details><summary className="muted">Demo medication barcodes</summary>
            {DEMO_BARCODES.map(([n, c]) => <div key={c}><button className="small ghost" style={{ color: "var(--brand)" }} onClick={() => setMed(c)}>{c}</button> {n}</div>)}
          </details>
        </div>
      </Card>
      <Card title="Due medications" actions={<button className="small ghost" style={{ color: "var(--brand)" }} onClick={due.reload}>Refresh</button>}>
        <ErrorBox error={due.error} />
        <table>
          <tbody>
            {(due.data || []).map((d, i) => (
              <tr key={i} className="click" onClick={() => setWrist(d.mrn)}>
                <td>{fmtTime(d.scheduled_at)}</td>
                <td><b>{d.patient_name}</b> <small className="muted">{d.mrn} · bed {d.bed}</small><br />{d.drug} {d.strength_mg} mg {d.route}{d.hold_reason && <div className="error">ON HOLD: {d.hold_reason}</div>}</td>
                <td><Badge tone={d.status === "given" ? "ok" : d.status === "late" ? "warn" : d.status === "held" ? "bad" : "neutral"}>{d.status}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
