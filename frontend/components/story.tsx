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
        <span className="right"><button onClick={run} disabled={s.running}>Chạy tiếp / thử lại</button></span>
      </div>
      {s.parent_story_id && <p className="small muted">Phiên bản sửa của <a href={`/story/?id=${s.parent_story_id}`}>bản trước</a>.</p>}
      {s.publish_after && ["EDITOR_REVIEW", "APPROVED_PRIVATE"].includes(s.state) && <div className={s.publish_hold ? "error row" : "hint row"}>
        {s.publish_hold ? <span>⛔ Đã dừng tự công khai. Video giữ RIÊNG TƯ.</span>
          : <span>⏰ Tự công khai lúc <b>{new Date(s.publish_after).toLocaleString("vi-VN")}</b> nếu không bấm Dừng.</span>}
        <span className="right"><button onClick={async () => { setErr(null); try { await api(`/api/stories/${s.id}/hold`, { method: "POST", json: { hold: !s.publish_hold } }); reload(); } catch (e) { setErr(e); } }}>
          {s.publish_hold ? "Cho phép tự công khai lại" : "⛔ Dừng tự công khai"}</button></span></div>}
      {s.auto_heal_attempts > 0 && <p className="small muted">Đã tự chạy lại {s.auto_heal_attempts} lần.</p>}
      {s.blockers?.length > 0 && <div className="error"><b>Đang bị chặn:</b><ul>{s.blockers.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
        {s.blockers.some((b: string) => b.includes("BLOCKED_BY_CREDENTIALS")) && <a href="/settings/">→ Mở Thiết lập để nhập/kiểm tra key API, rồi bấm “Chạy tiếp / thử lại”.</a>}</div>}
      {s.last_error && !s.blockers?.length && s.state.startsWith("FAILED") && <div className="error small">{s.last_error}</div>}
      {(s.last_error || s.blockers?.length > 0) && <p className="small"><a href={`/errors/?story_id=${s.id}`}>→ Xem nhật ký lỗi của video này</a></p>}
      <ErrorBox error={err} />
      <StoryTabs id={s.id} active={active} />
    </>
  );
}
