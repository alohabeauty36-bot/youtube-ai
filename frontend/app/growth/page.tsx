"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { Badge, Card, DemoBanner, ErrorBox, useLoad } from "@/components/ui";

const usd = (v: any, d = 2) => (v === null || v === undefined ? "—" : `$${Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
const num = (v: any) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("vi-VN"));
const fmtVal = (v: any) => (v && typeof v === "object" ? Object.entries(v).map(([k, x]: any) => `${k} ${(x * 100).toFixed(0)}%`).join(" · ") : String(v));
const PILLAR_NAME: Record<string, string> = { P1: "P1 · Trước khi mua", P2: "P2 · Mẹo dùng", P3: "P3 · Sạc, thị trường" };

/** Cột theo ngày, một chuỗi số liệu, một trục. Di chuột để xem giá trị từng ngày. */
function DayBars({ title, rows, field, fmt }: { title: string; rows: any[]; field: string; fmt: (v: any) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  const vals = rows.map(r => (r[field] === null || r[field] === undefined ? null : Number(r[field])));
  const max = Math.max(1e-9, ...vals.map(v => v ?? 0));
  const W = 640, H = 150, pad = 4, bw = rows.length ? (W - pad * 2) / rows.length : 0;
  const h = hover !== null ? rows[hover] : null;
  return (
    <div className="viz">
      <div className="row small"><b>{title}</b><span className="right muted">{h ? `${h.day}: ${fmt(vals[hover!])}` : `cao nhất ${fmt(max)}`}</span></div>
      <svg viewBox={`0 0 ${W} ${H + 18}`} width="100%" role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
        <line x1={0} x2={W} y1={H} y2={H} stroke="var(--line)" strokeWidth={1} />
        {rows.map((r, i) => {
          const v = vals[i];
          const bh = v ? Math.max(2, (v / max) * (H - 10)) : 0;
          const x = pad + i * bw;
          return (
            <g key={r.day} onMouseEnter={() => setHover(i)}>
              <rect x={x} y={0} width={bw} height={H} fill="transparent" />
              {v !== null && <rect x={x + 1} y={H - bh} width={Math.max(1, bw - 2)} height={bh} rx={2}
                fill="var(--viz-1)" opacity={hover === null || hover === i ? 1 : 0.45} />}
            </g>
          );
        })}
        {rows.length > 0 && <>
          <text x={pad} y={H + 14} fontSize={11} fill="var(--muted)">{rows[0].day.slice(5)}</text>
          <text x={W - pad} y={H + 14} fontSize={11} fill="var(--muted)" textAnchor="end">{rows[rows.length - 1].day.slice(5)}</text>
        </>}
      </svg>
    </div>
  );
}

export default function Growth() {
  const g = useLoad<any>("/api/growth");
  const [err, setErr] = useState<any>(null); const [busy, setBusy] = useState(""); const [kpi, setKpi] = useState("");
  useEffect(() => { if (g.data?.config) setKpi(String(g.data.config.kpi_monthly_revenue_usd || "")); }, [g.data?.config?.kpi_monthly_revenue_usd]);
  const act = async (k: string, fn: () => Promise<any>) => {
    setBusy(k); setErr(null);
    try { await fn(); await g.reload(); } catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const p = g.data?.latest; const st = p?.status || {}; const cfg = g.data?.config || {}; const cur = g.data?.current || {};
  const ch = g.data?.channel || {}; const memo = g.data?.memo?.memo;
  const prog = st.kpi_usd ? Math.min(1, (st.mtd_usd || 0) / st.kpi_usd) : 0;
  const projPos = st.kpi_usd ? Math.min(1, (st.projected_usd || 0) / st.kpi_usd) : 0;
  const series = st.series || [];
  return (
    <>
      <style>{`.viz{ --viz-1:#338ced } .meter{ position:relative; height:12px; background:var(--panel2); border-radius:6px; overflow:hidden; margin:8px 0 4px }
        .meter .fill{ position:absolute; left:0; top:0; bottom:0; background:var(--viz-1); border-radius:6px }
        .meter .mark{ position:absolute; top:-2px; bottom:-2px; width:2px; background:var(--fg) }`}</style>
      <h2>Tăng trưởng & KPI</h2>
      <DemoBanner show={p?.is_demo || g.data?.mode === "demo"} />
      <ErrorBox error={g.error || err} />
      {ch.connected && !ch.analytics_scope && <div className="error">Kênh được kết nối trước khi có Ban Tăng trưởng nên chưa có quyền đọc Analytics.
        Vào <Link href="/settings/#youtube">Thiết lập → YouTube</Link> → bấm <b>Kết nối kênh YouTube</b> lại và đồng ý các quyền mới (đọc số liệu, đọc doanh thu).</div>}
      {!ch.connected && <div className="hint">Chưa kết nối kênh. Vào <Link href="/settings/#youtube">Thiết lập → YouTube</Link> → Kết nối kênh YouTube.</div>}

      <Card title="KPI doanh thu quảng cáo tháng" right={<Badge kind={cfg.growth_auto_apply ? "ok" : "info"}>{cfg.growth_auto_apply ? "Ban Tăng trưởng tự chỉnh kế hoạch" : "Chỉ đề xuất"}</Badge>}>
        <div className="row">
          <span className="small">KPI (USD/tháng)</span>
          <input style={{ width: 140 }} type="number" min={0} step="1" value={kpi} onChange={e => setKpi(e.target.value)} placeholder="ví dụ 300" />
          <button disabled={!!busy} onClick={() => act("kpi", () => api("/api/config", { method: "PUT", json: { values: { kpi_monthly_revenue_usd: Number(kpi || 0) } } }))}>Lưu KPI</button>
          <button disabled={!!busy} onClick={() => act("auto", () => api("/api/config", { method: "PUT", json: { values: { growth_auto_apply: !cfg.growth_auto_apply } } }))}>
            {cfg.growth_auto_apply ? "Chuyển sang chỉ đề xuất" : "Cho phép tự chỉnh"}</button>
          <span className="right row">
            <button className="primary" disabled={!!busy} onClick={() => act("run", () => api("/api/growth/run", { method: "POST", json: { memo: false } }))}>
              {busy === "run" ? "Đang đồng bộ…" : "Đồng bộ & lập kế hoạch"}</button>
            <button disabled={!!busy} onClick={() => act("memo", () => api("/api/growth/run", { method: "POST", json: { memo: true } }))}>
              {busy === "memo" ? "Đang viết…" : "Viết bản ghi nhớ chiến lược"}</button>
          </span>
        </div>
        <p className="small muted">Doanh thu quảng cáo YouTube ước tính (estimatedAdRevenue), đọc từ YouTube Analytics. Ban Tăng trưởng chạy mỗi sáng 6:23,
          tự chỉnh số video/ngày và ưu tiên trụ cột cho Autopilot trong trần ngân sách AI. Không bao giờ tự công khai video hay sửa video trên kênh.</p>
        {p && st.kpi_set && <>
          <div className="meter" aria-label={`Đã đạt ${(prog * 100).toFixed(0)}% KPI`}><div className="fill viz" style={{ width: `${prog * 100}%` }} />
            <div className="mark" style={{ left: `calc(${projPos * 100}% - 1px)` }} title="Dự báo cuối tháng" /></div>
          <div className="small muted">Đã đạt {(prog * 100).toFixed(0)}% · vạch trắng = dự báo cuối tháng ({((st.projected_ratio || 0) * 100).toFixed(0)}%)
            · số liệu đến {st.data_through || "—"}, còn {st.days_remaining} ngày</div>
        </>}
      </Card>

      {!p && <Card><p className="muted">Chưa có lần chạy nào. Bấm “Đồng bộ & lập kế hoạch”.</p></Card>}
      {p && <>
        <div className="grid g4">
          <Card title={`Đã kiếm tháng ${st.month || ""}`}><div className="kpi">{usd(st.mtd_usd)}</div><div className="small muted">KPI {st.kpi_set ? usd(st.kpi_usd, 0) : "chưa đặt"}</div></Card>
          <Card title="Dự báo cuối tháng"><div className="kpi">{usd(st.projected_usd)}</div><div className="small muted">theo tốc độ 7 ngày gần nhất ({usd(st.daily_rate_usd)}/ngày)</div></Card>
          <Card title="Còn thiếu so với KPI"><div className="kpi">{st.kpi_set ? usd(st.gap_usd) : "—"}</div>
            <div className="small muted">{st.need_per_day_usd != null ? `cần ${usd(st.need_per_day_usd)}/ngày từ nay` : ""}</div></Card>
          <Card title="RPM 28 ngày"><div className="kpi">{usd(st.rpm_usd, 3)}</div><div className="small muted">USD / 1.000 lượt xem</div></Card>
        </div>
        <div className="grid g3">
          <Card title="Lượt xem/ngày"><div className="kpi">{num(st.views_per_day_7d)}</div>
            <div className="small muted">cần ~{num(st.required_views_per_day)}/ngày để đạt KPI</div></Card>
          <Card title="Kế hoạch sản xuất đang áp dụng"><div className="kpi">{cur.stories_per_day} <span className="small muted">video/ngày</span></div>
            <div className="small muted">trần Ban {cfg.growth_max_stories_per_day}/ngày · trần {cfg.max_videos_per_week} video/tuần · ngân sách đủ ~{st.limits?.afford_per_day ?? "—"}/ngày</div></Card>
          <Card title="Ưu tiên trụ cột (Autopilot)"><table><tbody>{Object.entries(cur.pillar_weights || {}).map(([k, v]: any) =>
            <tr key={k}><td>{PILLAR_NAME[k] || k}</td><td>{(v * 100).toFixed(0)}%</td></tr>)}</tbody></table></Card>
        </div>

        {p.owner_actions?.length > 0 && <Card title="Việc chỉ bạn làm được">
          <ul>{p.owner_actions.map((o: any, i: number) => <li key={i}><b>{o.title}</b><div className="small muted">{o.detail}</div></li>)}</ul></Card>}

        <Card title={`Quyết định lần chạy gần nhất · ${fmtDate(p.at)}`} right={<Badge kind={p.applied ? "ok" : "info"}>{!p.applied ? "chỉ đề xuất" : (p.decisions || []).some((d: any) => d.applied) ? "đã áp dụng" : "không cần đổi"}</Badge>}>
          <table><tbody>{(p.decisions || []).map((d: any, i: number) => <tr key={i}>
            <td><b>{d.label}</b><div className="small muted">{d.reason}</div></td>
            <td className="small" style={{ whiteSpace: "nowrap" }}>{d.unchanged ? fmtVal(d.to) : <>{fmtVal(d.from)} → {fmtVal(d.to)}</>}</td>
            <td>{d.unchanged ? <Badge>giữ nguyên</Badge> : d.applied ? <Badge kind="ok">đã áp dụng</Badge> : <Badge kind="warn">đề xuất</Badge>}</td>
          </tr>)}</tbody></table>
          {st.sync?.error && <p className="small" style={{ color: "#ff9c90" }}>{st.sync.error}</p>}
          {st.sync?.money_error && <p className="small muted">{st.sync.money_error}</p>}
        </Card>

        {series.length > 0 && <div className="grid g2">
          <Card><DayBars title="Lượt xem theo ngày (35 ngày)" rows={series} field="views" fmt={num} /></Card>
          <Card><DayBars title="Doanh thu quảng cáo theo ngày (USD)" rows={series} field="revenue_usd" fmt={(v) => usd(v)} /></Card>
        </div>}

        {memo && <Card title={`Bản ghi nhớ chiến lược · ${fmtDate(g.data.memo.at)}`} right={g.data.memo.is_demo && <Badge kind="demo">DEMO</Badge>}>
          <p>{memo.summary}</p>
          {memo.diagnosis?.length > 0 && <><b className="small">Chẩn đoán</b><ul className="small">{memo.diagnosis.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}
          {memo.actions?.length > 0 && <><b className="small">Hành động</b><table><tbody>{memo.actions.map((a: any, i: number) => <tr key={i}>
            <td><b>{a.title}</b><div className="small muted">{a.why}</div></td><td className="small">{a.how}{a.expected_impact && <div className="muted">{a.expected_impact}</div>}</td>
            <td><Badge kind={a.who === "owner" ? "warn" : "info"}>{a.who === "owner" ? "chủ kênh" : "agent"}</Badge></td></tr>)}</tbody></table></>}
          {memo.experiments?.length > 0 && <><b className="small">Thử nghiệm</b><ul className="small">{memo.experiments.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}
          {memo.content_guidance && <p className="small"><b>Định hướng cho Phòng Biên kịch:</b> {memo.content_guidance}</p>}
          {memo.risks?.length > 0 && <><b className="small">Rủi ro</b><ul className="small">{memo.risks.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}
        </Card>}
        {st.memo_note && <p className="small muted">{st.memo_note}</p>}

        <div className="grid g2">
          <Card title="Hiệu quả theo trụ cột (video của app, 28 ngày)">
            <table><thead><tr><th>Trụ cột</th><th>Video</th><th>Lượt xem TB</th><th>Xem hết TB</th><th>Doanh thu TB</th></tr></thead><tbody>
              {Object.entries(st.by_pillar || {}).map(([k, v]: any) => <tr key={k}><td>{PILLAR_NAME[k] || k}</td><td>{v.videos}</td><td>{num(v.avg_views)}</td>
                <td>{v.avg_view_pct}%</td><td>{usd(v.avg_revenue_usd, 3)}</td></tr>)}</tbody></table>
            {!Object.keys(st.by_pillar || {}).length && <p className="small muted">Chưa có video nào của app được công khai trong 28 ngày.</p>}
          </Card>
          <Card title="Top video 28 ngày">
            <table><thead><tr><th>Video</th><th>Lượt xem</th><th>Xem hết</th><th>Doanh thu</th></tr></thead><tbody>
              {(st.top_videos || []).map((v: any) => <tr key={v.id}><td className="small">{v.title || v.id} {v.ours && <Badge>{v.pillar || "app"}</Badge>}</td>
                <td>{num(v.views)}</td><td>{v.avg_view_pct}%</td><td>{usd(v.revenue_usd, 3)}</td></tr>)}</tbody></table>
          </Card>
        </div>

        {st.ypp && !st.monetized && <Card title="Điều kiện bật kiếm tiền (YPP)">
          <p className="small">Người đăng ký: <b>{num(st.ypp.subscribers)}</b> / {num(st.ypp.subscribers_needed)} · Giờ xem 365 ngày (ước tính): <b>{num(st.ypp.watch_hours_365)}</b> / {num(st.ypp.watch_hours_needed)}</p>
          <p className="small muted">{st.ypp.note}</p></Card>}

        <Card title="Lịch sử chạy">
          <table><thead><tr><th>Lúc</th><th>Ai</th><th>Đã kiếm</th><th>Dự báo</th><th>KPI</th><th>Thay đổi</th></tr></thead><tbody>
            {(g.data?.history || []).map((h: any) => <tr key={h.id}><td className="small">{fmtDate(h.at)}</td><td className="small">{h.actor}</td>
              <td>{usd(h.mtd_usd)}</td><td>{usd(h.projected_usd)}</td><td>{usd(h.kpi_usd, 0)}</td>
              <td className="small">{h.changes.map((c: any) => c.label).join(", ") || "—"}</td></tr>)}</tbody></table>
        </Card>
      </>}
    </>
  );
}
