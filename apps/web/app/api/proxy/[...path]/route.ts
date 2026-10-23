import { NextRequest, NextResponse } from "next/server";
import { accessToken, GATEWAY } from "@/lib/session";

export const dynamic = "force-dynamic";

const ALLOWED = new Set(["clinical", "diag", "life", "ai"]);
const PUBLIC = [/^diag\/verify$/, /^ai\/ocr\/sample$/];
const PASS_HEADERS = ["content-type", "content-disposition", "x-frame-count", "cache-control"];

async function forward(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");
  if (!ALLOWED.has(params.path[0])) return NextResponse.json({ error: "unknown service" }, { status: 404 });
  const token = await accessToken();
  if (!token && !PUBLIC.some((re) => re.test(path))) return NextResponse.json({ error: "not signed in" }, { status: 401 });

  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  const accept = req.headers.get("accept");
  if (accept) headers.Accept = accept;

  const init: RequestInit & { duplex?: string } = { method: req.method, headers, cache: "no-store", signal: req.signal };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = req.body;
    init.duplex = "half";
  }
  const upstream = await fetch(`${GATEWAY}/api/${path}${req.nextUrl.search}`, init);

  const out = new Headers();
  for (const h of PASS_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  if ((upstream.headers.get("content-type") || "").startsWith("text/event-stream")) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("X-Accel-Buffering", "no");
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers: out });
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE, forward as PATCH };
