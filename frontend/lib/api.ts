// Client API: token lưu ở localStorage của trình duyệt chủ kênh; không bao giờ gửi secret dịch vụ ra frontend.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  try { return localStorage.getItem("evcs_token") || ""; } catch { return ""; }
}
export function setToken(t: string) {
  try { localStorage.setItem("evcs_token", t); } catch { /* bỏ qua */ }
}

export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail));
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: any } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> || {}) };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  let body = init.body;
  if (init.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(init.json); }
  const r = await fetch(`${API_BASE}${path}`, { ...init, headers, body, cache: "no-store" });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(r.status, (data as any)?.detail ?? data);
  return data as T;
}

export const mediaUrl = (u?: string | null) => (u ? `${API_BASE}${u}` : "");

export function fmtTime(s?: number | null) {
  if (s == null) return "—";
  const m = Math.floor(s / 60), ss = Math.round(s % 60);
  return `${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}
export function fmtDate(s?: string | null) {
  if (!s) return "—";
  try { return new Date(s).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }); } catch { return s; }
}
