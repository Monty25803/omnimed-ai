import { cookies } from "next/headers";

const KEYCLOAK = process.env.KEYCLOAK_URL || "http://localhost:8080";
const TOKEN_URL = `${KEYCLOAK}/realms/omnimed/protocol/openid-connect/token`;
const CLIENT_ID = "omnimed-web";
export const GATEWAY = process.env.GATEWAY_URL || "http://localhost:8000";

const AT = "om_at";
const RT = "om_rt";
const secure = process.env.COOKIE_SECURE === "true";

type TokenSet = { access_token: string; refresh_token: string; expires_in: number; refresh_expires_in: number };

export function decodeJwt(token: string): Record<string, any> {
  const part = token.split(".")[1] || "";
  return JSON.parse(Buffer.from(part.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
}

async function tokenRequest(params: Record<string, string>): Promise<TokenSet> {
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: CLIENT_ID, ...params }),
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error_description || `identity provider returned ${res.status}`);
  }
  return res.json();
}

function store(t: TokenSet) {
  const jar = cookies();
  jar.set(AT, t.access_token, { httpOnly: true, sameSite: "strict", secure, path: "/", maxAge: t.expires_in });
  jar.set(RT, t.refresh_token, { httpOnly: true, sameSite: "strict", secure, path: "/", maxAge: t.refresh_expires_in || 1800 });
}

export async function login(username: string, password: string) {
  const t = await tokenRequest({ grant_type: "password", username, password, scope: "openid" });
  store(t);
  return decodeJwt(t.access_token);
}

export function logout() {
  const jar = cookies();
  jar.delete(AT);
  jar.delete(RT);
}

/** Returns a valid access token, refreshing it when it is within 30 s of expiry. */
export async function accessToken(): Promise<string | null> {
  const jar = cookies();
  const at = jar.get(AT)?.value;
  if (at) {
    try {
      if (decodeJwt(at).exp * 1000 - Date.now() > 30_000) return at;
    } catch {
      /* fall through to refresh */
    }
  }
  const rt = jar.get(RT)?.value;
  if (!rt) return null;
  try {
    const t = await tokenRequest({ grant_type: "refresh_token", refresh_token: rt });
    store(t);
    return t.access_token;
  } catch {
    logout();
    return null;
  }
}
