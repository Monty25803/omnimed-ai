"use client";
import { Fragment, useState } from "react";
import Shell from "@/components/Shell";
import { AsyncButton, Badge, Card, ErrorBox } from "@/components/ui";
import { api, fmtTime, useApi } from "@/lib/api";

export default function AuditPortal() {
  return <Shell title="Audit and compliance" roles={["admin"]}><Audit /></Shell>;
}

function Audit() {
  const [outcome, setOutcome] = useState("");
  const rows = useApi<any[]>(`clinical/audit?limit=300${outcome ? `&outcome=${outcome}` : ""}`, [outcome]);
  const [ver, setVer] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  async function verify() {
    setErr(null);
    try { setVer(await api("clinical/audit/verify")); } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="stack">
      <Card title="Ledger integrity" actions={<AsyncButton className="small" onClick={verify}>Verify hash chain + WORM mirror</AsyncButton>}>
        <ErrorBox error={err} />
        {ver && (
          <div className={ver.valid ? "okbox" : "error"}>
            {ver.valid ? "Chain intact." : `Chain BROKEN at seq ${ver.broken_at_seq}.`} {ver.entries_checked} entries recomputed; {ver.worm_sampled} cross-checked against the object-locked MinIO bucket
            {ver.worm_mismatches?.length ? `, mismatches: ${ver.worm_mismatches.join(", ")}` : ", no mismatches"}.
            <div className="mono">head {ver.head_hash}</div>
          </div>
        )}
        <p className="muted">Each entry stores SHA-256(prev_hash ‖ canonical fields). A database trigger blocks UPDATE/DELETE, and every entry is mirrored to a COMPLIANCE-mode object-lock bucket.</p>
      </Card>
      <Card title="Access log" actions={<select value={outcome} onChange={(e) => setOutcome(e.target.value)}><option value="">All outcomes</option><option value="allowed">Allowed</option><option value="denied">Denied</option><option value="blocked">Blocked</option></select>}>
        <ErrorBox error={rows.error} />
        <table>
          <thead><tr><th>Seq</th><th>When</th><th>Actor</th><th>Action</th><th>Resource</th><th>Outcome</th><th>WORM</th></tr></thead>
          <tbody>
            {(rows.data || []).map((r) => (
              <Fragment key={r.seq}>
                <tr className="click" onClick={() => setOpen(open === r.seq ? null : r.seq)}>
                  <td>{r.seq}</td><td><small>{fmtTime(r.at)}</small></td><td>{r.actor} <small className="muted">{r.role}</small></td>
                  <td>{r.action}</td><td>{r.resource} {r.resource_id}<small className="muted"> {r.facility}</small></td>
                  <td><Badge tone={r.outcome === "allowed" ? "ok" : "bad"}>{r.outcome}</Badge></td>
                  <td>{r.mirrored ? <Badge tone="info">locked</Badge> : <Badge>pending</Badge>}</td>
                </tr>
                {open === r.seq && <tr><td colSpan={7}><pre className="json">{r.detail}</pre><div className="mono muted">prev {r.prev_hash}<br />hash {r.hash}</div></td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
