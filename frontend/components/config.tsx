"use client";
import { useEffect, useState } from "react";
import { api, mediaUrl } from "@/lib/api";
import { Badge, Card, ErrorBox } from "@/components/ui";

type Field = { key: string; label: string; group: string; kind: string; options: string[]; help: string; placeholder: string;
  restart: boolean; source: string; value?: any; is_set?: boolean; masked?: string | null };
type Cfg = { groups: Record<string, string>; fields: Field[]; oauth_redirect_uri: string; local_access?: boolean };

const TEST_FOR: Record<string, string> = { anthropic: "anthropic", tts: "tts", youtube: "youtube", telegram: "telegram", media: "pexels", ai_images: "ai_images" };
const SOURCE: Record<string, [string, any]> = { ui: ["đã lưu trong giao diện", "ok"], env: ["từ file .env", "info"], default: ["mặc định", "info"] };
type DemoSummary = { stories: number; news: number; sources: number; channels: number; essays: number; cost_rows: number;
  running_stories: number; has_files: boolean; total: number };
const DEMO_LABELS: [keyof DemoSummary, string][] = [["stories", "video"], ["news", "tin"], ["sources", "nguồn tin"], ["channels", "kênh"],
  ["essays", "bài luận"], ["cost_rows", "dòng chi phí ước tính"]];

