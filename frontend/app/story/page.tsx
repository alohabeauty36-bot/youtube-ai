"use client";
import { Suspense, useState } from "react";
import { api, fmtTime, mediaUrl } from "@/lib/api";
import { Badge, Card, ErrorBox } from "@/components/ui";
import { StoryHeader, useStory } from "@/components/story";

function Editor() {
  const { data: s, error, reload } = useStory();
  const [edit, setEdit] = useState<string | null>(null); const [err, setErr] = useState<any>(null);
  const [sample, setSample] = useState<string>(""); const [busy, setBusy] = useState("");
  const sc = s?.script; const content = sc?.content;
  const call = async (k: string, fn: () => Promise<any>) => {
    setBusy(k); setErr(null);
    try { const r = await fn(); if (r?.reasons?.length) setErr({ message: "QA chưa đạt", reasons: r.reasons }); await reload(); return r; }
    catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const paras: string[] = content?.spoken_script ? content.spoken_script.split(/\n\s*\n/) : [];
  return (
    <>
      <ErrorBox error={error} />
      <StoryHeader s={s} active="editor" reload={reload} />
      <ErrorBox error={err} />
      {s && <div className="grid g2">
        <div>
          <Card title="Nguồn & claims" right={<span className="small muted">Bấm claim để mở nguồn</span>}>
            {(s.news?.claims || []).map((c: any) => <div key={c.id} className={`claim ${c.status}`}>
              <div className="row"><b>{c.claim_key}</b><Badge kind={c.status === "verified" ? "ok" : c.status === "inference" ? "warn" : "bad"}>{c.status === "contested" ? "[CẦN XÁC MINH]" : c.status}</Badge>
                <a href={c.source_url || "#"} target="_blank" rel="noreferrer">{c.text}</a></div>
              <div className="small muted">{c.date_scope} {c.verification_reason}</div></div>)}
          </Card>
          <Card title="ResearchPack" right={s.research_pack && <Badge>{s.research_pack.is_demo ? "DEMO writer" : s.research_pack.model}</Badge>}>
            {s.research_pack ? <div className="small">
              <p><b>Góc khai thác:</b> {s.research_pack.content.editorial_angle}</p>
              <p><b>Vì sao bây giờ:</b> {s.research_pack.content.why_now}</p>
              <p><b>Phản biện:</b></p><ul>{s.research_pack.content.counterarguments?.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
              <p><b>Rủi ro:</b></p><ul>{s.research_pack.content.risk_notes?.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
              <p><b>Câu hỏi trung tâm:</b> {s.core_question}</p></div> : <p className="muted">Chưa có.</p>}
          </Card>
          <Card title="Audio (VieNeu-TTS)" right={sc?.actual_audio_seconds && <Badge kind={sc.actual_audio_seconds >= 570 && sc.actual_audio_seconds <= 630 ? "ok" : "warn"}>{fmtTime(sc.actual_audio_seconds)} đo thật</Badge>}>
            {s.audio_url && <audio controls src={mediaUrl(s.audio_url)} style={{ width: "100%" }} />}
            <div className="row" style={{ marginTop: 8 }}>
              <button disabled={!sc || !!busy} onClick={async () => { const r = await call("sample", () => api(`/api/stories/${s.id}/tts/sample`, { method: "POST" })); if (r?.url) setSample(mediaUrl(r.url)); }}>
                {busy === "sample" ? "Đang đọc…" : "Nghe thử 30 giây"}</button>
              {sample && <audio controls src={sample} />}
            </div>
            {s.segments?.length > 0 && <details style={{ marginTop: 8 }}><summary>{s.segments.length} đoạn audio (nghe lại từng đoạn)</summary>
              <table><tbody>{s.segments.map((g: any) => <tr key={g.idx}><td className="small">{fmtTime(g.start)}</td><td className="small">{g.text}</td>
                <td>{g.qc?.ok ? <Badge kind="ok">QC</Badge> : <Badge kind="warn">{g.qc?.issues?.join(", ")}</Badge>}</td>
                <td><audio controls preload="none" src={mediaUrl(g.url)} style={{ width: 160 }} /></td></tr>)}</tbody></table></details>}
          </Card>
        </div>
        <div>
          <Card title={sc ? `Kịch bản v${sc.version}` : "Kịch bản"} right={sc && <div className="row">
            <Badge kind={sc.qa_passed ? "ok" : "bad"}>QA {sc.qa_passed ? "đạt" : "chưa đạt"}</Badge>
            {s.script_approved_by ? <Badge kind="ok">đã duyệt: {s.script_approved_by}</Badge> :
              !["SCRIPT_DRAFT", "NEEDS_REVIEW"].includes(s.state) ? <Badge kind="ok">{s.mode === "AUTOPILOT_PRIVATE" ? "Autopilot: gate tự động đạt" : "đã qua bước kịch bản"}</Badge> :
              <button className="primary" disabled={!sc.qa_passed || !!busy} onClick={() => call("ap", () => api(`/api/scripts/${sc.id}/approve`, { method: "POST" }))}>Duyệt kịch bản & dựng</button>}
          </div>}>
            {!sc && <div className="row"><p className="muted">Chưa có kịch bản.</p><button onClick={() => call("gen", () => api(`/api/stories/${s.id}/generate-script`, { method: "POST" }))}>Sinh kịch bản</button></div>}
            {content && <>
              <b className="small">Tiêu đề (chọn 1 để upload)</b>
              {content.title_options.map((t: any, i: number) => <label key={i} className="row small" style={{ margin: "4px 0" }}>
                <input type="radio" style={{ width: "auto" }} checked={content.selected_title_index === i}
                  onChange={() => call("t", () => api(`/api/scripts/${sc.id}`, { method: "PATCH", json: { selected_title_index: i, change_note: "Đổi tiêu đề" } }))} />
                {t.title} <Badge>{t.strategy}</Badge></label>)}
              <div className="row" style={{ margin: "10px 0" }}>
                <b className="small">Lời đọc (chỉ phần này được đưa vào TTS)</b>
                <span className="right row">
                  {edit === null ? <button onClick={() => setEdit(content.spoken_script)}>Sửa toàn bài</button> :
                    <><button onClick={() => setEdit(null)}>Huỷ</button><button className="primary" onClick={async () => { await call("save", () => api(`/api/scripts/${sc.id}`, { method: "PATCH", json: { spoken_script: edit } })); setEdit(null); }}>Lưu thành bản mới</button></>}
                </span></div>
              {edit !== null ? <textarea value={edit} onChange={e => setEdit(e.target.value)} /> :
                paras.map((p, i) => <div key={i} className="para"><div>{p}</div>
                  <div className="row small" style={{ marginTop: 4 }}><span className="muted">Đoạn {i + 1}</span>
                    <button disabled={!!busy} onClick={() => { const ins = prompt("Yêu cầu viết lại đoạn này (vd: ngắn hơn, rõ điều kiện hơn):"); if (ins) call("rw", () => api(`/api/scripts/${sc.id}`, { method: "PATCH", json: { rewrite_paragraph_index: i, rewrite_instruction: ins, change_note: `Viết lại đoạn ${i + 1}` } })); }}>Viết lại đoạn</button></div></div>)}
              <details style={{ marginTop: 10 }}><summary>Kết quả Script QA</summary>
                {sc.qa.checks?.map((c: any, i: number) => <div key={i} className="check"><span className="i">{c.ok ? "✅" : c.blocking ? "⛔" : "⚠️"}</span><span>{c.name} <span className="muted small">{c.detail}</span></span></div>)}</details>
              <details><summary>Claim map & ghi chú dựng (không đọc lên)</summary><pre className="small">{JSON.stringify({ editorial_claim_map: content.editorial_claim_map, production_notes: content.production_notes }, null, 2)}</pre></details>
            </>}
            {s.scripts?.length > 1 && <details><summary>Lịch sử phiên bản ({s.scripts.length})</summary>
              <ul className="small">{s.scripts.map((v: any) => <li key={v.id}>v{v.version} · {v.created_by} · {v.word_count} từ · {v.actual_audio_seconds ? fmtTime(v.actual_audio_seconds) : "—"} · {v.change_note} {v.qa_passed ? "✅" : "⛔"}</li>)}</ul></details>}
          </Card>
        </div>
      </div>}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={<p className="muted">Đang tải…</p>}><Editor /></Suspense>;
}
