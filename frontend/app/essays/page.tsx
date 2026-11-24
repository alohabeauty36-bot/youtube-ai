"use client";
import { Fragment, ReactNode, useCallback, useEffect, useState } from "react";
import { api, API_BASE, errorFromResponse, fmtDate, getToken } from "@/lib/api";
import { Badge, Card, ErrorBox, useLoad } from "@/components/ui";

const STATUS: Record<string, [string, "ok" | "warn" | "bad" | "info"]> = {
  queued: ["Đang chờ", "info"], researching: ["Đang research", "info"], writing: ["Đang viết", "info"],
  done: ["Đã viết xong", "ok"], needs_review: ["Cần xem lại", "warn"], blocked: ["Bị chặn", "warn"], failed: ["Lỗi", "bad"],
  approved: ["Đã duyệt", "ok"], rejected: ["Đã loại", "bad"],
};
const RUNNING = ["queued", "researching", "writing"];
const WRITER: Record<string, string> = {
  anthropic: "Thầy Claude", openai: "Thầy ChatGPT", local: "Trò tự viết (model trên máy)", memory: "Lấy lại từ bộ nhớ", demo: "Bản nháp DEMO",
};
const STYLES: Record<string, string> = { nghi_luan: "Nghị luận", phan_tich: "Phân tích", thuyet_minh: "Thuyết minh", so_sanh: "So sánh" };

function Status({ s }: { s: string }) {
  const [label, kind] = STATUS[s] || [s, "info"];
  return <Badge kind={kind}>{label}</Badge>;
}

