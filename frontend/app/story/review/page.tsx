"use client";
import { Suspense, useState } from "react";
import { api, fmtDate, fmtTime, mediaUrl } from "@/lib/api";
import { Badge, Card, ErrorBox } from "@/components/ui";
import { StoryHeader, useStory } from "@/components/story";

function Review() {
  const { data: s, error, reload } = useStory();
  const [err, setErr] = useState<any>(null); const [busy, setBusy] = useState(""); const [confirm, setConfirm] = useState(false);
  const r = s?.render; const seo = s?.seo; const up = s?.uploads?.[0];
  const call = async (k: string, fn: () => Promise<any>) => { setBusy(k); setErr(null); try { await fn(); await reload(); } catch (e) { setErr(e); } finally { setBusy(""); } };
  const canUpload = s?.state === "READY_TO_UPLOAD";
  const reviewable = ["UPLOADED_PRIVATE", "TELEGRAM_NOTIFIED", "EDITOR_REVIEW", "APPROVED_PRIVATE"].includes(s?.state);
  return (
    <>
      <ErrorBox error={error} />
      <StoryHeader s={s} active="review" reload={reload} />
      <ErrorBox error={err} />
      {s && <div className="grid g2">
        <div>
          <Card title="Xem trước" right={r && <Badge kind={r.qa_passed ? "ok" : "bad"}>QA {r.qa?.summary}</Badge>}>
            {r ? <video controls src={mediaUrl(r.preview_url)} style={{ width: "100%", borderRadius: 8 }} /> : <p className="muted">Chưa dựng.</p>}
            {r && <p className="small muted">{r.width}×{r.height} · {fmtTime(r.duration)} · SHA-256 {r.sha256?.slice(0, 16)}… · <a href={mediaUrl(r.video_url)}>tải MP4 1080p</a> · <a href={mediaUrl(r.srt_url)}>phụ đề .srt</a></p>}
          </Card>
          {r && <Card title="Media QA">{r.qa?.checks?.map((c: any, i: number) => <div key={i} className="check"><span className="i">{c.ok ? "✅" : "⛔"}</span><span>{c.name} <span className="muted small">{c.detail}</span></span></div>)}</Card>}
          {r && <Card title="Thumbnail (1280×720)"><div className="thumbs">{r.thumbnails.map((t: string, i: number) => <img key={i} src={mediaUrl(t)} alt={`thumbnail ${i}`} />)}</div></Card>}
        </div>
        <div>
          <Card title="SEO">
            {seo ? <>
              {seo.title_options.map((t: any, i: number) => <div key={i} className="row small"><span>{i === seo.selected_index ? "✅" : "▫️"}</span>{t.title}<Badge>{t.strategy}</Badge></div>)}
              <pre className="small">{seo.description}</pre>
              <div className="small">Tags: {seo.tags.join(", ")}</div>
            </> : <p className="muted">Chưa có.</p>}
          </Card>
          <Card title="YouTube — RIÊNG TƯ" right={<Badge kind="ok">privacyStatus = private (cố định)</Badge>}>
            <p className="small">Kênh đích: <b>{s.channel?.title || "chưa chọn"}</b> {s.channel?.is_demo && <Badge kind="demo">DEMO</Badge>}</p>
            {!up && <>
              <label className="row small"><input type="checkbox" style={{ width: "auto" }} checked={confirm} onChange={e => setConfirm(e.target.checked)} />
                Tôi xác nhận tải lên ở trạng thái <b>RIÊNG TƯ</b> vào kênh trên. Việc công khai tôi sẽ tự làm trên YouTube Studio.</label>
              <button className="primary" style={{ marginTop: 8 }} disabled={!canUpload || !confirm || !!busy}
                onClick={() => call("up", () => api(`/api/stories/${s.id}/upload-private`, { method: "POST", json: { confirm_private: true } }))}>
                {busy === "up" ? "Đang gửi…" : "Tải lên RIÊNG TƯ"}</button>
              {!canUpload && <p className="small muted">Chỉ bật khi trạng thái là “Sẵn sàng tải RIÊNG TƯ” (QA đạt, claim đã xác minh, asset có giấy phép).</p>}
            </>}
            {up && <table><tbody>
              <tr><td>Video ID</td><td>{up.youtube_video_id || "—"} {up.is_demo && <Badge kind="demo">DEMO</Badge>}</td></tr>
              <tr><td>Yêu cầu</td><td>{up.privacy_requested}</td></tr>
              <tr><td>Đọc lại từ YouTube</td><td><Badge kind={up.privacy_observed === "private" ? "ok" : "warn"}>{up.privacy_observed || "—"}</Badge> {fmtDate(up.verified_at)}</td></tr>
              <tr><td>Lần thử</td><td>{up.attempts} {up.error && <span className="small muted">{up.error}</span>}</td></tr>
              <tr><td>Studio</td><td>{up.studio_url && <a href={up.studio_url} target="_blank" rel="noreferrer">Mở YouTube Studio ↗</a>}</td></tr>
            </tbody></table>}
            <button style={{ marginTop: 8 }} disabled={!up} onClick={() => call("rf", () => api(`/api/stories/${s.id}/refresh-status`, { method: "POST" }))}>Kiểm tra lại trạng thái trên YouTube</button>
          </Card>
          <Card title="Kiểm duyệt của chủ kênh">
            <p className="small muted">“Đã kiểm duyệt” chỉ ghi nhận trong hệ thống — video VẪN RIÊNG TƯ. “Yêu cầu sửa” tạo phiên bản mới; bản cũ không bị xoá hay công khai.</p>
            <div className="row">
              <button className="primary" disabled={!reviewable || s.state === "APPROVED_PRIVATE" || !!busy} onClick={() => call("ap", () => api(`/api/stories/${s.id}/review`, { method: "POST", json: { action: "approve" } }))}>Đã kiểm duyệt</button>
              <button disabled={!reviewable || !!busy} onClick={() => { const note = prompt("Cần sửa gì?"); if (note) call("rv", () => api(`/api/stories/${s.id}/review`, { method: "POST", json: { action: "request_revision", note } })); }}>Yêu cầu sửa</button>
              <button className="danger" disabled={!reviewable || !!busy} onClick={() => { if (window.confirm("Loại video này khỏi hàng đợi? (Không xoá trên YouTube)")) call("rj", () => api(`/api/stories/${s.id}/review`, { method: "POST", json: { action: "reject" } })); }}>Loại</button>
            </div>
            <ul className="small">{s.reviews.map((e: any, i: number) => <li key={i}>{fmtDate(e.created_at)} · {e.actor} ({e.via}) · {e.action} {e.note} {e.new_story_id && <a href={`/story/?id=${e.new_story_id}`}>→ bản mới</a>}</li>)}</ul>
          </Card>
          <Card title="Telegram">
            {s.telegram.length ? s.telegram.map((m: any, i: number) => <div key={i} className="small">{m.chat_id}: <Badge kind={m.status === "sent" ? "ok" : "bad"}>{m.status}</Badge> {m.is_demo && <Badge kind="demo">DEMO</Badge>} lần {m.attempts} {m.error}</div>) : <p className="small muted">Chưa gửi.</p>}
          </Card>
          <Card title="Lịch sử job & retry">
            <table><tbody>{s.jobs.map((j: any) => <tr key={j.id}><td className="small">{j.job_type}</td><td><Badge kind={j.status === "succeeded" ? "ok" : j.status === "blocked" ? "warn" : "bad"}>{j.status}</Badge></td>
              <td className="small">{j.attempts} lần · {j.duration_seconds ?? "—"}s</td><td className="small muted">{j.error}</td></tr>)}</tbody></table>
          </Card>
        </div>
      </div>}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="muted">Đang tải…</p>}><Review /></Suspense>;
}
