// Client API: token lưu ở localStorage của trình duyệt chủ kênh; không bao giờ gửi secret dịch vụ ra frontend.
// Mặc định cùng địa chỉ với backend (giao diện được backend phục vụ). Khi dev bằng `next dev`: NEXT_PUBLIC_API_BASE=http://localhost:8010
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

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
  // DB lưu giờ UTC; SQLite trả về chuỗi không kèm múi giờ → coi là UTC (nếu không trình duyệt hiểu nhầm là giờ máy, lệch 7 tiếng)
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(s) || !s.includes("T") ? s : `${s}Z`;
  try { return new Date(iso).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }); } catch { return s; }
}
