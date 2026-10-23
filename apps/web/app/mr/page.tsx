"use client";
import { useEffect, useRef, useState } from "react";
import Shell from "@/components/Shell";
import SignaturePad from "@/components/SignaturePad";
import { AsyncButton, Badge, Card, Empty, ErrorBox, Tabs } from "@/components/ui";
import { api, ApiError, fmtTime, useApi } from "@/lib/api";

type Checkin = { checkin_id: number; clinic: any; at: number; valid_minutes: number };

export default function MrPortal() {
  return <Shell title="Field force (ethical detailing)" roles={["mr"]}><Mr /></Shell>;
}

function Mr() {
  const [tab, setTab] = useState("visit");
  const [chk, setChk] = useState<Checkin | null>(null);
  return (
    <>
      <div className="warnbox">This portal never shows patient data. Visits require a geo-verified check-in at the doctor&apos;s clinic and the doctor&apos;s opt-in; only medical-legal-review approved content is served.</div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "visit", label: "Visit" }, { id: "detail", label: "E-detailing" }, { id: "samples", label: "Samples" }, { id: "slots", label: "Appointments" }, { id: "analytics", label: "Analytics" }]} />
      {tab === "visit" && <Visit chk={chk} setChk={setChk} />}
      {tab === "detail" && <Detailing chk={chk} />}
      {tab === "samples" && <Samples chk={chk} />}
      {tab === "slots" && <Slots />}
      {tab === "analytics" && <Analytics />}
    </>
  );
}