export function ConfigPanel({ onChanged }: { onChanged?: () => void } = {}) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [tests, setTests] = useState<Record<string, any>>({});
  const [notice, setNotice] = useState("");
  const [purge, setPurge] = useState<DemoSummary | null>(null);
  const load = async () => { try { setCfg(await api<Cfg>("/api/config")); } catch (e) { setErr(e); } };
  useEffect(() => { load(); }, []);
  if (!cfg) return <ErrorBox error={err} />;

  const set = (k: string, v: any) => setEdits(e => ({ ...e, [k]: v }));
  const fieldsOf = (g: string) => cfg.fields.filter(f => f.group === g);
  const curMode = cfg.fields.find(f => f.key === "app_mode")?.value;
  const toProduction = "app_mode" in edits && edits.app_mode === "production" && curMode !== "production";
  const save = async (g: string, clear: string[] = [], confirmed = false) => {
    const values: Record<string, any> = {};
    fieldsOf(g).forEach(f => { if (f.key in edits) values[f.key] = edits[f.key]; });
    setBusy(g); setErr(null); setNotice("");
    try {
      if (values.app_mode === "production" && curMode !== "production" && !confirmed) {
        // Chạy thật = xoá sạch dữ liệu DEMO → hỏi lại trước khi lưu
        const sum = await api<DemoSummary>("/api/demo-data");
        if (sum.total > 0) { setPurge(sum); return; }
      }
      const r = await api<any>("/api/config", { method: "PUT", json: { values, clear } });
      setCfg({ ...cfg, ...r }); setPurge(null);
      setEdits(e => { const n = { ...e }; Object.keys(values).forEach(k => delete n[k]); return n; });
      const p = r.demo_purged;
      setNotice(`Đã lưu ${[...r.updated, ...r.cleared].length} mục.` + (r.restart_needed.length ? ` Cần khởi động lại ứng dụng để áp dụng: ${r.restart_needed.join(", ")}.` : "")
        + (p ? ` Đã chuyển sang chạy thật và xoá ${p.total} mục dữ liệu DEMO.` + (p.autopilot_reset ? " Autopilot đã được tắt, bật lại ở mục Tự động hoá khi sẵn sàng." : "") : ""));
      if (p || "app_mode" in values) onChanged?.();
    } catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const test = async (g: string, send = false) => {
    setBusy("t" + g);
    try { const r = await api(`/api/config/test/${TEST_FOR[g]}${send ? "?send_test=true" : ""}`, { method: "POST" }); setTests(t => ({ ...t, [g]: r })); }
    catch (e) { setTests(t => ({ ...t, [g]: { ok: false, message: String((e as any).message) } })); } finally { setBusy(""); }
  };
  const fieldValue = (f: Field) => (f.key in edits ? edits[f.key] : f.kind.includes("secret") ? "" : f.value ?? "");

  const input = (f: Field) => {
    const v = fieldValue(f);
    if (f.kind === "bool") return <input type="checkbox" style={{ width: "auto" }} checked={!!v} onChange={e => set(f.key, e.target.checked)} />;
    if (f.kind === "select") return <select value={v} onChange={e => set(f.key, e.target.value)}>{f.options.map(o => <option key={o} value={o}>{o || "(tự chọn)"}</option>)}</select>;
    if (f.kind === "textarea_secret") return <>
      <textarea style={{ minHeight: 90, fontFamily: "monospace", fontSize: 12 }} value={v} placeholder={f.is_set ? "Đã lưu — dán JSON mới để thay" : '{"web": {"client_id": "…", "client_secret": "…"}}'}
        onChange={e => set(f.key, e.target.value)} />
      <input type="file" accept=".json,application/json" style={{ marginTop: 6 }} onChange={async e => { const file = e.target.files?.[0]; if (file) set(f.key, await file.text()); }} />
    </>;
    return <input type={f.kind === "secret" ? "password" : f.kind === "int" || f.kind === "float" ? "number" : "text"} value={v}
      autoComplete="off" placeholder={f.kind === "secret" ? (f.is_set ? `Đã lưu (${f.masked}) — nhập để thay` : f.placeholder) : f.placeholder}
      onChange={e => set(f.key, e.target.value)} />;
  };

  return (
    <>
      <h3 style={{ marginTop: 0 }}>Cấu hình API & dịch vụ</h3>
      <p className="small muted">Key/token được mã hoá trước khi lưu và không bao giờ hiển thị lại (chỉ 4 ký tự cuối). Giá trị lưu ở đây ghi đè file .env.
        {cfg.local_access && " Bạn đang mở trực tiếp trên máy chạy ứng dụng nên không cần mật khẩu."}</p>
      <ErrorBox error={err} />
      {notice && <div className="news-card small">✅ {notice}</div>}
      <div className="grid g2">
        {Object.entries(cfg.groups).map(([g, title]) => (
          <Card key={g} title={title} right={<div className="row">
            {TEST_FOR[g] && <button disabled={!!busy} onClick={() => test(g)}>{busy === "t" + g ? "Đang kiểm tra…" : "Kiểm tra"}</button>}
            <button className="primary" disabled={!!busy || !fieldsOf(g).some(f => f.key in edits)} onClick={() => save(g)}>{busy === g ? "Đang lưu…" : "Lưu"}</button>
          </div>}>
            {g === "youtube" && <div className="small" style={{ marginBottom: 8 }}>Authorized redirect URI cần thêm vào OAuth client:
              <div className="row"><code style={{ background: "#0d1626", padding: "3px 6px", borderRadius: 6 }}>{cfg.oauth_redirect_uri}</code>
                <button onClick={() => navigator.clipboard?.writeText(cfg.oauth_redirect_uri)}>Sao chép</button></div></div>}
            {g === "mode" && toProduction && !purge && <div className="small" style={{ marginBottom: 8, color: "#ffe17a" }}>
              ⚠️ Lưu chế độ production sẽ xoá toàn bộ dữ liệu DEMO (tin, video, kênh, bài luận giả lập…).</div>}
            {g === "mode" && purge && toProduction && <PurgeConfirm s={purge} busy={busy === g} onCancel={() => setPurge(null)} onConfirm={() => save(g, [], true)} />}
            <table><tbody>{fieldsOf(g).map(f => <tr key={f.key}>
              <td style={{ width: "38%" }}><div>{f.label}</div>
                <div className="row small"><Badge kind={SOURCE[f.source]?.[1]}>{SOURCE[f.source]?.[0]}</Badge>{f.restart && <Badge kind="warn">cần khởi động lại</Badge>}
                  {f.source === "ui" && <a href="#" onClick={e => { e.preventDefault(); save(g, [f.key]); }}>xoá</a>}</div></td>
              <td>{input(f)}{f.help && <div className="small muted" style={{ marginTop: 3 }}>{f.help}</div>}</td></tr>)}</tbody></table>
            {tests[g] && <TestResult g={g} r={tests[g]} onPick={(k, v) => set(k, v)} onSend={() => test(g, true)} current={edits.telegram_allowed_chat_ids ?? cfg.fields.find(f => f.key === "telegram_allowed_chat_ids")?.value} />}
          </Card>))}
      </div>
    </>
  );
}