// Markdown tối giản: "## tiêu đề" và đoạn văn; [S1] thành chỉ mục nguồn. Không dùng innerHTML.
function withCites(text: string): ReactNode[] {
  return text.split(/(\[S\d+\])/g).map((part, i) => {
    const m = part.match(/^\[(S\d+)\]$/);
    return m ? <a key={i} href={`#src-${m[1]}`} className="cite">[{m[1]}]</a> : <Fragment key={i}>{part}</Fragment>;
  });
}
function Essay({ md }: { md: string }) {
  return <div className="essay">{md.split(/\n\s*\n/).map((b, i) => {
    const t = b.trim();
    if (/^#{2,4}\s/.test(t)) return <h3 key={i}>{t.replace(/^#+\s*/, "")}</h3>;
    return <p key={i}>{withCites(t)}</p>;
  })}</div>;
}

async function download(id: string) {
  const path = `/api/essays/${id}/export.md`;
  const r = await fetch(`${API_BASE}${path}`, { headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {} });
  if (!r.ok) throw await errorFromResponse(r, path);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = `bai-luan-${id.slice(0, 8)}.md`; a.click();
  URL.revokeObjectURL(url);
}

export default function Essays() {
  const list = useLoad<any[]>("/api/essays", d => (d || []).some(e => RUNNING.includes(e.status)) ? 3000 : null);
  const agents = useLoad<any>("/api/agents");
  const [sel, setSel] = useState<string | null>(null);
  const detail = useLoad<any>(sel ? `/api/essays/${sel}` : null, d => d && RUNNING.includes(d.status) ? 3000 : null);
  const [f, setF] = useState({ topic: "", requirements: "", words: 1200, style: "nghi_luan", language: "vi" });
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<any>(null); const [msg, setMsg] = useState("");
  const [note, setNote] = useState(""); const [edit, setEdit] = useState<string | null>(null);

  useEffect(() => { if (!sel && list.data?.length) setSel(list.data[0].id); }, [list.data, sel]);
  useEffect(() => { setEdit(null); setNote(""); }, [sel]);
  const reloadAll = useCallback(async () => { await list.reload(); await detail.reload(); }, [list, detail]);
  const act = async (k: string, fn: () => Promise<any>, ok = "Đã lưu.") => {
    setBusy(k); setErr(null); setMsg("");
    try { await fn(); setMsg(ok); await reloadAll(); } catch (e) { setErr(e); } finally { setBusy(""); }
  };

  const dept = (agents.data?.departments || []).find((d: any) => d.code === "bai_luan");
  const E = detail.data;
  const sources: any[] = E?.dossier?.sources || [];
  const review = (action: string, extra: any = {}) => api(`/api/essays/${E.id}/review`, { method: "POST", json: { action, note, ...extra } });

  return (
    <>
      <h2>Phòng Bài luận</h2>
      <p className="muted small">Nhập chủ đề → phòng tự tìm tư liệu trên mạng (Wikipedia, SearXNG/Brave nếu có; miễn phí) → viết bài luận có trích nguồn.
        Lúc đầu thầy Claude và ChatGPT viết (luân phiên, có trần ngân sách), trò trên máy học theo. Khi phòng đạt <b>Bậc thầy</b>, trò tự viết, không gọi Claude/ChatGPT.</p>
      {dept && <div className="row small">
        <span>Cấp hiện tại: <Badge kind={dept.level >= 4 ? "master" : dept.graduated ? "ok" : "info"}>{dept.level_name}</Badge></span>
        <span>Trò đạt: <b>{dept.pass_rate == null ? "—" : `${Math.round(dept.pass_rate * 100)}%`}</b> ({dept.window} lần gần nhất, {dept.student_passes} lần đạt)</span>
        <span>Thầy đã viết: <b>{dept.teacher_calls}</b> · Trò tự viết thay: <b>{dept.student_live}</b></span>
        {!agents.data?.student?.available && <Badge kind="warn">Chưa có trò trên máy (Ollama) → chưa thể tự học</Badge>}
      </div>}
      <ErrorBox error={list.error || err} />
      {msg && <div className="hint">{msg}</div>}

      <Card title="Viết bài luận mới">
        <div className="grid g2">
          <label>Chủ đề<input value={f.topic} placeholder="Ví dụ: Có nên mua ô tô điện ở Việt Nam lúc này?" onChange={e => setF({ ...f, topic: e.target.value })} /></label>
          <div className="row">
            <label style={{ flex: 1 }}>Kiểu bài<select value={f.style} onChange={e => setF({ ...f, style: e.target.value })}>
              {Object.entries(STYLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label style={{ width: 120 }}>Số từ<input type="number" min={300} max={3000} step={100} value={f.words} onChange={e => setF({ ...f, words: Number(e.target.value) })} /></label>
            <label style={{ width: 120 }}>Ngôn ngữ<select value={f.language} onChange={e => setF({ ...f, language: e.target.value })}>
              <option value="vi">Tiếng Việt</option><option value="en">English</option></select></label>
          </div>
        </div>
        <label>Yêu cầu thêm (tuỳ chọn)<input value={f.requirements} placeholder="Ví dụ: tập trung chi phí sở hữu 5 năm, giọng trung lập, cho người mua lần đầu"
          onChange={e => setF({ ...f, requirements: e.target.value })} /></label>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="primary" disabled={!!busy || f.topic.trim().length < 3} onClick={() => act("new", async () => {
            const r = await api("/api/essays", { method: "POST", json: f }); setSel(r.id); setF({ ...f, topic: "", requirements: "" });
          }, "Đã nhận đề. Phòng đang tìm tư liệu và viết, trang tự cập nhật.")}>{busy === "new" ? "Đang gửi…" : "Viết bài luận"}</button>
          <span className="small muted">Mỗi bài do thầy Claude viết tốn khoảng 0,05–0,10 USD, nằm trong trần 10% ngân sách tháng của phòng. Trò tự viết: $0.</span>
        </div>
      </Card>

      <div className="grid" style={{ gridTemplateColumns: "minmax(240px, 1fr) 3fr" }}>
        <Card title="Các bài đã viết">
          {!list.data?.length && <p className="small muted">Chưa có bài nào.</p>}
          <ul className="essay-list">{(list.data || []).map(e => (
            <li key={e.id} className={e.id === sel ? "on" : ""} onClick={() => setSel(e.id)}>
              <div>{e.title || e.topic}</div>
              {e.title && e.title !== e.topic && <div className="small muted">Đề: {e.topic}</div>}
              <div className="row small"><Status s={e.status} />{e.source && <span className="muted">{WRITER[e.source] || e.source}</span>}
                <span className="muted">{fmtDate(e.created_at)}</span></div>
            </li>))}</ul>
        </Card>

        {E ? <Card title={<>{E.title || E.topic} {E.is_demo && <Badge kind="demo">DEMO</Badge>}</>} right={<Status s={E.status} />}>
          <div className="row small muted">
            <span>{STYLES[E.style] || E.style} · {E.words} từ yêu cầu{E.words_written ? ` · viết ${E.words_written} từ` : ""}</span>
            {E.source && <span>Người viết: <b>{WRITER[E.source] || E.source}</b>{E.model ? ` (${E.model})` : ""}</span>}
            <span>Chi phí: <b>${(E.usd || 0).toFixed(4)}</b></span>
            {E.qa?.metrics && <span>Bám nguồn: <b>{Math.round((E.qa.metrics.grounding || 0) * 100)}%</b> · {E.qa.metrics.distinct_sources}/{E.qa.metrics.sources_available} nguồn được dùng</span>}
          </div>
          {RUNNING.includes(E.status) && <div className="hint">{E.stage_note || "Đang xử lý…"}</div>}
          {E.error && <div className="error">{E.error}</div>}
          {E.qa?.problems?.length > 0 && <div className="error"><b>Chưa qua QA:</b><ul>{E.qa.problems.map((p: string, i: number) => <li key={i}>{p}</li>)}</ul></div>}
          {E.qa?.warnings?.length > 0 && <div className="hint"><ul style={{ margin: 0 }}>{E.qa.warnings.map((p: string, i: number) => <li key={i}>{p}</li>)}</ul></div>}
          {E.content?.thesis && <p><b>Luận điểm:</b> {E.content.thesis}</p>}

          {E.markdown && (edit == null ? <Essay md={E.markdown} /> :
            <textarea value={edit} onChange={e => setEdit(e.target.value)} />)}

          {E.markdown && <div className="row" style={{ marginTop: 10 }}>
            {edit == null ? <>
              <button className="primary" disabled={!!busy || E.status === "approved"} onClick={() => act("ok", () => review("approve"), "Đã duyệt. Bài này thành bài mẫu cho phòng học theo.")}>Duyệt</button>
              <button disabled={!!busy} onClick={() => setEdit(E.markdown)}>Sửa rồi duyệt</button>
            </> : <>
              <button className="primary" disabled={!!busy} onClick={() => act("ok", async () => { await review("approve", { markdown: edit }); setEdit(null); },
                "Đã lưu bản sửa và duyệt. Trò sẽ học theo bản của bạn.")}>Lưu bản sửa & duyệt</button>
              <button disabled={!!busy} onClick={() => setEdit(null)}>Huỷ sửa</button>
            </>}
            <button disabled={!!busy} onClick={() => act("dl", () => download(E.id), "Đã tải file .md")}>Tải về (.md)</button>
            <button disabled={!!busy || RUNNING.includes(E.status)} onClick={() => act("re", () => api(`/api/essays/${E.id}/rerun`, { method: "POST" }), "Đang research và viết lại.")}>Viết lại</button>
          </div>}
          {E.markdown && <div className="row" style={{ marginTop: 6 }}>
            <input style={{ flex: 1 }} value={note} placeholder="Góp ý cho phòng (ví dụ: mở bài dài quá, cần thêm ví dụ thực tế)…" onChange={e => setNote(e.target.value)} />
            <button disabled={!!busy || !note.trim()} onClick={() => act("fb", async () => { await review("feedback"); setNote(""); }, "Đã ghi thành bài học cho phòng.")}>Gửi góp ý</button>
            <button disabled={!!busy || E.status === "rejected"} onClick={() => act("rj", async () => { await review("reject"); setNote(""); }, "Đã loại. Phòng sẽ không dùng bài này làm mẫu.")}>Loại bài</button>
          </div>}

          {sources.length > 0 && <>
            <h4>Nguồn tư liệu ({sources.length})</h4>
            <ol className="sources">{sources.map(s => <li key={s.id} id={`src-${s.id}`}>
              <b>[{s.id}]</b> <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> <span className="small muted">— {s.publisher}{s.license ? ` · ${s.license}` : ""}</span>
              <details><summary className="small">Đoạn trích đã dùng ({s.passages.length})</summary>
                <ul className="small">{s.passages.map((p: string, i: number) => <li key={i}>{p}</li>)}</ul></details>
            </li>)}</ol>
            {E.dossier?.queries?.length > 0 && <p className="small muted">Truy vấn đã tìm: {E.dossier.queries.join(" · ")}</p>}
          </>}
        </Card> : <Card><p className="muted">Chọn một bài ở cột bên trái, hoặc viết bài mới.</p></Card>}
      </div>
    </>
  );
}
