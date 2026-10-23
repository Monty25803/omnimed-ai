"use client";
import { useEffect, useRef } from "react";

export default function SignaturePad({ onChange }: { onChange: (dataUrl: string | null) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const strokes = useRef(0);

  useEffect(() => {
    const c = ref.current!;
    const ctx = c.getContext("2d")!;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#102030";
  }, []);

  const pos = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * e.currentTarget.width, ((e.clientY - r.top) / r.height) * e.currentTarget.height];
  };

  return (
    <div className="stack" style={{ gap: 4 }}>
      <canvas
        ref={ref}
        width={480}
        height={150}
        className="sig"
        style={{ width: "100%" }}
        onPointerDown={(e) => {
          drawing.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          const ctx = e.currentTarget.getContext("2d")!;
          const [x, y] = pos(e);
          ctx.beginPath();
          ctx.moveTo(x, y);
        }}
        onPointerMove={(e) => {
          if (!drawing.current) return;
          const ctx = e.currentTarget.getContext("2d")!;
          const [x, y] = pos(e);
          ctx.lineTo(x, y);
          ctx.stroke();
        }}
        onPointerUp={(e) => {
          drawing.current = false;
          strokes.current += 1;
          onChange(e.currentTarget.toDataURL("image/png"));
        }}
      />
      <div className="row between">
        <small className="muted">Doctor signs above to acknowledge receipt.</small>
        <button type="button" className="small ghost" style={{ color: "var(--brand)" }} onClick={() => {
          const c = ref.current!;
          const ctx = c.getContext("2d")!;
          ctx.fillStyle = "#fff";
          ctx.fillRect(0, 0, c.width, c.height);
          strokes.current = 0;
          onChange(null);
        }}>Clear</button>
      </div>
    </div>
  );
}
