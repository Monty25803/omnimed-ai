"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import DicomViewer from "@/components/DicomViewer";
import { AsyncButton, Badge, Card, Empty, ErrorBox, Tabs, flagTone } from "@/components/ui";
import { api, fmtTime, useApi } from "@/lib/api";

const ts = () => new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);

function hl7Sample(mrn: string, k: string) {
  const t = ts();
  return [`MSH|^~\\&|OMNI-ANALYZER|OMNILAB|OMNIMED|F1|${t}||ORU^R01|HL7-${t}|P|2.5.1`, `PID|1||${mrn}^^^OMNIMED^MR`, "OBR|1|||80048^Basic metabolic panel^L",
    `OBX|1|NM|2823-3^Potassium^LN||${k}|mmol/L|||||F|||${t}`, `OBX|2|NM|2951-2^Sodium^LN||136|mmol/L|||||F|||${t}`, `OBX|3|NM|2160-0^Creatinine^LN||1.7|mg/dL|||||F|||${t}`].join("\n");
}
function astmSample(mrn: string) {
  const t = ts();
  return [`H|\\^&|ASTM-${t}||OMNI-CHEM^1.0|||||||P|1|${t}`, `P|1||${mrn}`, "O|1|S1||^^^CBC", "R|1|^^^WBC|14.2|10*3/uL|4.0-11.0|H", "R|2|^^^HGB|11.1|g/dL|12.0-17.5|L", "R|3|^^^PLT|210|10*3/uL|150-450|N", "R|4|^^^CRP|86|mg/L|0-10|H", "L|1|N"].join("\n");
}

export default function PathologyPortal() {
  return <Shell title="Pathology and imaging" roles={["lab"]}><Pathology /></Shell>;
}

function Pathology() {
  const [tab, setTab] = useState("lims");
  const patients = useApi<any[]>("clinical/patients");
  const [pid, setPid] = useState("");
  useEffect(() => { if (!pid && patients.data?.[0]) setPid(patients.data[0].id); }, [patients.data, pid]);
  const patient = patients.data?.find((p) => p.id === pid);
  return (
    <>
      <div className="row between" style={{ marginBottom: 10 }}>
        <Tabs value={tab} onChange={setTab} tabs={[{ id: "lims", label: "Analyzer ingest" }, { id: "reports", label: "Reports and signing" }, { id: "imaging", label: "Imaging" }]} />
        <label>Patient
          <select value={pid} onChange={(e) => setPid(e.target.value)}>
            {(patients.data || []).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.mrn}</option>)}
          </select>
        </label>
      </div>
      <ErrorBox error={patients.error} />
      {tab === "lims" && patient && <Lims mrn={patient.mrn} />}
      {tab === "reports" && pid && <Reports pid={pid} />}
      {tab === "imaging" && pid && <Imaging pid={pid} />}
    </>
  );
}

