"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Badge, Card, ErrorBox, STATE_LABEL, useLoad } from "@/components/ui";

const PAGE = 50;
const LEVEL: Record<string, { kind: "bad" | "warn"; label: string }> = {
  CRITICAL: { kind: "bad", label: "Nghiêm trọng" }, ERROR: { kind: "bad", label: "Lỗi" }, WARNING: { kind: "warn", label: "Cảnh báo" },
};
const SINCE = [["", "Mọi lúc"], ["1", "1 giờ qua"], ["24", "24 giờ qua"], ["168", "7 ngày qua"]] as const;

function LevelBadge({ level }: { level: string }) {
  const l = LEVEL[level] || { kind: "warn", label: level };
  return <Badge kind={l.kind}>{l.label}</Badge>;
}

function report(d: any): string {
  return [
    `[${d.level}] ${d.source_label} — ${d.exc_type || "log"}`,
    `Lần đầu: ${fmtDate(d.first_seen_at)} · Lần cuối: ${fmtDate(d.last_seen_at)} · Số lần: ${d.count}`,
    d.story ? `Video: ${d.story.topic} (${d.story.id}) · trạng thái ${d.story.state}` : "",
    d.job ? `Job: ${d.job.job_type} · ${d.job.status} · thử ${d.job.attempts}/${d.job.max_attempts}` : "",
    "", d.message, "", d.traceback || "",
  ].filter(x => x !== null).join("\n").trim();
}

function Detail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const { data: d, error, reload } = useLoad<any>(`/api/errors/${id}`);
  const [note, setNote] = useState(""); const [err, setErr] = useState<any>(null); const [copied, setCopied] = useState(false);
  if (error) return <ErrorBox error={error} />;
  if (!d) return <p className="small muted">Đang tải…</p>;
  const resolve = async (resolved: boolean) => {
    setErr(null);
    try { await api(`/api/errors/${id}/resolve`, { method: "POST", json: { resolved, note: note || null } }); setNote(""); await reload(); onChanged(); }
    catch (e) { setErr(e); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(report(d)); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* trình duyệt chặn */ }
  };
  return (
    <div className="err-detail">
      {d.hint && <div className="hint">💡 <b>Gợi ý xử lý:</b> {d.hint}</div>}
      <pre className="err-full">{d.message}</pre>
      <div className="grid g3 small">
        <div><div className="muted">Thời gian</div>Lần đầu {fmtDate(d.first_seen_at)}<br />Lần cuối {fmtDate(d.last_seen_at)}<br />Đã xảy ra <b>{d.count}</b> lần</div>
        <div><div className="muted">Nơi phát sinh</div>{d.source_label}{d.exc_type && <> · <code>{d.exc_type}</code></>}<br /><code className="muted">{d.logger}</code>
          {d.job && <><br />Job <b>{d.job.job_type}</b> · {d.job.status} · thử {d.job.attempts}/{d.job.max_attempts}
            {d.job.duration_seconds != null && <> · {d.job.duration_seconds}s</>}</>}</div>
        <div><div className="muted">Video liên quan</div>
          {d.story ? <><Link href={`/story/?id=${d.story.id}`}>{d.story.topic}</Link><br />{STATE_LABEL[d.story.state] || d.story.state}</> : <span className="muted">Không gắn video</span>}</div>
      </div>
      {d.details && Object.keys(d.details).length > 0 && <details><summary className="small">Dữ liệu kèm theo</summary><pre className="trace">{JSON.stringify(d.details, null, 2)}</pre></details>}
      {d.job?.history?.length > 0 && <details><summary className="small">Lịch sử các lần thử của job ({d.job.history.length})</summary>
        <table><tbody>{d.job.history.map((h: any, i: number) => <tr key={i}><td className="small">#{h.attempt}</td><td className="small">{h.status}</td>
          <td className="small muted">{fmtDate(h.at)}</td><td className="small">{h.error}</td></tr>)}</tbody></table></details>}
      {d.traceback && <details><summary className="small">Traceback (dành cho lập trình viên)</summary><pre className="trace">{d.traceback}</pre></details>}
      <ErrorBox error={err} />
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={copy}>{copied ? "✓ Đã sao chép" : "Sao chép báo cáo lỗi"}</button>
        {d.resolved
          ? <><span className="small muted">Đã xử lý bởi {d.resolved_by} lúc {fmtDate(d.resolved_at)}{d.resolution_note && <> — “{d.resolution_note}”</>}</span>
            <button className="right" onClick={() => resolve(false)}>Mở lại</button></>
          : <><input style={{ flex: 1, minWidth: 200 }} value={note} onChange={e => setNote(e.target.value)} placeholder="Ghi chú cách đã xử lý (tuỳ chọn)" />
            <button className="primary" onClick={() => resolve(true)}>Đánh dấu đã xử lý</button></>}
      </div>
    </div>
  );
}

