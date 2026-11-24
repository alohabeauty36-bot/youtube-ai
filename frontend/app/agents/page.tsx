"use client";
import { useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Badge, Card, ErrorBox, useLoad } from "@/components/ui";

const usd = (v?: number | null, d = 2) => (v == null ? "—" : `$${Number(v).toFixed(d)}`);
const pct = (v?: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const DEPT_NAME: Record<string, string> = {
  san_tin: "Phòng Săn tin", nghien_cuu: "Phòng Nghiên cứu", bien_kich: "Phòng Biên kịch", bien_tap: "Phòng Biên tập", bai_luan: "Phòng Bài luận", hoc_tap: "Ban Đào tạo",
};
const STYLE_ROWS: [string, string][] = [["essays", "Số bài"], ["words", "Số từ"], ["sections", "Số phần (##)"], ["paragraphs", "Số đoạn"],
  ["words_per_paragraph", "Từ / đoạn"], ["words_per_sentence", "Từ / câu"], ["citations_per_100w", "Trích dẫn / 100 từ"],
  ["distinct_sources", "Số nguồn dùng"], ["counter_markers", "Dấu hiệu phản biện"]];
const WRITERS: [string, string][] = [["anthropic", "Claude"], ["openai", "ChatGPT"], ["owner", "Chủ kênh sửa"], ["local", "Trò"]];

function Styles({ st }: { st: Record<string, any> }) {
  const cols = WRITERS.filter(([k]) => st[k]);
  if (!cols.length) return <p className="small muted">Chưa có bài luận nào để so sánh cách viết.</p>;
  return <details open><summary className="small">Cách viết của từng thầy (trò lấy trung bình của thầy làm mục tiêu)</summary>
    <table className="small"><thead><tr><th></th>{cols.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead><tbody>
      {STYLE_ROWS.map(([k, l]) => <tr key={k}><td>{l}</td>{cols.map(([w]) => <td key={w}>{st[w]?.[k] ?? "—"}</td>)}</tr>)}</tbody></table></details>;
}
const MODE_LABEL: Record<string, string> = { auto: "Tự động", teacher_only: "Chỉ nhờ thầy", student_only: "Chỉ trò tự làm" };

function Bar({ used, cap, good }: { used: number; cap: number; good?: boolean }) {
  // good: thanh "càng đầy càng tốt" (tỷ lệ đạt) → đỏ khi thấp; mặc định là thanh chi tiêu → đỏ khi gần trần
  const r = cap > 0 ? Math.min(1, used / cap) : 0;
  const cls = good ? (r < 0.5 ? "bad" : r < 0.8 ? "warn" : "") : (r > 0.9 ? "bad" : r > 0.7 ? "warn" : "");
  return <div className="bar"><div className={cls} style={{ width: `${r * 100}%` }} /></div>;
}

export default function Agents() {
  const b = useLoad<any>("/api/budget", () => 30000);
  const a = useLoad<any>("/api/agents", () => 30000);
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<any>(null); const [msg, setMsg] = useState("");
  const act = async (k: string, fn: () => Promise<any>) => {
    setBusy(k); setErr(null); setMsg("");
    try { const r = await fn(); setMsg(r?.reason || (r?.ran ? "Đã đúc kết sổ tay văn phong." : "Đã lưu.")); await a.reload(); await b.reload(); }
    catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const B = b.data, A = a.data;
  return (
    <>
      <h2>Bộ não AI & Ngân sách</h2>
      <ErrorBox error={b.error || a.error || err} />
      {msg && <div className="hint">{msg}</div>}

      <div className="grid g4">
        <Card title="Đã dùng tháng này">
          <div className="kpi">{usd(B?.spent_month_usd)} <span className="small muted">/ {usd(B?.monthly_cap_usd, 0)}</span></div>
          <Bar used={B?.spent_month_usd || 0} cap={B?.effective_cap_usd || 1} />
          <p className="small muted">Trần cứng {usd(B?.effective_cap_usd)} (giữ lại {pct(B?.safety_margin)} phòng sai số). Dự báo cả tháng: {usd(B?.forecast_month_usd)}.</p>
        </Card>
        <Card title="Hôm nay">
          <div className="kpi">{usd(B?.spent_today_usd)} <span className="small muted">/ {usd(B?.allowance_today_usd)}</span></div>
          <Bar used={B?.spent_today_usd || 0} cap={B?.allowance_today_usd || 1} />
          <p className="small muted">Phần còn lại của tháng chia đều {B?.days_left} ngày còn lại, tối đa 2× mức bình quân.</p>
        </Card>
        <Card title="Số tin hôm nay">
          <div className="kpi">{B?.stories_today ?? 0} <span className="small muted">/ {B?.stories_per_day}</span></div>
          <div className="row"><Badge kind={B?.can_start_story ? "ok" : "warn"}>{B?.can_start_story ? "Còn suất làm tin" : "Tạm dừng mở tin mới"}</Badge></div>
          <p className="small muted">{B?.can_start_story ? `Mục tiêu ${B?.stories_per_day_min}–${B?.stories_per_day} tin/ngày.` : B?.reason}</p>
        </Card>
        <Card title="Chi phí mỗi video">
          <div className="kpi">{usd(B?.avg_story_usd, 3)}</div>
          <p className="small muted">Trần mỗi video {usd(B?.story_max_usd)}. Với số tiền còn lại làm được khoảng <b>{B?.capacity_month_stories ?? "—"}</b> video nữa trong tháng.</p>
        </Card>
      </div>
      {B?.stale_reservations > 0 && <div className="error">Có {B.stale_reservations} lần giữ chỗ ngân sách quá 2 giờ chưa chốt (có thể do mất kết nối). Số tiền này đang được tính là đã tiêu cho an toàn.</div>}

      <Card title="Trần ngân sách theo phòng ban (tháng này)">
        <table><thead><tr><th>Phòng ban</th><th>Đã dùng</th><th>Trần</th><th style={{ width: "40%" }}></th></tr></thead><tbody>
          {(B?.by_department || []).map((d: any) => <tr key={d.department}><td>{DEPT_NAME[d.department] || d.department}</td><td>{usd(d.used_usd, 3)}</td><td>{usd(d.cap_usd)}</td>
            <td><Bar used={d.used_usd} cap={d.cap_usd} /></td></tr>)}</tbody></table>
      </Card>

      <Card title="Thầy và trò">
        <div className="row">
          <Badge kind={A?.teachers?.anthropic ? "ok" : "warn"}>Claude: {A?.teachers?.anthropic ? "đã kết nối" : "chưa có key"}</Badge>
          <Badge kind={A?.teachers?.openai ? "ok" : "info"}>ChatGPT: {A?.teachers?.openai ? "đã kết nối" : "chưa dùng"}</Badge>
          <Badge kind={A?.student?.available ? "ok" : A?.student?.configured ? "warn" : "info"}>
            Trò ({A?.student?.model}): {A?.student?.available ? "đang chạy trên máy" : A?.student?.configured ? "không kết nối được Ollama" : "chưa cài"}</Badge>
          <span className="small muted">Thầy chính: {A?.teacher_strategy === "alternate" ? "luân phiên Claude và ChatGPT" : A?.teacher_strategy}</span>
        </div>
        <p className="small muted">Mỗi đề bài: <b>bộ nhớ</b> (đề cũ, $0) → <b>trò tự làm</b> nếu phòng đã tốt nghiệp ($0) → <b>thầy</b> (tốn tiền, luôn qua Ban Tài chính).
          Mỗi lần thầy làm, trò làm thử cùng đề để được chấm. Tốt nghiệp khi đạt ≥ {pct(A?.graduation?.rate)} trong {A?.graduation?.window} lần gần nhất (tối thiểu {A?.graduation?.min} lần);
          sau đó vẫn gửi thầy kiểm tra ngẫu nhiên {pct(A?.graduation?.audit_rate)} số việc. Lên <b>Bậc thầy</b> khi có ≥ {A?.graduation?.master_min_passes} lần đạt và
          tỷ lệ đạt ≥ {pct(A?.graduation?.master_rate)}: phòng tự làm, chỉ gửi thầy {pct(A?.graduation?.master_audit_rate)} số việc (và khi bài của trò không qua QA). Kịch bản do trò viết vẫn phải qua Script QA và chủ kênh duyệt như thường.</p>
        {!A?.student?.configured && <p className="small">Muốn các phòng tự viết không tốn tiền: cài <b>Ollama</b> trên máy, chạy <code>ollama pull {A?.student?.model}</code>, rồi nhập địa chỉ <code>http://localhost:11434</code> trong Thiết lập → Bộ não phòng ban.</p>}
      </Card>

      <div className="grid g2">
        {(A?.departments || []).map((d: any) => (
          <Card key={d.code} title={<>{d.name} <Badge kind={d.level >= 4 ? "master" : d.graduated ? "ok" : "info"}>{d.learns ? (d.level >= 4 ? `★ ${d.level_name}` : d.level_name) : "Không cần học thay"}</Badge></>}
            right={d.learns && <select value={d.mode} disabled={!!busy} onChange={e => act(`m-${d.code}`, () => api(`/api/agents/${d.code}/mode`, { method: "POST", json: { mode: e.target.value } }))}>
              {Object.entries(MODE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>}>
            <p className="small muted">{d.role} · thầy Claude: <code>{d.teacher_model}</code>
              {d.teacher_strategy && <> · {d.teacher_strategy === "alternate" ? "luân phiên Claude và ChatGPT" : d.teacher_strategy}</>}</p>
            {d.learns && <>
              <div className="row small">
                <span>Tỷ lệ trò đạt: <b>{pct(d.pass_rate)}</b> ({d.window} lần gần nhất)</span>
                <span>Giống thầy: <b>{pct(d.similarity_avg)}</b></span>
              </div>
              <Bar used={d.pass_rate || 0} cap={1} good />
            </>}
            <table className="small"><tbody>
              <tr><td>Lần nhờ thầy</td><td>{d.teacher_calls}</td><td>Lấy lại từ bộ nhớ</td><td>{d.cache_hits}</td></tr>
              <tr><td>Trò làm thử / đạt</td><td>{d.student_attempts} / {d.student_passes}</td><td>Trò làm thay thầy</td><td>{d.student_live}</td></tr>
              <tr><td>Ký ức</td><td>{Object.entries(d.memories || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "—"}</td><td>Đã tiết kiệm</td><td>{usd(d.saved_usd, 3)}</td></tr>
            </tbody></table>
            {d.code === "bai_luan" && d.styles && <Styles st={d.styles} />}
            {d.style_guide && <details><summary className="small">Sổ tay văn phong</summary><pre className="small">{d.style_guide}</pre></details>}
            {d.lessons?.length > 0 && <details><summary className="small">Bài học gần đây ({d.lessons.length})</summary>
              <ul className="small">{d.lessons.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul></details>}
            {d.code !== "hoc_tap" && <div className="row" style={{ marginTop: 6 }}>
              <button disabled={!!busy} onClick={() => act(`r-${d.code}`, () => api(`/api/agents/${d.code}/reflect`, { method: "POST" }))}>
                {busy === `r-${d.code}` ? "Đang đúc kết…" : "Đúc kết bài học ngay"}</button>
              <span className="small muted">Tự chạy mỗi Chủ nhật. Lần gần nhất: {fmtDate(d.last_reflect_at)}</span></div>}
          </Card>))}
      </div>

      <Card title="Sổ cái chi tiêu AI (30 lần gần nhất)">
        <table><thead><tr><th>Lúc</th><th>Phòng</th><th>Thầy</th><th>Token vào / cache / ra</th><th>Trạng thái</th><th>USD</th></tr></thead><tbody>
          {(B?.recent || []).map((r: any, i: number) => <tr key={i}><td className="small">{fmtDate(r.at)}</td><td>{DEPT_NAME[r.department] || r.department}</td>
            <td className="small">{r.provider} · {r.model}</td><td className="small">{r.input_tokens} / {r.cache_read_tokens} / {r.output_tokens}</td>
            <td><Badge kind={r.status === "settled" ? "ok" : r.status === "reserved" ? "warn" : "info"}>{{ settled: "đã chốt", reserved: "đang giữ chỗ", released: "đã huỷ" }[r.status as string] || r.status}</Badge></td>
            <td>{usd(r.usd, 4)}</td></tr>)}</tbody></table>
        {!B?.recent?.length && <p className="small muted">Chưa có lần gọi Claude/ChatGPT nào.</p>}
      </Card>
    </>
  );
}