function Lims({ mrn }: { mrn: string }) {
  const [proto, setProto] = useState<"hl7" | "astm">("hl7");
  const [msg, setMsg] = useState(hl7Sample(mrn, "6.8"));
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const recent = useApi<any[]>("diag/results/recent");
  useEffect(() => { setMsg(proto === "hl7" ? hl7Sample(mrn, "6.8") : astmSample(mrn)); }, [mrn, proto]);
  async function send() {
    setErr(null);
    try {
      const r = await api(`diag/lims/${proto}`, { json: { message: msg.replace(/\n/g, "\r") } });
      setOut(r); recent.reload();
      setMsg(proto === "hl7" ? hl7Sample(mrn, "4.4") : astmSample(mrn));
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid g2">
      <Card title="Analyzer message" actions={<><select value={proto} onChange={(e) => setProto(e.target.value as any)}><option value="hl7">HL7 v2 ORU^R01</option><option value="astm">ASTM E1394</option></select><AsyncButton className="small" onClick={send}>Ingest</AsyncButton></>}>
        <textarea rows={12} className="mono" value={msg} onChange={(e) => setMsg(e.target.value)} />
        <p className="muted">The sample HL7 message carries a potassium of 6.8 mmol/L. Ingesting it fires the clinical synapse: doctor alert, STAT ECG task and auto-hold of potassium-raising drugs.</p>
        <ErrorBox error={err} />
        {out && (
          <div className={out.panic_count ? "error" : "okbox"}>
            {out.protocol} {out.message_id}: {out.results.length} result(s) stored for {out.patient_id}. {out.panic_count ? `${out.panic_count} PANIC value(s) published to the clinical synapse.` : "No panic values."}
            {out.unknown_codes?.length > 0 && <div>Unknown codes: {out.unknown_codes.join(", ")}</div>}
            {out.warnings?.length > 0 && <div>{out.warnings.join("; ")}</div>}
          </div>
        )}
      </Card>
      <Card title="Recent results" actions={<button className="small ghost" style={{ color: "var(--brand)" }} onClick={recent.reload}>Refresh</button>}>
        <ErrorBox error={recent.error} />
        {!recent.data?.length && <Empty>No results ingested yet.</Empty>}
        <table>
          <tbody>
            {(recent.data || []).map((r) => (
              <tr key={r.id ?? r.result_id}><td>{r.patient_id}</td><td>{r.analyte} <small className="muted">{r.loinc}</small></td><td>{r.value} {r.unit}</td><td><Badge tone={flagTone(r.flag)}>{r.flag}</Badge></td><td><small>{fmtTime(r.received_at)}</small></td></tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Reports({ pid }: { pid: string }) {
  const results = useApi<any[]>(`diag/results?patient_id=${pid}`, [pid]);
  const reports = useApi<any[]>(`diag/reports?patient_id=${pid}`, [pid]);
  const [sel, setSel] = useState<number[]>([]);
  const [title, setTitle] = useState("Laboratory Report");
  const [err, setErr] = useState<string | null>(null);
  const [signed, setSigned] = useState<any>(null);
  useEffect(() => { setSel([]); setSigned(null); }, [pid]);
  async function create() {
    setErr(null);
    try { await api("diag/reports", { json: { patient_id: pid, result_ids: sel, title } }); setSel([]); reports.reload(); } catch (e) { setErr((e as Error).message); }
  }
  async function sign(id: number) {
    setErr(null);
    try { setSigned(await api(`diag/reports/${id}/sign`, { method: "POST" })); reports.reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid g2">
      <Card title="Results for report" actions={<AsyncButton className="small" onClick={create} disabled={!sel.length}>Create draft ({sel.length})</AsyncButton>}>
        <label>Report title<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <ErrorBox error={results.error || err} />
        <table>
          <tbody>
            {(results.data || []).map((r) => (
              <tr key={r.id}>
                <td><input type="checkbox" checked={sel.includes(r.id)} onChange={(e) => setSel(e.target.checked ? [...sel, r.id] : sel.filter((x) => x !== r.id))} /></td>
                <td>{r.analyte}</td><td>{r.value} {r.unit}</td><td><Badge tone={flagTone(r.flag)}>{r.flag}</Badge></td><td><small>{fmtTime(r.received_at)}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!results.data?.length && <Empty>No results for this patient. Ingest some on the Analyzer tab.</Empty>}
      </Card>
      <Card title="Reports">
        {(reports.data || []).map((r) => (
          <div key={r.id} className="row between alert-item">
            <span><b>#{r.id} {r.title}</b> <small className="muted">{r.result_ids.length} result(s) · {fmtTime(r.created_at)}</small></span>
            <span className="row">
              <Badge tone={r.status === "signed" ? "ok" : "warn"}>{r.status}</Badge>
              {r.status === "draft" && <AsyncButton className="small" onClick={() => sign(r.id)}>Sign (ECDSA)</AsyncButton>}
              {r.status === "signed" && <a href={`/api/proxy/diag/reports/${r.id}/pdf`} target="_blank" rel="noreferrer">PDF</a>}
            </span>
          </div>
        ))}
        {!reports.data?.length && <Empty>No reports yet.</Empty>}
        {signed && (
          <div className="okbox">
            Signed. SHA-256 <span className="mono">{signed.sha256.slice(0, 24)}...</span><br />
            <a href={signed.verify_url.replace(/^https?:\/\/[^/]+/, "")} target="_blank" rel="noreferrer">Open the public verification page (QR target)</a>
          </div>
        )}
      </Card>
    </div>
  );
}

function Imaging({ pid }: { pid: string }) {
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [key, setKey] = useState(0);
  async function upload(f: File) {
    setErr(null); setOk(null);
    const fd = new FormData();
    fd.append("file", f, f.name);
    try { const r = await api<any>("diag/dicom/upload", { body: fd }); setOk(`Stored study ${r.study_uid} for ${r.patient_id}.`); setKey(key + 1); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <Card title="Imaging studies" actions={<label className="row">Upload DICOM (STOW-RS)<input type="file" accept=".dcm,application/dicom" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>}>
      <ErrorBox error={err} />
      {ok && <div className="okbox">{ok}</div>}
      <DicomViewer key={`${pid}-${key}`} patientId={pid} />
    </Card>
  );
}
