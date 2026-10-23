"use client";
import { useState } from "react";
import Shell from "@/components/Shell";
import StockPanel from "@/components/StockPanel";
import { AsyncButton, Badge, Card, Empty, ErrorBox, Tabs } from "@/components/ui";
import { api, ApiError, fmtDate, fmtTime, useApi } from "@/lib/api";

export default function PharmacyPortal() {
  return <Shell title="Pharmacy" roles={["pharmacist"]}><Pharmacy /></Shell>;
}

function Pharmacy() {
  const [tab, setTab] = useState("queue");
  return (
    <>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "queue", label: "Dispensing queue" }, { id: "inventory", label: "Inventory" }, { id: "refill", label: "Refill risk" }, { id: "ocr", label: "Prescription OCR" }]} />
      {tab === "queue" && <Queue />}
      {tab === "inventory" && <Inventory />}
      {tab === "refill" && <Refill />}
      {tab === "ocr" && <Ocr />}
    </>
  );
}

function Queue() {
  const rx = useApi<any[]>("clinical/prescriptions");
  const [sel, setSel] = useState<any>(null);
  const [subs, setSubs] = useState<any>(null);
  const [network, setNetwork] = useState<any>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function pick(m: any) {
    setSel(m); setMsg(null); setNetwork(null);
    try { setSubs(await api(`clinical/pharmacy/substitutes?rxcui=${m.rxcui}&strength_mg=${m.strength_mg}`)); } catch (e) { setSubs(null); setMsg({ ok: false, text: (e as Error).message }); }
  }
  async function dispense(inventoryId?: number) {
    setMsg(null);
    try {
      const r = await api<any>(`clinical/prescriptions/${sel.id}/dispense`, { json: inventoryId ? { inventory_id: inventoryId } : {} });
      setMsg({ ok: true, text: `Dispensed ${r.dispensed_qty} x ${r.brand}. Remaining stock ${r.remaining_stock}${r.low_stock ? " (LOW, reorder)" : ""}.` });
      rx.reload();
    } catch (e) {
      const ae = e as ApiError;
      setMsg({ ok: false, text: ae.message });
      if (ae.body?.network) setNetwork(ae.body.network);
    }
  }
  const queue = (rx.data || []).filter((m) => !m.dispensed || m.status === "on-hold");
  return (
    <div className="grid g2">
      <Card title="Prescriptions awaiting dispensing" actions={<button className="small ghost" style={{ color: "var(--brand)" }} onClick={rx.reload}>Refresh</button>}>
        <ErrorBox error={rx.error} />
        <table>
          <thead><tr><th>Patient</th><th>Drug</th><th>Status</th></tr></thead>
          <tbody>
            {queue.map((m) => (
              <tr key={m.id} className={`click ${sel?.id === m.id ? "sel" : ""}`} onClick={() => pick(m)}>
                <td>{m.patient_id}<br /><small className="muted">{m.prescriber} · {fmtDate(m.created_at)}</small></td>
                <td><b>{m.drug}</b> {m.strength_mg} mg {m.frequency} x {m.duration_days} d</td>
                <td><Badge tone={m.status === "active" ? "ok" : "bad"}>{m.status}</Badge>{m.hold_reason && <><br /><small>{m.hold_reason}</small></>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!queue.length && <Empty>Queue is empty.</Empty>}
      </Card>
      <Card title={sel ? `#${sel.id} ${sel.drug} ${sel.strength_mg} mg` : "Select a prescription"}>
        {!sel && <Empty>Pick a prescription to see bio-equivalent substitutes and dispense.</Empty>}
        {sel && (
          <div className="stack">
            {sel.status === "on-hold" && <div className="error">On hold: {sel.hold_reason}. Only the prescriber can release it.</div>}
            {sel.status === "active" && <AsyncButton onClick={() => dispense()}>Dispense (best on-site stock)</AsyncButton>}
            {msg && <div className={msg.ok ? "okbox" : "error"}>{msg.text}</div>}
            {network && <StockPanel s={network} />}
            {subs && (
              <>
                <h3>Bio-equivalent substitutes</h3>
                <p className="muted">{subs.basis}</p>
                <table>
                  <thead><tr><th>Product</th><th>Where</th><th>Qty</th><th>Price</th><th /></tr></thead>
                  <tbody>
                    {subs.options.map((o: any) => (
                      <tr key={o.inventory_id}>
                        <td>{o.brand} {o.generic && <Badge tone="ok">generic</Badge>}{o.saving_vs_costliest_pct > 0 && <> <Badge tone="info">save {o.saving_vs_costliest_pct}%</Badge></>}</td>
                        <td>{o.pharmacy} <small className="muted">{o.distance_miles} mi</small></td>
                        <td>{o.qty}</td><td>₹{o.unit_price}</td>
                        <td>{o.distance_miles === 0 && o.qty > 0 && sel.status === "active" && <button className="small" onClick={() => dispense(o.inventory_id)}>Dispense this</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

function Inventory() {
  const inv = useApi<any[]>("clinical/pharmacy/inventory");
  const [csv, setCsv] = useState("8901000000022,40\n8901000000052,25");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  async function sync() {
    setErr(null);
    const items = csv.split(/\n/).map((l) => l.split(",")).filter((p) => p.length === 2).map(([barcode, qty]) => ({ barcode: barcode.trim(), qty: Number(qty) }));
    try { setRes(await api("clinical/pharmacy/inventory/sync", { json: { items } })); inv.reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid side" style={{ gridTemplateColumns: "minmax(0,1fr) 320px" }}>
      <Card title="Hospital pharmacy stock">
        <ErrorBox error={inv.error} />
        <table>
          <thead><tr><th>Product</th><th>Molecule</th><th>Qty</th><th>Used 14 d</th><th>Cover</th><th>Status</th></tr></thead>
          <tbody>
            {(inv.data || []).map((i) => (
              <tr key={i.id}>
                <td>{i.brand} {i.generic && <Badge tone="ok">generic</Badge>}<br /><small className="mono muted">{i.barcode}</small></td>
                <td>{i.ingredient} {i.strength_mg} mg</td>
                <td>{i.qty}</td><td>{i.used_14d}</td>
                <td>{i.days_of_cover != null ? `${i.days_of_cover} d` : "-"}</td>
                <td><Badge tone={i.status === "ok" ? "ok" : i.status === "out" ? "bad" : "warn"}>{i.status}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Barcode stock count sync">
        <p className="muted">One <code>barcode,qty</code> per line, as exported by a handheld scanner.</p>
        <textarea rows={8} value={csv} onChange={(e) => setCsv(e.target.value)} className="mono" />
        <AsyncButton onClick={sync}>Sync counts</AsyncButton>
        <ErrorBox error={err} />
        {res && <div className="okbox">Updated {res.updated} item(s).{res.unknown_barcodes?.length ? ` Unknown: ${res.unknown_barcodes.join(", ")}` : ""}</div>}
      </Card>
    </div>
  );
}

function Refill() {
  const risk = useApi<any[]>("clinical/pharmacy/refill-risk");
  const outbox = useApi<any[]>("clinical/outbox");
  const [msg, setMsg] = useState<string | null>(null);
  async function run() {
    const r = await api<any>("clinical/pharmacy/reminders/run", { method: "POST" });
    setMsg(`${r.queued} reminder(s) queued.`); outbox.reload();
  }
  return (
    <div className="grid g2">
      <Card title="Predicted non-adherence" actions={<AsyncButton className="small" onClick={run}>Queue reminders</AsyncButton>}>
        <ErrorBox error={risk.error} />
        {msg && <div className="okbox">{msg}</div>}
        <table>
          <thead><tr><th>Patient</th><th>Drug</th><th>PDC</th><th>Runs out</th><th>Risk</th></tr></thead>
          <tbody>
            {(risk.data || []).map((r, i) => (
              <tr key={i}>
                <td>{r.patient_name}</td><td>{r.drug}</td><td>{Math.round(r.pdc * 100)}%</td>
                <td>{fmtDate(r.run_out_date)}<br /><small className="muted">{r.days_until_out < 0 ? `${-r.days_until_out} d overdue` : `in ${r.days_until_out} d`}</small></td>
                <td><Badge tone={r.risk_band === "high" ? "bad" : r.risk_band === "medium" ? "warn" : "ok"}>{r.risk_band} {r.risk}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">PDC = proportion of days covered from fill history. Risk is a logistic score over PDC, lateness and days to run-out.</p>
      </Card>
      <Card title="Notification outbox (SMS / WhatsApp)">
        <ErrorBox error={outbox.error} />
        {!outbox.data?.length && <Empty>No messages queued.</Empty>}
        {(outbox.data || []).map((o) => (
          <div key={o.id} className="alert-item"><div className="row between"><b>{o.channel} → {o.destination}</b><small>{fmtTime(o.created_at)} · {o.status}</small></div><p>{o.message}</p></div>
        ))}
      </Card>
    </div>
  );
}

function Ocr() {
  const [file, setFile] = useState<File | null>(null);
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<string>("/api/proxy/ai/ocr/sample");
  async function run(useSample: boolean) {
    setErr(null);
    try {
      let blob: Blob | null = file;
      if (useSample) blob = await (await fetch("/api/proxy/ai/ocr/sample")).blob();
      if (!blob) return;
      const fd = new FormData();
      fd.append("file", blob, "prescription.png");
      setOut(await api("ai/ocr", { body: fd }));
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid g2">
      <Card title="Scan a paper prescription">
        <div className="stack">
          <input type="file" accept="image/*" capture="environment" onChange={(e) => { const f = e.target.files?.[0] || null; setFile(f); if (f) setPreview(URL.createObjectURL(f)); }} />
          <div className="row">
            <AsyncButton onClick={() => run(false)} disabled={!file}>Read uploaded image</AsyncButton>
            <AsyncButton className="ghost" onClick={() => run(true)}>Use synthetic sample</AsyncButton>
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt="prescription" style={{ width: "100%", border: "1px solid var(--line)", borderRadius: 8 }} />
          <ErrorBox error={err} />
        </div>
      </Card>
      <Card title="Extracted medications" actions={out && <Badge tone="info">{out.engine} · {out.elapsed_ms} ms</Badge>}>
        {!out && <Empty>Results appear here. Every item is matched to RxNorm; low-confidence lines are flagged for pharmacist review.</Empty>}
        {out?.items.map((i: any, k: number) => (
          <div key={k} className={i.needs_review ? "warnbox" : "okbox"}>
            <b>{i.molecule}</b> <small>(read &quot;{i.matched_text}&quot;, RxNorm {i.rxcui})</small><br />
            {i.strength_mg ?? "?"} mg · {i.frequency ?? "frequency unclear"} · {i.duration_days ? `${i.duration_days} d` : "duration unclear"} · confidence {i.confidence}
            {i.needs_review && <div><b>Needs review</b>{!i.strength_valid && " · strength not marketed"}</div>}
          </div>
        ))}
        {out && <details><summary className="muted">Raw OCR lines</summary>{out.lines.map((l: any, k: number) => <div key={k} className="mono">{l.ocr_confidence} | {l.text}</div>)}</details>}
      </Card>
    </div>
  );
}
