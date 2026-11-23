"use client";
import { Suspense, useState } from "react";
import { api, fmtTime, mediaUrl } from "@/lib/api";
import { Badge, Card, ErrorBox } from "@/components/ui";
import { StoryHeader, useStory } from "@/components/story";

const COLORS: Record<string, string> = { title: "#2ecc71", fact: "#3498db", formula: "#9b59b6", timeline: "#f1c40f", pros_cons: "#e67e22",
  question: "#1abc9c", photo: "#ecf0f1", narration: "#7f8c8d", credits: "#95a5a6" };

function Media() {
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
            <div className="small"><b>#{c.idx}</b> {fmtTime(c.start)}–{fmtTime(c.end)} {c.paragraph_idx != null && <span className="muted">đoạn {c.paragraph_idx + 1}</span>} <Badge>{c.visual_type}</Badge> {c.asset_ids.length ? (c.ai_generated ? <Badge kind="warn">ảnh AI</Badge> : <Badge kind="ok">asset</Badge>) : <Badge>đồ họa nội bộ</Badge>}
              {c.claim_ids.map((x: string) => <Badge key={x} kind="info">{x}</Badge>)}</div></div>)}</div>
        </Card>
        <Card title="Tư liệu & giấy phép" right={<button onClick={async () => { try { await api(`/api/stories/${s.id}/assets/search`, { method: "POST" }); reload(); } catch (e) { setErr(e); } }}>Tìm ảnh cho từng đoạn</button>}>
          <table><thead><tr><th>Đoạn</th><th>Nguồn</th><th>Mô tả</th><th>Giấy phép</th><th>Trạng thái</th><th></th></tr></thead><tbody>
            {s.assets.filter((a: any) => a.current !== false).map((a: any) => <tr key={a.id}>
              <td className="small">{a.paragraph_idx != null ? `#${a.paragraph_idx + 1}` : "dùng chung"}{a.search_query && <div className="muted" title={a.search_query}>“{a.search_query.slice(0, 40)}”</div>}</td>
              <td>{a.provider} {a.is_demo && <Badge kind="demo">DEMO</Badge>} {a.is_ai_generated && <Badge kind="warn">AI</Badge>}<div className="small muted">{a.creator}</div>
                {a.source_url && <div className="small"><a href={a.source_url} target="_blank" rel="noreferrer">trang gốc</a></div>}</td>
              <td className="small">{a.scene_description} {a.is_illustration && <Badge kind="warn">minh họa</Badge>} {a.model_name && <Badge kind="info">{a.model_name}</Badge>}</td>
              <td className="small">{a.license?.name || <span style={{ color: "#ff9c90" }}>THIẾU</span>}<div className="muted">{a.license?.attribution}</div></td>
              <td><Badge kind={a.review_status === "approved" ? "ok" : a.review_status === "rejected" ? "bad" : "warn"}>{a.review_status}</Badge><div className="small muted">{a.rejection_reason}</div></td>
              <td className="row"><button onClick={() => review(a, "approved")}>Duyệt</button><button onClick={() => review(a, "rejected")}>Loại</button></td></tr>)}
          </tbody></table>
          <p className="small muted">Mỗi đoạn lời đọc được tìm ảnh <b>trên mạng trước</b> (thư viện của bạn → Openverse → Wikimedia → Pexels → Pixabay), chỉ nhận ảnh có giấy phép cho phép dùng thương mại.
            Đoạn <b>không có ảnh hợp quyền</b> mới gọi Gemini tạo ảnh minh họa (gắn nhãn “Minh họa AI”, không dùng làm thumbnail); đoạn nói về sự cố/sự kiện có thật hoặc đã hết trần ảnh AI thì dùng đồ họa tự dựng.
            Ảnh Google/web search không có giấy phép chỉ là tham chiếu, không bao giờ được tải.</p>
        </Card>
      </>}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="muted">Đang tải…</p>}><Media /></Suspense>;
}
