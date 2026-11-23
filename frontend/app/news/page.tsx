"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Badge, Card, DemoBanner, ErrorBox, useLoad } from "@/components/ui";

const STATUS_KIND: Record<string, any> = { shortlist: "ok", research: "warn", reference: "info", blocked: "bad", duplicate: "info", dismissed: "info" };

export default function News() {
  const [pillar, setPillar] = useState(""); const [status, setStatus] = useState("");
  const q = new URLSearchParams({ ...(pillar && { pillar }), ...(status && { status }) }).toString();
  const { data, error, reload } = useLoad<any[]>(`/api/news${q ? "?" + q : ""}`);
  const [open, setOpen] = useState<string | null>(null); const [err, setErr] = useState<any>(null);
  const router = useRouter();
  const create = async (n: any) => {
    setErr(null);
    try { const st = await api("/api/stories", { method: "POST", json: { news_item_id: n.id } }); await api(`/api/stories/${st.id}/run`, { method: "POST" }); router.push(`/stories/${st.id}`); }
    catch (e) { setErr(e); }
  };
  const claimAct = async (c: any, status: string) => {
    const note = prompt(status === "verified" ? "Lý do xác minh thủ công (bắt buộc) — nguồn đã đối chiếu:" : "Ghi chú:") || "";
    const url = status === "verified" ? (prompt("URL nguồn chứng minh:", c.source_url || "") || "") : undefined;
    try { await api(`/api/claims/${c.id}`, { method: "PATCH", json: { status, editor_note: note, source_url: url } }); reload(); } catch (e) { setErr(e); }
  };
  return (
    <>
      <h2>Săn tin</h2>
      <DemoBanner show={data?.some(n => n.is_demo)} />
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 160 }} value={pillar} onChange={e => setPillar(e.target.value)}><option value="">Mọi trụ cột</option><option>P1</option><option>P2</option><option>P3</option></select>
        <select style={{ width: 180 }} value={status} onChange={e => setStatus(e.target.value)}><option value="">Mọi trạng thái</option>
          {Object.keys(STATUS_KIND).map(s => <option key={s}>{s}</option>)}</select>
        <button onClick={async () => { try { await api("/api/sources/scan", { method: "POST" }); reload(); } catch (e) { setErr(e); } }}>Quét tin ngay</button>
      </div>
      <ErrorBox error={error || err} />
      {(data || []).map(n => (
        <div key={n.id} className="news-card">
          <div className="row">
            <span className="score">{n.score}</span><Badge kind={STATUS_KIND[n.status]}>{n.status}</Badge>
            {n.pillar && <Badge>{n.pillar}</Badge>}{n.is_demo && <Badge kind="demo">DEMO</Badge>}
            <b>{n.title}</b>
            <span className="right row">
              <button onClick={() => setOpen(open === n.id ? null : n.id)}>{open === n.id ? "Ẩn" : "Chứng cứ & lý do"}</button>
              <button className="primary" disabled={n.has_story || ["blocked", "duplicate"].includes(n.status)} onClick={() => create(n)}>
                {n.has_story ? "Đã có video" : "Làm video (MANUAL)"}</button>
            </span>
          </div>
          <div className="small muted">{n.publisher || "Không rõ nguồn"} · đăng {fmtDate(n.published_at)}
            {n.effective_to && <> · hiệu lực đến {fmtDate(n.effective_to)}</>} · <a href={n.url} target="_blank" rel="noreferrer">nguồn gốc</a></div>
          {n.block_reason && <div className="error small">{n.block_reason}</div>}
          {open === n.id && <div style={{ marginTop: 8 }}>
            <div className="grid g2">
              <div><b className="small">Điểm (/100)</b><table><tbody>{Object.entries(n.score_breakdown || {}).map(([k, v]) => <tr key={k}><td>{k}</td><td>{String(v)}</td></tr>)}</tbody></table>
                <ul className="small">{(n.score_reasons || []).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul></div>
              <div><b className="small">Nguồn</b><ul className="small">{(n.supporting_sources || []).map((s: any) => <li key={s.source_id}>[{s.source_id}] cấp {s.trust_tier} · {s.publisher} · <a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></li>)}</ul></div>
            </div>
            <b className="small">Claims</b>
            {n.claims.map((c: any) => <div key={c.id} className={`claim ${c.status}`}>
              <div className="row"><b>{c.claim_key}</b><Badge kind={c.status === "verified" ? "ok" : c.status === "inference" ? "warn" : "bad"}>{c.status}</Badge>
                {c.sensitive && <Badge kind="warn">nhạy cảm</Badge>}<span>{c.text}</span>
                <span className="right row"><button onClick={() => claimAct(c, "verified")}>Xác minh tay</button><button onClick={() => claimAct(c, "removed")}>Loại claim</button></span></div>
              <div className="small muted">{c.verification_reason} · {c.source_ids?.join(", ")} {c.evidence_excerpt && <>· “{c.evidence_excerpt}”</>}</div>
            </div>)}
          </div>}
        </div>
      ))}
      {!data?.length && <Card><p className="muted">Chưa có tin. Bấm “Quét tin ngay”.</p></Card>}
    </>
  );
}
