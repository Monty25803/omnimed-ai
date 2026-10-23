"use client";
import { Fragment, Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

function Verify() {
  const q = useSearchParams();
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const r = q.get("r"), h = q.get("h"), s = q.get("s");
    if (!r || !h || !s) { setErr("This link is missing the report id, hash or signature."); return; }
    fetch(`/api/proxy/diag/verify?r=${encodeURIComponent(r)}&h=${encodeURIComponent(h)}&s=${encodeURIComponent(s)}`)
      .then((x) => x.json()).then(setRes).catch((e) => setErr(e.message));
  }, [q]);
  return (
    <div className="login">
      <div className="card" style={{ maxWidth: 640 }}>
        <h1 style={{ fontSize: 22 }}>Report verification</h1>
        <p className="muted">OmniMed City Hospital Laboratory · ECDSA P-256 signed reports</p>
        {err && <div className="error">{err}</div>}
        {!res && !err && <p>Checking...</p>}
        {res && (
          <>
            <div className={res.valid ? "okbox" : "error"} style={{ fontSize: 16 }}>
              {res.valid ? "Authentic. This report was signed by the laboratory and has not been altered." : `NOT VERIFIED. ${res.reason || "The content or signature does not match."}`}
            </div>
            {res.report_id && (
              <dl className="kv">
                <dt>Report</dt><dd>#{res.report_id} {res.title}</dd>
                <dt>Signed by</dt><dd>{res.signed_by}</dd>
                <dt>Signed at</dt><dd>{res.signed_at && new Date(res.signed_at).toLocaleString("en-IN")}</dd>
                <dt>Results</dt><dd>{res.result_count}</dd>
                {res.checks && Object.entries(res.checks).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{v ? "yes" : "NO"}</dd></Fragment>)}
              </dl>
            )}
            {res.signer_certificate && <details><summary className="muted">Signer certificate</summary><pre className="json">{res.signer_certificate}</pre></details>}
          </>
        )}
        <p className="muted" style={{ marginTop: 12 }}>No patient-identifying data is shown on this public page. Synthetic demonstration data.</p>
      </div>
    </div>
  );
}

export default function VerifyPage() {
  return <Suspense fallback={<p>Loading...</p>}><Verify /></Suspense>;
}
