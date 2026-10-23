"use client";
import { useEffect, useRef, useState } from "react";

/** Camera barcode scanner using the browser BarcodeDetector API, with manual entry as the fallback. */
export default function Scanner({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [on, setOn] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!on) return;
    const Detector = (window as any).BarcodeDetector;
    if (!Detector) { setMsg("Camera scanning needs BarcodeDetector (Chrome/Edge on Android or desktop). Type or paste the code."); setOn(false); return; }
    let stream: MediaStream | null = null;
    let stop = false;
    const det = new Detector({ formats: ["ean_13", "code_128", "qr_code", "data_matrix"] });
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (!video.current) return;
        video.current.srcObject = stream;
        await video.current.play();
        while (!stop) {
          const codes = await det.detect(video.current).catch(() => []);
          if (codes.length) { onChange(codes[0].rawValue); setOn(false); break; }
          await new Promise((r) => setTimeout(r, 200));
        }
      } catch (e) { setMsg((e as Error).message); setOn(false); }
    })();
    return () => { stop = true; stream?.getTracks().forEach((t) => t.stop()); };
  }, [on, onChange]);

  return (
    <div className="stack" style={{ gap: 4 }}>
      <label>{label}
        <div className="row">
          <input value={value} onChange={(e) => onChange(e.target.value)} style={{ flex: 1 }} />
          <button type="button" className="small ghost" style={{ color: "var(--brand)" }} onClick={() => setOn(!on)}>{on ? "Stop" : "Scan"}</button>
        </div>
      </label>
      {on && <video ref={video} muted playsInline style={{ width: "100%", maxHeight: 180, background: "#000", borderRadius: 6 }} />}
      {msg && <small className="muted">{msg}</small>}
    </div>
  );
}
