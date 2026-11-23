"use client";
import { useState } from "react";
import { api, fmtTime, mediaUrl } from "@/lib/api";
import { Badge, Card, ErrorBox } from "@/components/ui";
import { StoryHeader, useStory } from "@/components/story";

const COLORS: Record<string, string> = { title: "#2ecc71", fact: "#3498db", formula: "#9b59b6", timeline: "#f1c40f", pros_cons: "#e67e22",
  question: "#1abc9c", photo: "#ecf0f1", narration: "#7f8c8d", credits: "#95a5a6" };

export default function Media() {
  const { data: s, error, reload } = useStory();
  const [err, setErr] = useState<any>(null);
  const total = s?.scenes?.length ? s.scenes[s.scenes.length - 1].end : 0;
  const review = async (a: any, status: string) => {
    setErr(null);
    const body: any = { review_status: status };
    if (status === "approved" && !a.license?.name) {
      body.license_name = prompt("Tên giấy phép (vd: Owner-provided, CC BY 4.0):") || undefined;
      body.permission_proof = prompt("Bằng chứng quyền sử dụng (hợp đồng/email/URL):") || undefined;
      body.attribution_text = prompt("Ghi công (nếu giấy phép yêu cầu):") || undefined;
    } else if (status === "rejected") body.note = prompt("Lý do loại:") || "";
    try { await api(`/api/assets/${a.id}`, { method: "PATCH", json: body }); reload(); } catch (e) { setErr(e); }
  };
  return (
    <>
      <ErrorBox error={error} />
      <StoryHeader s={s} active="media" reload={reload} />
      <ErrorBox error={err} />
      {s && <>
        <Card title={`Timeline (${s.scenes?.length || 0} cảnh · ${fmtTime(total)})`} right={<button onClick={async () => { try { await api(`/api/stories/${s.id}/render`, { method: "POST" }); reload(); } catch (e) { setErr(e); } }}>Dựng lại</button>}>
          <div className="timeline">{(s.scenes || []).map((c: any) => <div key={c.idx} title={`${c.visual_type} ${fmtTime(c.start)}–${fmtTime(c.end)}`}
            style={{ left: `${(c.start / total) * 100}%`, width: `${((c.end - c.start) / total) * 100}%`, background: COLORS[c.visual_type] || "#555" }}>{c.visual_type}</div>)}</div>
          <div className="row small" style={{ marginTop: 6 }}>{Object.entries(COLORS).map(([k, v]) => <span key={k}><span style={{ display: "inline-block", width: 10, height: 10, background: v, borderRadius: 2 }} /> {k}</span>)}</div>
        </Card>
        <Card title="Cảnh">
          <div className="thumbs">{(s.scenes || []).map((c: any) => <div key={c.idx}><img src={mediaUrl(c.image_url)} alt={c.caption} loading="lazy" />
            <div className="small"><b>#{c.idx}</b> {fmtTime(c.start)}–{fmtTime(c.end)} <Badge>{c.visual_type}</Badge> {c.asset_ids.length ? <Badge kind="ok">asset</Badge> : <Badge>đồ họa nội bộ</Badge>}
              {c.claim_ids.map((x: string) => <Badge key={x} kind="info">{x}</Badge>)}</div></div>)}</div>
        </Card>
        <Card title="Tư liệu & giấy phép" right={<button onClick={async () => { try { await api(`/api/stories/${s.id}/assets/search`, { method: "POST" }); reload(); } catch (e) { setErr(e); } }}>Tìm tư liệu</button>}>
          <table><thead><tr><th>Nguồn</th><th>Mô tả</th><th>Giấy phép</th><th>Trạng thái</th><th></th></tr></thead><tbody>
            {s.assets.map((a: any) => <tr key={a.id}><td>{a.provider} {a.is_demo && <Badge kind="demo">DEMO</Badge>}<div className="small muted">{a.creator}</div></td>
              <td className="small">{a.scene_description} {a.is_illustration && <Badge kind="warn">minh họa</Badge>}</td>
              <td className="small">{a.license?.name || <span style={{ color: "#ff9c90" }}>THIẾU</span>}<div className="muted">{a.license?.attribution}</div></td>
              <td><Badge kind={a.review_status === "approved" ? "ok" : a.review_status === "rejected" ? "bad" : "warn"}>{a.review_status}</Badge><div className="small muted">{a.rejection_reason}</div></td>
              <td className="row"><button onClick={() => review(a, "approved")}>Duyệt</button><button onClick={() => review(a, "rejected")}>Loại</button></td></tr>)}
          </tbody></table>
          <p className="small muted">Ảnh tìm từ web/Google chỉ là tham chiếu, không bao giờ được tải tự động. Asset thiếu bằng chứng quyền sử dụng bị loại và thay bằng đồ họa tự dựng.</p>
        </Card>
      </>}
    </>
  );
}
