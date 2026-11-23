"use client";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, DemoBanner, ErrorBox, StateBadge, StoryTabs, useLoad } from "@/components/ui";

const RUNNING_STATES = ["RESEARCHING", "RENDERING", "UPLOADING"];

export function useStory() {
  const { id } = useParams<{ id: string }>();
  const r = useLoad<any>(`/api/stories/${id}`, d => (d?.running || RUNNING_STATES.includes(d?.state) ? 2500 : null));
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
      {s.parent_story_id && <p className="small muted">Phiên bản sửa của <a href={`/stories/${s.parent_story_id}`}>bản trước</a>.</p>}
      {s.blockers?.length > 0 && <div className="error"><b>Đang bị chặn:</b><ul>{s.blockers.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul></div>}
      {s.last_error && !s.blockers?.length && s.state.startsWith("FAILED") && <div className="error small">{s.last_error}</div>}
      <ErrorBox error={err} />
      <StoryTabs id={s.id} active={active} />
    </>
  );
}
