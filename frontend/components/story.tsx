"use client";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, DemoBanner, ErrorBox, StateBadge, StoryTabs, useLoad } from "@/components/ui";

const RUNNING_STATES = ["RESEARCHING", "RENDERING", "UPLOADING"];

export function useStory() {
  const id = useSearchParams().get("id") || "";
  const r = useLoad<any>(id ? `/api/stories/${id}` : null, d => (d?.running || RUNNING_STATES.includes(d?.state) ? 2500 : null));
  return { id, ...r };
}

export const STOP_STATES = ["NEEDS_REVIEW", "FAILED_RETRYABLE", "FAILED_PERMANENT"];

/** Việc cần làm cho video đang dừng: lời hướng dẫn + nút bấm (chạy lại, mở đúng tab Thiết lập, mở Biên tập, bỏ video). */
export function NextSteps({ s, reload, compact = false }: { s: any; reload: () => void; compact?: boolean }) {
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<any>(null);
  const steps: any[] = s?.next_steps || [];
  if (!steps.length) return null;
  const act = async (st: any) => {
    setErr(null);
    if (st.action === "settings") { location.href = `/settings/#${st.tab || "mode"}`; return; }
    if (st.action === "editor") { location.href = `/story/?id=${s.id}`; return; }
    if (st.action === "dismiss" && !confirm(`Bỏ video "${s.topic}"? Tin này sẽ không được chọn lại.`)) return;
    setBusy(st.action);
    try {
      await api(`/api/stories/${s.id}/${st.action === "dismiss" ? "dismiss" : "run"}`, { method: "POST" });
      setTimeout(reload, 600);
    } catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const btn = (st: any, i: number) => <button key={i} className={st.action === "run" ? "primary small" : st.action === "dismiss" ? "danger small" : "small"}
    disabled={!!busy || s.running} onClick={() => act(st)}>{busy === st.action ? "Đang gửi…" : st.label}</button>;
  if (compact) {
    const uniq = steps.filter((st, i) => steps.findIndex(x => x.action === st.action && x.tab === st.tab) === i);
    return <div className="row">{uniq.map(btn)}<ErrorBox error={err} /></div>;
  }
  return (
    <div className="hint">
      <b>Việc cần làm</b>
      <ol style={{ margin: "6px 0 4px", paddingLeft: 20 }}>{steps.map((st, i) => <li key={i} style={{ marginBottom: 6 }}>{st.text} {btn(st, i)}</li>)}</ol>
      <ErrorBox error={err} />
    </div>
  );
}

export function StoryHeader({ s, active, reload }: { s: any; active: "editor" | "media" | "review"; reload: () => void }) {
  const [err, setErr] = useState<any>(null);
  if (!s) return null;
  const run = async () => { setErr(null); try { await api(`/api/stories/${s.id}/run`, { method: "POST" }); setTimeout(reload, 500); } catch (e) { setErr(e); } };
  return (
    <>
      <DemoBanner show={s.is_demo} />
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>{s.topic}</h2>
        <Badge>{s.pillar}</Badge><Badge>{s.mode}</Badge><StateBadge state={s.state} /><Badge>v{s.revision}</Badge>
        {s.running && <Badge kind="warn">đang xử lý…</Badge>}
        <span className="right"><button className={STOP_STATES.includes(s.state) ? "primary" : ""} onClick={run} disabled={s.running}>Chạy tiếp / thử lại</button></span>
      </div>
      {s.parent_story_id && <p className="small muted">Phiên bản sửa của <a href={`/story/?id=${s.parent_story_id}`}>bản trước</a>.</p>}
      {s.publish_after && ["EDITOR_REVIEW", "APPROVED_PRIVATE"].includes(s.state) && <div className={s.publish_hold ? "error row" : "hint row"}>
        {s.publish_hold ? <span>⛔ Đã dừng tự công khai. Video giữ RIÊNG TƯ.</span>
          : <span>⏰ Tự công khai lúc <b>{new Date(s.publish_after).toLocaleString("vi-VN")}</b> nếu không bấm Dừng.</span>}
        <span className="right"><button onClick={async () => { setErr(null); try { await api(`/api/stories/${s.id}/hold`, { method: "POST", json: { hold: !s.publish_hold } }); reload(); } catch (e) { setErr(e); } }}>
          {s.publish_hold ? "Cho phép tự công khai lại" : "⛔ Dừng tự công khai"}</button></span></div>}
      {s.auto_heal_attempts > 0 && <p className="small muted">Đã tự chạy lại {s.auto_heal_attempts} lần.</p>}
      {STOP_STATES.includes(s.state) && <NextSteps s={s} reload={reload} />}
      {s.blockers?.length > 0 && <div className="error"><b>Đang bị chặn:</b><ul>{s.blockers.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
        {s.blockers.some((b: string) => b.includes("BLOCKED_BY_CREDENTIALS")) && <a href="/settings/">→ Mở Thiết lập để nhập/kiểm tra key API, rồi bấm “Chạy tiếp / thử lại”.</a>}</div>}
      {s.last_error && !s.blockers?.length && s.state.startsWith("FAILED") && <div className="error small">{s.last_error}</div>}
      {(s.last_error || s.blockers?.length > 0) && <p className="small"><a href={`/errors/?story_id=${s.id}`}>→ Xem nhật ký lỗi của video này</a></p>}
      <ErrorBox error={err} />
      <StoryTabs id={s.id} active={active} />
    </>
  );
}