function ErrorLogPage() {
  const params = useSearchParams(); const router = useRouter();
  const storyId = params.get("story_id") || "";
  const [status, setStatus] = useState("open"); const [level, setLevel] = useState(""); const [source, setSource] = useState("");
  const [since, setSince] = useState(""); const [qInput, setQInput] = useState(""); const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0); const [open, setOpen] = useState<number | null>(null);
  const [auto, setAuto] = useState(true); const [err, setErr] = useState<any>(null); const [msg, setMsg] = useState("");

  useEffect(() => { const t = setTimeout(() => setQ(qInput.trim()), 400); return () => clearTimeout(t); }, [qInput]);
  useEffect(() => { setOffset(0); }, [status, level, source, since, q, storyId]);

  const filter = useMemo(() => ({ ...(level && { level }), ...(source && { source }), ...(q && { q }), ...(storyId && { story_id: storyId }) }),
    [level, source, q, storyId]);
  const qs = new URLSearchParams({ status, limit: String(PAGE), offset: String(offset), ...filter, ...(since && { since_hours: since }) }).toString();
  const { data, error, reload } = useLoad<any>(`/api/errors?${qs}`, () => (auto && open === null ? 10000 : null));
  const sm = data?.summary;
  const act = async (fn: () => Promise<any>, done: (r: any) => string) => {
    setErr(null); setMsg("");
    try { const r = await fn(); setMsg(done(r)); await reload(); } catch (e) { setErr(e); }
  };
  const resolveAll = () => {
    if (!data?.total || !confirm(`Đánh dấu đã xử lý ${data.total} lỗi đang mở khớp bộ lọc hiện tại?`)) return;
    act(() => api("/api/errors/resolve-bulk", { method: "POST", json: filter }), r => `Đã đánh dấu xử lý ${r.resolved} lỗi.`);
  };
  const purge = () => {
    if (!confirm("Xoá vĩnh viễn mọi lỗi ĐÃ XỬ LÝ? Lỗi đang mở được giữ nguyên.")) return;
    act(() => api("/api/errors", { method: "DELETE" }), r => `Đã xoá ${r.deleted} lỗi đã xử lý.`);
  };
  const quickResolve = (id: number, resolved: boolean) => act(() => api(`/api/errors/${id}/resolve`, { method: "POST", json: { resolved } }),
    () => resolved ? "Đã đánh dấu xử lý." : "Đã mở lại.");

  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <h2 style={{ margin: 0 }}>Nhật ký lỗi</h2>
        <span className="right row">
          <label className="row small"><input type="checkbox" style={{ width: "auto" }} checked={auto} onChange={e => setAuto(e.target.checked)} /> Tự làm mới 10s</label>
          <button onClick={reload}>Làm mới</button>
        </span>
      </div>
      <p className="small muted" style={{ marginTop: -6 }}>Mọi cảnh báo và lỗi của tool (quét tin, Claude, giọng đọc, dựng video, YouTube, Telegram, lịch chạy, API) được ghi ở đây.
        Lỗi lặp lại được gộp thành một dòng kèm số lần. Key và token luôn được che trước khi lưu.</p>
      <ErrorBox error={error || err} />
      <div className="grid g4">
        <Card title="Đang mở"><div className="kpi">{sm?.open ?? "—"}</div></Card>
        <Card title="Lỗi nghiêm trọng đang mở"><div className="kpi" style={{ color: sm?.open_critical ? "var(--bad)" : undefined }}>{sm?.open_critical ?? "—"}</div></Card>
        <Card title="Số lần xảy ra 24 giờ qua"><div className="kpi">{sm?.last_24h ?? "—"}</div></Card>
        <Card title="Đã xử lý"><div className="kpi">{sm?.resolved ?? "—"}</div></Card>
      </div>
      {sm?.by_source?.length > 0 && <div className="row" style={{ marginBottom: 12 }}>
        <span className="small muted">Lỗi đang mở theo nguồn:</span>
        {sm.by_source.map((s: any) => <button key={s.source} className={`chip ${source === s.source ? "on" : ""}`}
          onClick={() => setSource(source === s.source ? "" : s.source)}>{s.label} <b>{s.count}</b></button>)}
      </div>}
      <Card>
        <div className="row" style={{ marginBottom: 10 }}>
          <select style={{ width: 140 }} value={status} onChange={e => setStatus(e.target.value)}>
            <option value="open">Đang mở</option><option value="resolved">Đã xử lý</option><option value="all">Tất cả</option></select>
          <select style={{ width: 170 }} value={level} onChange={e => setLevel(e.target.value)}>
            <option value="">Mọi mức độ</option><option value="ERROR,CRITICAL">Lỗi & nghiêm trọng</option><option value="WARNING">Cảnh báo</option></select>
          <select style={{ width: 170 }} value={source} onChange={e => setSource(e.target.value)}>
            <option value="">Mọi nguồn</option>{(data?.sources || []).map((s: any) => <option key={s.source} value={s.source}>{s.label}</option>)}</select>
          <select style={{ width: 130 }} value={since} onChange={e => setSince(e.target.value)}>{SINCE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <input style={{ flex: 1, minWidth: 200 }} value={qInput} onChange={e => setQInput(e.target.value)} placeholder="Tìm theo nội dung, loại lỗi, job, ID video…" />
        </div>
        {storyId && <div className="row small" style={{ marginBottom: 8 }}><Badge>Chỉ video {storyId.slice(0, 8)}</Badge>
          <a href="#" onClick={e => { e.preventDefault(); router.push("/errors/"); }}>Bỏ lọc video</a></div>}
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="small muted">{data ? `${data.total} nhóm lỗi` : "Đang tải…"}</span>
          {msg && <span className="small" style={{ color: "var(--ok)" }}>{msg}</span>}
          <span className="right row">
            {status !== "resolved" && <button disabled={!data?.total} onClick={resolveAll}>Đánh dấu đã xử lý tất cả (theo bộ lọc)</button>}
            <button className="danger" disabled={!sm?.resolved} onClick={purge}>Xoá lỗi đã xử lý</button>
          </span>
        </div>
        <table className="errtable">
          <thead><tr><th style={{ width: 150 }}>Lần cuối</th><th style={{ width: 100 }}>Mức</th><th style={{ width: 130 }}>Nguồn</th><th>Nội dung</th>
            <th style={{ width: 60 }}>Số lần</th><th style={{ width: 120 }}></th></tr></thead>
          <tbody>{(data?.items || []).map((e: any) => [
            <tr key={e.id} className={`err-row ${open === e.id ? "sel" : ""} ${e.resolved ? "done" : ""}`} onClick={() => setOpen(open === e.id ? null : e.id)}>
              <td className="small">{fmtDate(e.last_seen_at)}</td>
              <td><LevelBadge level={e.level} /></td>
              <td className="small">{e.source_label}{e.job_type && <div className="muted">{e.job_type}</div>}</td>
              <td><div className="err-msg">{e.message}</div>
                <div className="small muted meta">{e.exc_type && <code>{e.exc_type}</code>}
                  {e.story_id && <Link href={`/story/?id=${e.story_id}`} onClick={ev => ev.stopPropagation()}>{e.story_topic || `video ${e.story_id.slice(0, 8)}`}</Link>}
                  {e.hint && <span>💡 có gợi ý xử lý</span>}</div></td>
              <td>{e.count > 1 ? <Badge kind="info">×{e.count}</Badge> : <span className="small muted">1</span>}</td>
              <td onClick={ev => ev.stopPropagation()}>
                {e.resolved ? <button className="small" onClick={() => quickResolve(e.id, false)}>Mở lại</button>
                  : <button className="small" onClick={() => quickResolve(e.id, true)}>Đã xử lý</button>}</td>
            </tr>,
            open === e.id && <tr key={`${e.id}-d`} className="err-open"><td colSpan={6}><Detail id={e.id} onChanged={reload} /></td></tr>,
          ])}</tbody>
        </table>
        {data && !data.items.length && <p className="muted" style={{ textAlign: "center", padding: 18 }}>
          {status === "open" && !q && !level && !source && !since ? "✅ Không có lỗi nào đang mở. Tool đang chạy ổn." : "Không có lỗi khớp bộ lọc."}</p>}
        {data && data.total > PAGE && <div className="row" style={{ marginTop: 10 }}>
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Trước</button>
          <span className="small muted">{offset + 1}–{Math.min(offset + PAGE, data.total)} / {data.total}</span>
          <button disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Sau →</button>
        </div>}
      </Card>
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="muted">Đang tải…</p>}><ErrorLogPage /></Suspense>;
}