function Visit({ chk, setChk }: { chk: Checkin | null; setChk: (c: Checkin | null) => void }) {
  const clinics = useApi<any[]>("life/clinics");
  const history = useApi<any[]>("life/checkins");
  const [err, setErr] = useState<string | null>(null);
  const [pos, setPos] = useState<{ lat: number; lon: number; accuracy_m: number; source: string } | null>(null);

  function locate() {
    setErr(null);
    if (!navigator.geolocation) { setErr("Geolocation is not available."); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => setPos({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: p.coords.accuracy, source: "device GPS" }),
      (e) => setErr(`Location error: ${e.message}`), { enableHighAccuracy: true, timeout: 15000 });
  }
  async function checkIn(clinic: any) {
    setErr(null);
    if (!pos) { setErr("Get a location fix first."); return; }
    try {
      const r = await api<any>("life/checkins", { json: { clinic_id: clinic.id, lat: pos.lat, lon: pos.lon, accuracy_m: pos.accuracy_m } });
      setChk({ checkin_id: r.checkin_id, clinic, at: Date.now(), valid_minutes: r.valid_minutes });
    } catch (e) {
      const d = (e as ApiError).body?.detail;
      setErr(d?.reason ? `Check-in rejected: ${d.reason} (${d.distance_m} m from the clinic)` : (e as Error).message);
    } finally { history.reload(); }
  }
  async function end() {
    if (chk) await api(`life/checkins/${chk.checkin_id}/end`, { method: "POST" });
    setChk(null); history.reload();
  }
  return (
    <div className="grid g2">
      <Card title="Location" actions={<button className="small" onClick={locate}>Use device GPS</button>}>
        {pos ? <p>{pos.lat.toFixed(5)}, {pos.lon.toFixed(5)} · ±{Math.round(pos.accuracy_m)} m <Badge>{pos.source}</Badge></p> : <Empty>No fix yet.</Empty>}
        <p className="muted">Demo: simulate standing at a clinic (desktop browsers rarely have GPS).</p>
        <div className="row">
          {(clinics.data || []).map((c) => <button key={c.id} className="small ghost" style={{ color: "var(--brand)" }} onClick={() => setPos({ lat: c.lat + 0.0003, lon: c.lon, accuracy_m: 12, source: `simulated near ${c.id}` })}>Near {c.id}</button>)}
          <button className="small ghost" style={{ color: "var(--brand)" }} onClick={() => setPos({ lat: 12.9352, lon: 77.6245, accuracy_m: 10, source: "simulated, 5 km away" })}>Far away</button>
        </div>
        <ErrorBox error={err} />
        {chk && (
          <div className="okbox row between">
            <span>Checked in at <b>{chk.clinic.name}</b> · valid {chk.valid_minutes} min</span>
            <button className="small" onClick={end}>End visit</button>
          </div>
        )}
      </Card>
      <Card title="Clinics">
        <ErrorBox error={clinics.error} />
        <table>
          <tbody>
            {(clinics.data || []).map((c) => (
              <tr key={c.id}>
                <td><b>{c.name}</b><br /><small className="muted">{c.doctor_name} · {c.specialty} · fence {c.radius_m} m</small></td>
                <td>{c.detailing_consent ? <Badge tone="ok">opted in</Badge> : <Badge tone="bad">no consent</Badge>}</td>
                <td><AsyncButton className="small" onClick={() => checkIn(c)} disabled={!pos}>Check in</AsyncButton></td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3>Recent check-ins</h3>
        {(history.data || []).slice(0, 8).map((h) => (
          <div key={h.id} className="row between"><span>{h.clinic_id} · {Math.round(h.distance_m)} m</span><span><Badge tone={h.accepted ? "ok" : "bad"}>{h.accepted ? "verified" : "rejected"}</Badge> <small>{fmtTime(h.started_at)}</small></span></div>
        ))}
      </Card>
    </div>
  );
}

function Detailing({ chk }: { chk: Checkin | null }) {
  const assets = useApi<any[]>(chk ? `life/assets?doctor=${chk.clinic.doctor}` : null, [chk?.checkin_id]);
  const [asset, setAsset] = useState<any>(null);
  const [slide, setSlide] = useState(0);
  const since = useRef(Date.now());
  const [err, setErr] = useState<string | null>(null);

  async function logDwell(s: number) {
    if (!chk || !asset) return;
    const dwell = Date.now() - since.current;
    since.current = Date.now();
    await api("life/engagement", { json: { checkin_id: chk.checkin_id, asset_id: asset.id, slide: s, dwell_ms: Math.min(dwell, 3_600_000) } }).catch((e) => setErr(e.message));
  }
  async function open(id: string) {
    setErr(null);
    try { setAsset(await api(`life/assets/${id}`)); setSlide(0); since.current = Date.now(); } catch (e) { setErr((e as Error).message); }
  }
  async function go(n: number) { await logDwell(slide); setSlide(n); }
  useEffect(() => () => { void logDwell(slide); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!chk) return <Card title="E-detailing"><Empty>Check in at a clinic first. Content is unlocked only during a verified visit.</Empty></Card>;
  const slides = asset?.slides || [];
  return (
    <div className="grid side">
      <Card title={`Approved content for ${chk.clinic.doctor_name}`}>
        <ErrorBox error={assets.error || err} />
        {(assets.data || []).map((a) => (
          <div key={a.id} className={`alert-item click ${asset?.id === a.id ? "sel" : ""}`} onClick={() => open(a.id)} style={{ cursor: "pointer" }}>
            <b>{a.title}</b><br /><small className="muted">{a.product} · {a.kind} · {a.mlr_ref}</small>
            {a.relevance != null && <div className="bar" title={`relevance ${a.relevance}`}><i style={{ width: `${Math.min(100, a.relevance * 15)}%` }} /></div>}
          </div>
        ))}
      </Card>
      <Card title={asset ? asset.title : "Select content"} actions={asset && <Badge tone="info">{asset.mlr_ref}</Badge>}>
        {!asset && <Empty>Relevance ranking uses only the doctor&apos;s declared interests, specialty and publications, never patient data.</Empty>}
        {asset && (
          <div className="stack">
            <div className="slide"><h3>{slides[slide]?.title}</h3><p>{slides[slide]?.body}</p></div>
            <div className="row between">
              <button disabled={slide === 0} onClick={() => go(slide - 1)}>Previous</button>
              <span>{slide + 1} / {slides.length}</span>
              <button disabled={slide >= slides.length - 1} onClick={() => go(slide + 1)}>Next</button>
            </div>
            <button className="ghost" style={{ color: "var(--brand)" }} onClick={() => logDwell(slide)}>Finish and log engagement</button>
            <small className="muted">Source: {asset.source}</small>
          </div>
        )}
      </Card>
    </div>
  );
}

function Samples({ chk }: { chk: Checkin | null }) {
  const stock = useApi<any[]>("life/samples/stock");
  const ledger = useApi<any[]>("life/samples/ledger");
  const [product, setProduct] = useState("");
  const [qty, setQty] = useState(2);
  const [sig, setSig] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function give() {
    setMsg(null);
    try {
      const r = await api<any>("life/samples/distribute", { json: { checkin_id: chk!.checkin_id, product, qty, signature_png: sig } });
      setMsg({ ok: true, text: `Recorded ledger #${r.ledger_id}, lot ${r.lot}. ${r.remaining} left. Signature SHA-256 ${r.signature_sha256.slice(0, 16)}...` });
      stock.reload(); ledger.reload();
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  }
  return (
    <div className="grid g2">
      <Card title="Distribute samples">
        {!chk ? <Empty>Requires an active verified check-in.</Empty> : (
          <div className="stack">
            <div className="row">
              <label>Product<select value={product} onChange={(e) => setProduct(e.target.value)}><option value="">Select...</option>{(stock.data || []).map((s) => <option key={s.product} value={s.product}>{s.product} ({s.qty} left, {s.lot})</option>)}</select></label>
              <label>Qty<input type="number" min={1} max={20} value={qty} onChange={(e) => setQty(Number(e.target.value))} style={{ width: 70 }} /></label>
            </div>
            <SignaturePad onChange={setSig} />
            <AsyncButton onClick={give} disabled={!product || !sig}>Record with signature</AsyncButton>
            {msg && <div className={msg.ok ? "okbox" : "error"}>{msg.text}</div>}
            <small className="muted">Monthly cap per doctor and product is enforced server-side.</small>
          </div>
        )}
      </Card>
      <Card title="Sample ledger">
        {(ledger.data || []).map((l) => <div key={l.id} className="row between"><span>{l.product} x {l.qty} → {l.doctor} <small className="muted">{l.lot}</small></span><small>{fmtTime(l.at)}</small></div>)}
        {!ledger.data?.length && <Empty>No distributions yet.</Empty>}
      </Card>
    </div>
  );
}

function Slots() {
  const slots = useApi<any[]>("life/slots");
  const [topic, setTopic] = useState("RENOVIA-3 outcomes update");
  const [err, setErr] = useState<string | null>(null);
  async function req(id: number) {
    setErr(null);
    try { await api(`life/slots/${id}/request`, { json: { topic } }); slots.reload(); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <Card title="Doctor-published detailing slots" actions={<label>Topic<input value={topic} onChange={(e) => setTopic(e.target.value)} /></label>}>
      <ErrorBox error={err || slots.error} />
      <table>
        <tbody>
          {(slots.data || []).slice(0, 30).map((s) => (
            <tr key={s.id}><td>{fmtTime(s.starts_at)}</td><td>{s.doctor}</td><td>{s.duration_min} min</td><td><Badge tone={s.status === "open" ? "neutral" : s.status === "approved" ? "ok" : "warn"}>{s.status}</Badge> {s.topic}</td>
              <td>{s.status === "open" && <button className="small" onClick={() => req(s.id)}>Request</button>}</td></tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Analytics() {
  const a = useApi<any>("life/analytics");
  return (
    <Card title="Engagement analytics">
      <ErrorBox error={a.error} />
      {a.data && (
        <>
          <div className="row"><Badge tone="ok">{a.data.verified_visits} verified visits</Badge><Badge tone="bad">{a.data.rejected_checkins} rejected check-ins</Badge></div>
          <table style={{ marginTop: 10 }}>
            <thead><tr><th>Content</th><th>Slide</th><th>Views</th><th>Avg dwell</th></tr></thead>
            <tbody>{a.data.slides.map((s: any, i: number) => <tr key={i}><td>{s.title}</td><td>{s.slide + 1}</td><td>{s.views}</td><td>{(Number(s.avg_dwell_ms) / 1000).toFixed(1)} s</td></tr>)}</tbody>
          </table>
        </>
      )}
    </Card>
  );
}
