import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";

const extra = (Constants.expoConfig?.extra || {}) as { gatewayUrl?: string; keycloakUrl?: string };
export const config = { gateway: extra.gatewayUrl || "http://10.0.2.2:8000", keycloak: extra.keycloakUrl || "http://10.0.2.2:8080" };

type Tokens = { access_token: string; refresh_token: string; expires_at: number };
let tokens: Tokens | null = null;
const KEY = "omnimed.tokens";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body: any) {
    super(message);
  }
}

function decode(jwt: string): Record<string, any> {
  const b64 = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const json = decodeURIComponent(
    atob(b64.padEnd(b64.length + ((4 - (b64.length % 4)) % 4), "="))
      .split("")
      .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
      .join(""),
  );
  return JSON.parse(json);
}

async function tokenRequest(params: Record<string, string>): Promise<Tokens> {
  const body = Object.entries({ client_id: "omnimed-mobile", ...params }).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  const res = await fetch(`${config.keycloak}/realms/omnimed/protocol/openid-connect/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body,
  });
  const data = await res.json();
  if (!res.ok) throw new ApiError(res.status, data.error_description || "sign-in failed", data);
  const t = { access_token: data.access_token, refresh_token: data.refresh_token, expires_at: Date.now() + data.expires_in * 1000 };
  tokens = t;
  await SecureStore.setItemAsync(KEY, JSON.stringify(t));
  return t;
}

export async function login(username: string, password: string) {
  const t = await tokenRequest({ grant_type: "password", username, password, scope: "openid" });
  const claims = decode(t.access_token);
  return { username: claims.preferred_username as string, roles: (claims.realm_access?.roles || []) as string[] };
}

export async function restore() {
  const raw = await SecureStore.getItemAsync(KEY);
  if (!raw) return null;
  tokens = JSON.parse(raw);
  try {
    const t = await token();
    const claims = decode(t);
    return { username: claims.preferred_username as string, roles: (claims.realm_access?.roles || []) as string[] };
  } catch {
    await logout();
    return null;
  }
}

export async function logout() {
  tokens = null;
  await SecureStore.deleteItemAsync(KEY);
}

async function token(): Promise<string> {
  if (!tokens) throw new ApiError(401, "not signed in", null);
  if (tokens.expires_at - Date.now() < 30_000) await tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refresh_token });
  return tokens!.access_token;
}

export async function api<T = any>(path: string, opts: { method?: string; json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${await token()}` };
  let body: string | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(`${config.gateway}/api/${path.replace(/^\//, "")}`, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  const text = await res.text();
  let data: any = text;
  try { data = JSON.parse(text); } catch { /* not JSON */ }
  if (!res.ok) {
    const msg = typeof data === "object" ? data.error || (typeof data.detail === "string" ? data.detail : data.detail?.reason) || JSON.stringify(data) : text;
    throw new ApiError(res.status, msg, data);
  }
  return data as T;
}
