import { NextResponse } from "next/server";
import { login } from "@/lib/session";

const PORTAL: Record<string, string> = {
  doctor: "/doctor", nurse: "/nurse", pharmacist: "/pharmacy", lab: "/pathology", mr: "/mr", patient: "/patient", admin: "/audit",
};

export async function POST(req: Request) {
  const { username, password } = await req.json().catch(() => ({}));
  if (!username || !password) return NextResponse.json({ error: "username and password are required" }, { status: 400 });
  try {
    const claims = await login(String(username), String(password));
    const roles: string[] = claims.realm_access?.roles || [];
    const role = Object.keys(PORTAL).find((r) => roles.includes(r));
    if (!role) return NextResponse.json({ error: "this account has no OmniMed portal role" }, { status: 403 });
    return NextResponse.json({ role, portal: PORTAL[role], username: claims.preferred_username });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 401 });
  }
}
