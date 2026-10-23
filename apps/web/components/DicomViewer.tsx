"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Badge, ErrorBox } from "./ui";

type Instance = { sop_uid: string; number: number; frames: number };
type Series = {
  series_uid: string; description: string; modality: string; rows: number; columns: number; pixel_spacing: number[];
  bits_allocated: number; pixel_representation: number; rescale_slope: number; rescale_intercept: number;
  window_center: number; window_width: number; instances: Instance[];
};
type Finding = { label: string; area_mm2: number; equivalent_diameter_mm: number; mean_hu: number; circularity: number; centroid: number[]; contour: number[][] };
type SegResult = { findings: Finding[]; disclaimer: string; inference_ms: number; plugin: string; version: string };

const VS = `#version 300 es
in vec2 p; out vec2 uv;
void main() { uv = vec2((p.x + 1.0) * 0.5, (1.0 - p.y) * 0.5); gl_Position = vec4(p, 0.0, 1.0); }`;
const FS = `#version 300 es
precision highp float;
uniform highp sampler2D img; uniform float wc; uniform float ww; uniform int invert;
in vec2 uv; out vec4 o;
void main() {
  float hu = texture(img, uv).r;
  float v = clamp((hu - (wc - 0.5)) / max(ww - 1.0, 1.0) + 0.5, 0.0, 1.0);
  if (invert == 1) v = 1.0 - v;
  o = vec4(v, v, v, 1.0);
}`;

const PRESETS: Record<string, [number, number]> = { Lung: [-600, 1500], Mediastinum: [40, 400], Bone: [400, 1800], Soft: [50, 350] };

function compile(gl: WebGL2RenderingContext, type: number, src: string) {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || "shader error");
  return s;
}