function PurgeConfirm({ s, busy, onConfirm, onCancel }: { s: DemoSummary; busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  const parts = DEMO_LABELS.filter(([k]) => (s[k] as number) > 0).map(([k, label]) => `${s[k]} ${label}`);
  return (
    <div className="error small" style={{ marginTop: 0, marginBottom: 10 }}>
      <b>Chuyển sang chạy thật sẽ xoá hết dữ liệu DEMO</b>
      <div style={{ marginTop: 4 }}>Sẽ xoá: {parts.join(", ") || "—"}{s.has_files ? ", cùng file video/âm thanh/ảnh DEMO" : ""}. Không hoàn tác được.</div>
      <div className="muted" style={{ marginTop: 4 }}>Giữ nguyên: cấu hình & key đã nhập, sổ cái ngân sách, nhật ký audit, dữ liệu thật.</div>
      {s.running_stories > 0 && <div style={{ marginTop: 4 }}>⏳ Đang có {s.running_stories} video DEMO chạy dở: đợi chạy xong rồi mới chuyển được.</div>}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="danger" disabled={busy} onClick={onConfirm}>{busy ? "Đang xoá…" : "Xoá dữ liệu DEMO và chạy thật"}</button>
        <button disabled={busy} onClick={onCancel}>Huỷ</button>
      </div>
    </div>
  );
}

function TestResult({ g, r, onPick, onSend, current }: { g: string; r: any; onPick: (k: string, v: any) => void; onSend: () => void; current: string }) {
  return (
    <div className={r.ok ? "news-card small" : "error small"} style={{ marginTop: 10 }}>
      {r.ok ? "✅ " : "⛔ "}{r.message}
      {g === "anthropic" && r.models?.length > 0 && <div className="row" style={{ marginTop: 6 }}>Chọn model:
        {r.models.slice(0, 12).map((m: string) => <button key={m} onClick={() => onPick("anthropic_model", m)}>{m}</button>)}</div>}
      {g === "telegram" && r.recent_chats?.length > 0 && <div style={{ marginTop: 6 }}>Chat gần đây (đã nhắn bot):
        {r.recent_chats.map((c: any) => <div key={c.chat_id} className="row">{c.name} — <code>{c.chat_id}</code>
          <button onClick={() => onPick("telegram_allowed_chat_ids", [current, c.chat_id].filter(Boolean).join(","))}>Cho phép</button></div>)}</div>}
      {g === "telegram" && r.ok && <div style={{ marginTop: 6 }}><button onClick={onSend}>Gửi tin nhắn thử tới chat được phép</button>
        {r.sent?.map((x: any) => <div key={x.chat_id}>{x.chat_id}: {x.ok ? "đã gửi" : x.error}</div>)}</div>}
      {g === "youtube" && r.registered && <div>Đang đăng ký: {r.registered.join(", ") || "(trống)"}</div>}
      {g === "tts" && r.audio_url && <div style={{ marginTop: 6 }}><audio controls autoPlay src={mediaUrl(r.audio_url)} style={{ width: "100%" }} />
        {r.qc && <div className="muted">Độ to (RMS) {r.qc.rms} · đỉnh {r.qc.peak} · {r.qc.chars_per_sec} ký tự/giây</div>}</div>}
    </div>
  );
}
