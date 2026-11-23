"use client";
import { useEffect, useState } from "react";
import { api, fmtDate, getToken, setToken } from "@/lib/api";
import { Badge, Card, ErrorBox, useLoad } from "@/components/ui";
import { ConfigPanel } from "@/components/config";

export default function Settings() {
  const [tok, setTok] = useState(""); const [err, setErr] = useState<any>(null);
  useEffect(() => setTok(getToken()), []);
  const integ = useLoad<any>("/api/system/integrations");
  const st = useLoad<any>("/api/settings");
  const ch = useLoad<any[]>("/api/settings/channels");
  const voices = useLoad<any>("/api/voices");
  const src = useLoad<any[]>("/api/sources");
  const act = async (fn: () => Promise<any>, after?: () => void) => { setErr(null); try { await fn(); after?.(); } catch (e) { setErr(e); } };
  const kind = (v: string) => (v === "CONFIGURED" ? "ok" : v === "DEMO" ? "demo" : "bad") as any;
  return (
    <>
      <h2>Thiết lập & tài khoản</h2>
      <ErrorBox error={err || integ.error} />
      <ConfigPanel />
      <h3>Tài khoản & tự động hoá</h3>
      <div className="grid g2">
        <Card title="Đăng nhập dashboard">
          <p className="small muted">Chỉ cần khi mở dashboard từ máy khác. Nhập mật khẩu chủ kênh hoặc biên tập viên (đặt ở mục Bảo mật dashboard). Lưu trong trình duyệt này.</p>
          <div className="row"><input type="password" value={tok} onChange={e => setTok(e.target.value)} placeholder="Bearer token" />
            <button onClick={() => { setToken(tok); location.reload(); }}>Lưu</button></div>
        </Card>
        <Card title="Tích hợp" right={<Badge kind={integ.data?.mode === "demo" ? "demo" : "ok"}>{integ.data?.mode}</Badge>}>
          {integ.data && <table><tbody>
            {["anthropic", "tts", "youtube", "telegram", "pexels"].map(k => <tr key={k}><td>{k}</td><td><Badge kind={kind(integ.data[k])}>{integ.data[k]}</Badge></td></tr>)}
            <tr><td>Model Claude</td><td>{integ.data.anthropic_model}</td></tr><tr><td>TTS</td><td>{integ.data.tts_provider} · giọng {integ.data.voice}</td></tr>
          </tbody></table>}
          <p className="small muted">{integ.data?.privacy_policy}</p>
        </Card>
        <Card title="Kênh YouTube (OAuth)">
          <p className="small muted">Dùng OAuth 2.0 chính thức (không dùng cookie/mật khẩu). Cần dán client_secret.json ở mục YouTube phía trên trước. Khi cấp quyền, chọn đúng kênh; kênh khác ID kênh đích sẽ bị chặn.</p>
          <button onClick={() => act(async () => { const r = await api("/api/oauth/google/start"); location.href = r.authorization_url; })}>Kết nối kênh YouTube</button>
          <table style={{ marginTop: 10 }}><tbody>{(ch.data || []).map(c => <tr key={c.id}><td><b>{c.title}</b> {c.is_demo && <Badge kind="demo">DEMO</Badge>}<div className="small muted">{c.youtube_channel_id} · token {fmtDate(c.token_refreshed_at)}</div></td>
            <td>{c.selected ? <Badge kind="ok">Kênh đích</Badge> : <button onClick={() => act(() => api(`/api/settings/channels/${c.id}/select`, { method: "POST" }), ch.reload)}>Chọn làm kênh đích</button>}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Tự động hoá">
          {st.data && <table><tbody>
            <tr><td>Autopilot Private</td><td><button onClick={() => act(() => api("/api/settings", { method: "PATCH", json: { autopilot_enabled: !st.data.autopilot_enabled } }), st.reload)}>{st.data.autopilot_enabled ? "Đang bật — Tắt" : "Đang tắt — Bật"}</button></td></tr>
            <tr><td>Lịch quét tin</td><td><button onClick={() => act(() => api("/api/settings", { method: "PATCH", json: { scan_schedule_enabled: !st.data.scan_schedule_enabled } }), st.reload)}>{st.data.scan_schedule_enabled ? `Mỗi ${st.data.scan_interval_hours}h — Tắt` : "Đang tắt — Bật"}</button></td></tr>
            <tr><td>Thời lượng mục tiêu</td><td>{st.data.duration_range?.join("–")} giây (đo bằng audio thật)</td></tr>
            <tr><td>Quyền riêng tư</td><td><Badge kind="ok">{st.data.privacy}</Badge></td></tr>
          </tbody></table>}
        </Card>
        <Card title="Giọng đọc VieNeu" right={voices.data?.is_demo && <Badge kind="demo">DEMO TTS</Badge>}>
          {voices.data?.error && <p className="small" style={{ color: "#ff9c90" }}>{voices.data.error}</p>}
          <select value={st.data?.voice || ""} onChange={e => act(() => api("/api/settings", { method: "PATCH", json: { voice: e.target.value } }), st.reload)}>
            {(voices.data?.voices || []).map((v: any) => <option key={v.voice_id} value={v.voice_id}>{v.label}</option>)}</select>
          <p className="small muted">Gợi ý thử “Quang Sơn” rồi so sánh đoạn mẫu 30 giây ở trang Biên tập. Clone giọng chỉ mở khi bạn xác nhận sở hữu/được phép dùng giọng mẫu.</p>
        </Card>
        <Card title="Nguồn tin (allowed / blocked)">
          <table><tbody>{(src.data || []).map(x => <tr key={x.id}><td>{x.name}<div className="small muted">{x.domain} · cấp {x.trust_tier} {x.feed_url}</div></td>
            <td><button onClick={() => act(() => api(`/api/sources/${x.id}`, { method: "PATCH", json: { blocked: !x.blocked } }), src.reload)}>{x.blocked ? "Bỏ chặn" : "Chặn"}</button></td></tr>)}</tbody></table>
          <button style={{ marginTop: 8 }} onClick={() => { const name = prompt("Tên nguồn:"); const feed = prompt("RSS/Atom feed URL (chính thức):"); const domain = feed ? new URL(feed).hostname : "";
            const tier = Number(prompt("Cấp tin cậy (1 chính thức, 2 báo uy tín, 3 cộng đồng):", "2")); if (name && feed) act(() => api("/api/sources", { method: "POST", json: { name, feed_url: feed, domain, trust_tier: tier } }), src.reload); }}>Thêm nguồn RSS</button>
        </Card>
      </div>
    </>
  );
}
