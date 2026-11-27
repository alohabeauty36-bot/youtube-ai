"use client";
import Link from "next/link";
import { useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Badge, Card, ErrorBox, StateBadge, useLoad } from "@/components/ui";
import { NextSteps, STOP_STATES } from "@/components/story";

export default function Stories() {
  const { data, error, reload } = useLoad<any[]>("/api/stories", d => (d || []).some((s: any) => s.running) ? 3000 : null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<any>(null);
  const stuck = (data || []).filter(s => STOP_STATES.includes(s.state));
  const runAll = async () => {
    setBusy(true); setErr(null);
    try { for (const s of stuck) if (!s.running) await api(`/api/stories/${s.id}/run`, { method: "POST" }); setTimeout(reload, 800); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const reason = (s: any) => (s.blockers?.[0] || s.last_error || "").replace(/\s+/g, " ");
  return (
    <>
      <h2>Video đang làm</h2>
      <ErrorBox error={error || err} />
      {stuck.length > 0 && <div className="hint row">
        <span><b>{stuck.length} video đang chờ bạn xử lý.</b> Xem lý do ở cột “Lý do / việc cần làm”, xử lý theo gợi ý rồi bấm <b>Chạy lại</b>.
          Bấm vào tên video để xem đầy đủ hướng dẫn.</span>
        <span className="right"><button className="primary" disabled={busy} onClick={runAll}>{busy ? "Đang gửi…" : `Chạy lại cả ${stuck.length} video`}</button></span>
      </div>}
      <Card>
        <table><thead><tr><th>Chủ đề</th><th>Trụ cột</th><th>Chế độ</th><th>Trạng thái</th><th>Lý do / việc cần làm</th><th>Bản</th><th>Cập nhật</th></tr></thead>
          <tbody>{(data || []).map(s => <tr key={s.id}>
            <td><Link href={`/story/?id=${s.id}`}>{s.topic}</Link> {s.is_demo && <Badge kind="demo">DEMO</Badge>} {s.running && <Badge kind="warn">đang chạy…</Badge>}</td>
            <td>{s.pillar}</td><td>{s.mode}</td><td><StateBadge state={s.state} /></td>
            <td style={{ maxWidth: 420 }}>{STOP_STATES.includes(s.state) ? <>
              {reason(s) && <div className="small err-msg" title={reason(s)}>{reason(s)}</div>}
              <NextSteps s={s} reload={reload} compact /></> : <span className="muted small">—</span>}</td>
            <td>v{s.revision}</td><td className="small">{fmtDate(s.updated_at)}</td></tr>)}</tbody></table>
        {!data?.length && <p className="muted">Chưa có video. Chọn một tin ở trang Săn tin.</p>}
      </Card>
    </>
  );
}