export default function DicomViewer({ patientId, canSegment = true }: { patientId: string; canSegment?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const glRef = useRef<{ gl: WebGL2RenderingContext; prog: WebGLProgram; tex: WebGLTexture } | null>(null);
  // Stored pixel values stay 16-bit (half the memory of HU floats); only the drawn slice is converted.
  const cache = useRef<Map<string, Int16Array | Uint16Array>>(new Map());
  const scratch = useRef<Float32Array | null>(null);
  const [studies, setStudies] = useState<any[]>([]);
  const [study, setStudy] = useState<string>("");
  const [series, setSeries] = useState<Series | null>(null);
  const [idx, setIdx] = useState(0);
  const [wl, setWl] = useState<[number, number]>([-600, 1500]);
  const [invert, setInvert] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<{ loaded: number; bytes: number; ms: number; via: string } | null>(null);
  const [seg, setSeg] = useState<SegResult | null>(null);
  const [segFor, setSegFor] = useState<string>("");
  const [probe, setProbe] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setStudies([]); setStudy(""); setSeries(null); setSeg(null); cache.current.clear();
    api<any[]>(`diag/dicom/studies?patient_id=${patientId}`).then((s) => { setStudies(s); if (s[0]) setStudy(s[0].study_uid); }).catch((e) => setError(e.message));
  }, [patientId]);

  useEffect(() => {
    if (!study) return;
    api<Series[]>(`diag/dicom/studies/${study}/series`).then((s) => {
      const se = s[0];
      setSeries(se);
      setIdx(Math.floor(se.instances.length / 2));
      if (se.modality === "CT") setWl(PRESETS.Lung); else setWl([se.window_center, se.window_width]);
    }).catch((e) => setError(e.message));
  }, [study]);

  // WebGL2 setup: HU values live in an R32F texture so window/level is a pure shader uniform change.
  useEffect(() => {
    const c = canvas.current;
    if (!c || !series) return;
    c.width = series.columns; c.height = series.rows;
    const gl = c.getContext("webgl2");
    if (!gl) { setError("WebGL2 is not available in this browser."); return; }
    const prog = gl.createProgram()!;
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const tex = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
    glRef.current = { gl, prog, tex };
  }, [series]);

  const typed = useCallback((buf: ArrayBuffer, s: Series, offset = 0, length?: number) =>
    (s.pixel_representation === 1 ? new Int16Array(buf, offset, length) : new Uint16Array(buf, offset, length)), []);

  // Preferred path: a few large slice ranges from the assembled series volume.
  const loadVolume = useCallback(async (s: Series) => {
    const n = s.instances.length;
    const sliceBytes = s.rows * s.columns * 2;
    const chunk = Math.max(16, Math.ceil(n / 4));
    let bytes = 0;
    const ranges: [number, number][] = [];
    for (let start = 0; start < n; start += chunk) ranges.push([start, Math.min(chunk, n - start)]);
    await Promise.all(ranges.map(async ([start, count]) => {
      const res = await fetch(`/api/proxy/diag/dicom/volume/${study}/${s.series_uid}?start=${start}&count=${count}`);
      if (!res.ok) throw new Error(`volume fetch failed (${res.status})`);
      const b = await res.arrayBuffer();
      if (b.byteLength !== count * sliceBytes) throw new Error("volume fetch returned a short read");
      bytes += b.byteLength;
      for (let k = 0; k < count; k++) cache.current.set(s.instances[start + k].sop_uid, typed(b, s, k * sliceBytes, s.rows * s.columns));
    }));
    return bytes;
  }, [study, typed]);

  // Fallback for compressed or multi-frame series: one WADO-RS frame request per instance.
  const loadFrames = useCallback(async (s: Series) => {
    let bytes = 0;
    const todo = s.instances.filter((i) => !cache.current.has(i.sop_uid));
    const pool = 6;
    for (let k = 0; k < todo.length; k += pool) {
      await Promise.all(todo.slice(k, k + pool).map(async (inst) => {
        const res = await fetch(`/api/proxy/diag/dicom/frames/${study}/${s.series_uid}/${inst.sop_uid}?f=1`);
        if (!res.ok) throw new Error(`frame fetch failed (${res.status})`);
        const b = await res.arrayBuffer();
        bytes += b.byteLength;
        cache.current.set(inst.sop_uid, typed(b, s));
      }));
    }
    return bytes;
  }, [study, typed]);

  const loadAll = useCallback(async () => {
    if (!series) return;
    setBusy(true);
    const t0 = performance.now();
    let bytes: number;
    let via = "volume";
    try {
      bytes = series.bits_allocated === 16 ? await loadVolume(series) : await loadFrames(series);
      if (series.bits_allocated !== 16) via = "frames";
    } catch {
      via = "frames";
      bytes = await loadFrames(series);
    }
    setStats({ loaded: cache.current.size, bytes, ms: Math.round(performance.now() - t0), via });
    setBusy(false);
  }, [series, loadVolume, loadFrames]);

  useEffect(() => { loadAll().catch((e) => { setError(e.message); setBusy(false); }); }, [loadAll]);

  const inst = series?.instances[idx];
  const draw = useCallback(() => {
    const g = glRef.current;
    if (!g || !series || !inst) return;
    const raw = cache.current.get(inst.sop_uid);
    if (!raw) return;
    if (!scratch.current || scratch.current.length !== raw.length) scratch.current = new Float32Array(raw.length);
    const px = scratch.current;
    for (let i = 0; i < raw.length; i++) px[i] = raw[i] * series.rescale_slope + series.rescale_intercept;
    const { gl, prog, tex } = g;
    gl.viewport(0, 0, series.columns, series.rows);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, series.columns, series.rows, 0, gl.RED, gl.FLOAT, px);
    gl.uniform1f(gl.getUniformLocation(prog, "wc"), wl[0]);
    gl.uniform1f(gl.getUniformLocation(prog, "ww"), wl[1]);
    gl.uniform1i(gl.getUniformLocation(prog, "invert"), invert ? 1 : 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }, [series, inst, wl, invert]);

  useEffect(() => { draw(); }, [draw, stats]);

  useEffect(() => {
    if (!inst) return;
    api<any[]>(`diag/dicom/annotations?study_uid=${study}&sop_uid=${inst.sop_uid}`).then((a) => {
      if (a[0]) { setSeg(a[0].result); setSegFor(inst.sop_uid); } else if (segFor !== inst.sop_uid) setSeg(null);
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inst?.sop_uid, study]);

  function onWheel(e: React.WheelEvent) {
    if (!series) return;
    e.preventDefault();
    setIdx((i) => Math.max(0, Math.min(series.instances.length - 1, i + (e.deltaY > 0 ? 1 : -1))));
  }

  function onDrag(e: React.MouseEvent) {
    if (e.buttons !== 1) return;
    setWl(([c, w]) => [c + e.movementY * 4, Math.max(1, w + e.movementX * 8)]);
  }

  function onMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!series || !inst) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.floor(((e.clientX - r.left) / r.width) * series.columns);
    const y = Math.floor(((e.clientY - r.top) / r.height) * series.rows);
    const px = cache.current.get(inst.sop_uid);
    if (px && x >= 0 && y >= 0 && x < series.columns && y < series.rows) setProbe(`(${x}, ${y}) ${Math.round(px[y * series.columns + x] * series.rescale_slope + series.rescale_intercept)} HU`);
    onDrag(e);
  }

  async function runSegment() {
    if (!series || !inst) return;
    setBusy(true);
    try {
      const r = await api<SegResult>("diag/dicom/segment", { json: { study_uid: study, series_uid: series.series_uid, sop_uid: inst.sop_uid, frame: 1 } });
      setSeg(r); setSegFor(inst.sop_uid);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (!studies.length) return <div><ErrorBox error={error} /><p className="muted">No imaging studies for this patient.</p></div>;
  const showSeg = seg && segFor === inst?.sop_uid;
  return (
    <div className="stack">
      <ErrorBox error={error} />
      <div className="row between">
        <select value={study} onChange={(e) => setStudy(e.target.value)}>
          {studies.map((s) => <option key={s.study_uid} value={s.study_uid}>{s.description} · {s.study_date} · {s.instances} images</option>)}
        </select>
        <div className="row">
          {Object.entries(PRESETS).map(([k, v]) => <button key={k} className="small ghost" style={{ color: "var(--brand)" }} onClick={() => setWl(v)}>{k}</button>)}
          <button className="small ghost" style={{ color: "var(--brand)" }} onClick={() => setInvert(!invert)}>Invert</button>
          {canSegment && <button className="small" disabled={busy || !stats} onClick={runSegment}>Run lesion assist</button>}
        </div>
      </div>
      {series && (
        <div className="grid side" style={{ gridTemplateColumns: "minmax(0, 1fr) 280px" }}>
          <div className="viewer" onWheel={onWheel}>
            <canvas ref={canvas} onMouseMove={onMove} onContextMenu={(e) => e.preventDefault()} />
            {showSeg && (
              <svg className="overlay" viewBox={`0 0 ${series.columns} ${series.rows}`} preserveAspectRatio="none">
                {seg!.findings.map((f, i) => (
                  <g key={i}>
                    <polygon points={f.contour.map((p) => p.join(",")).join(" ")} fill="rgba(255,60,60,.18)" stroke="#ff4d4d" strokeWidth={1.5} />
                    <text x={f.centroid[0] + 14} y={f.centroid[1] - 10} fill="#ffb3b3" fontSize={14}>{f.equivalent_diameter_mm} mm</text>
                  </g>
                ))}
              </svg>
            )}
            <div className="hud">
              {series.modality} · image {idx + 1}/{series.instances.length} · W {Math.round(wl[1])} L {Math.round(wl[0])}<br />
              {probe}
            </div>
          </div>
          <div className="stack">
            <input type="range" min={0} max={series.instances.length - 1} value={idx} onChange={(e) => setIdx(Number(e.target.value))} />
            <dl className="kv">
              <dt>Series</dt><dd>{series.description || series.series_uid.slice(-12)}</dd>
              <dt>Matrix</dt><dd>{series.columns} x {series.rows}</dd>
              <dt>Spacing</dt><dd>{series.pixel_spacing.map(Number).map((x) => x.toFixed(2)).join(" x ")} mm</dd>
              <dt>Loaded</dt><dd>{stats ? `${stats.loaded} images, ${(stats.bytes / 1048576).toFixed(1)} MB in ${stats.ms} ms (${stats.via})` : busy ? "loading..." : ""}</dd>
            </dl>
            <p className="muted">Scroll to change slice, drag to adjust window/level.</p>
            {showSeg && (
              <div>
                <h3>Lesion assist <Badge tone="info">{seg!.plugin} {seg!.version}</Badge></h3>
                {seg!.findings.length === 0 && <p className="muted">No enclosed lesion found on this slice.</p>}
                {seg!.findings.map((f, i) => (
                  <div key={i} className="warnbox">
                    <b>{f.label}</b><br />{f.equivalent_diameter_mm} mm eq. diameter · {f.area_mm2} mm² · {f.mean_hu} HU · circularity {f.circularity}
                  </div>
                ))}
                <small className="muted">{seg!.disclaimer}</small>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
