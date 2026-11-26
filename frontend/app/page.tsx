"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { Badge, Card, DemoBanner, ErrorBox, STATE_LABEL, ScanResult, useLoad } from "@/components/ui";

export default function Dashboard() {
  const { data, error, reload } = useLoad<any>("/api/dashboard");
  const bud = useLoad<any>("/api/budget");
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<any>(null); const [msg, setMsg] = useState(""); const [scan, setScan] = useState<any>(null);
  const act = async (k: string, fn: () => Promise<any>) => {
    setBusy(k); setErr(null);
    try { const r = await fn(); setMsg(JSON.stringify(r)); await reload(); } catch (e) { setErr(e); } finally { setBusy(""); }
  };
  const st = data?.settings;
  return (
    <>
      <h2>Bảng điều khiển</h2>
      <DemoBanner show={data?.mode === "demo"} />
      <ErrorBox error={error || err} />
      <Card title="Tự động hoàn toàn" right={<Badge kind={st?.full_auto ? "ok" : "info"}>{st?.full_auto ? "Đang bật" : "Đang tắt"}</Badge>}>
        <div className="row">
          <span className="small">{st?.full_auto
            ? <>App tự quét tin, tự chọn tin, tự làm video, tự sửa lỗi (không được thì bỏ tin), tải lên RIÊNG TƯ và {st?.auto_publish_delay_minutes > 0
              ? <>tự công khai sau <b>{st.auto_publish_delay_minutes} phút</b> nếu bạn không bấm ⛔ Dừng trên Telegram</> : <>giữ riêng tư để bạn tự công khai</>}. Luôn trong trần ngân sách AI.</>
            : <>Bật để app tự làm mọi việc: quét tin, chọn tin, làm video, tự sửa lỗi, tải lên và tự công khai sau thời gian chờ (bạn vẫn có nút Dừng).</>}</span>
          <span className="right"><button className={st?.full_auto ? "" : "primary"} disabled={!!busy}
            onClick={() => act("full", () => api("/api/config", { method: "PUT", json: { values: { full_auto: !st?.full_auto } } }))}>
            {st?.full_auto ? "Tắt" : "Bật Tự động hoàn toàn"}</button></span>
        </div>
      </Card>
      <div className="grid g4">
        <Card title="Kênh đích"><div className="kpi small">{data?.channel || <span className="muted">Chưa chọn</span>}</div>
          <Link href="/settings">Đổi kênh / kết nối OAuth →</Link></Card>
        <Card title="Autopilot Private">
          <div className="row"><Badge kind={st?.autopilot_enabled || st?.full_auto ? "ok" : "info"}>{st?.full_auto ? "Bật (Tự động hoàn toàn)" : st?.autopilot_enabled ? "Đang bật" : "Đang tắt"}</Badge>
            {!st?.full_auto && <button disabled={!!busy} onClick={() => act("ap", () => api("/api/settings", { method: "PATCH", json: { autopilot_enabled: !st?.autopilot_enabled } }))}>
              {st?.autopilot_enabled ? "Tắt" : "Bật"}</button>}</div>
          <p className="small muted">Tự chọn tin ≥ {st?.score_autopilot}/100 đủ chứng cứ → dựng → tải lên RIÊNG TƯ → Telegram. Tối đa {bud.data?.stories_per_day ?? "—"} tin/ngày, luôn trong trần ngân sách.</p>
          <button disabled={!!busy || !(st?.autopilot_enabled || st?.full_auto)} onClick={() => act("tick", () => api("/api/autopilot/tick", { method: "POST" }))}>Chạy 1 vòng ngay</button>
        </Card>
        <Card title="Lịch quét tin">
          <div className="row"><Badge kind={st?.scan_schedule_enabled || st?.full_auto ? "ok" : "info"}>{st?.scan_schedule_enabled || st?.full_auto ? `Mỗi ${st?.scan_interval_hours}h` : "Đang tắt"}</Badge>
            {!st?.full_auto && <button disabled={!!busy} onClick={() => act("sch", () => api("/api/settings", { method: "PATCH", json: { scan_schedule_enabled: !st?.scan_schedule_enabled } }))}>
              {st?.scan_schedule_enabled ? "Tắt" : "Bật"}</button>}</div>
          <button style={{ marginTop: 8 }} disabled={!!busy} onClick={() => act("scan", async () => { const r = await api("/api/sources/scan", { method: "POST" }); setScan(r); return null; })}>
            {busy === "scan" ? "Đang quét…" : "Quét tin ngay"}</button>
        </Card>
        <Card title="Ngân sách AI tháng này"><div className="kpi">${(bud.data?.spent_month_usd ?? 0).toFixed(2)} <span className="small muted">/ ${bud.data?.monthly_cap_usd ?? 50}</span></div>
          <p className="small muted">Hôm nay ${(bud.data?.spent_today_usd ?? 0).toFixed(2)} / ${(bud.data?.allowance_today_usd ?? 0).toFixed(2)} · {bud.data?.stories_today ?? 0}/{bud.data?.stories_per_day ?? 3} tin</p>
          <Link href="/agents">Bộ não AI & Ngân sách →</Link></Card>
      </div>
      <ScanResult r={scan} />
      {msg && msg !== "null" && <pre className="small">{msg}</pre>}
      {data?.open_errors?.total > 0 && <div className={(data.open_errors.ERROR || 0) + (data.open_errors.CRITICAL || 0) > 0 ? "error row" : "hint row"}>
        <span>🐞 Tool đang có <b>{data.open_errors.total}</b> lỗi/cảnh báo chưa xử lý
          {(data.open_errors.ERROR || 0) + (data.open_errors.CRITICAL || 0) > 0 && <> (trong đó <b>{(data.open_errors.ERROR || 0) + (data.open_errors.CRITICAL || 0)}</b> lỗi)</>}.</span>
        <Link className="right" href="/errors">Xem Nhật ký lỗi →</Link></div>}
      {data?.alerts?.length > 0 && <Card title="⚠️ Cần chú ý">
        {data.alerts.map((a: any) => <div key={a.story_id} className="news-card"><Link href={`/story/?id=${a.story_id}`}><b>{a.topic}</b></Link> — {STATE_LABEL[a.state] || a.state}
          <ul>{a.blockers.map((b: string, i: number) => <li key={i} className="small">{b}</li>)}</ul></div>)}
      </Card>}
      <Card title="Video theo trạng thái">
        <div className="row">{Object.entries(data?.by_state || {}).map(([k, v]) => <Badge key={k}>{STATE_LABEL[k] || k}: {String(v)}</Badge>)}
          {!Object.keys(data?.by_state || {}).length && <span className="muted">Chưa có video nào.</span>}</div>
      </Card>
      <div className="grid g3">
        {(["P1", "P2", "P3"] as const).map(p => (
          <Card key={p} title={{ P1: "P1 · Trước khi mua xe", P2: "P2 · Mẹo dùng & tối ưu", P3: "P3 · Trạm sạc, thị trường, chính sách" }[p]}>
            {(data?.shortlist?.[p] || []).map((n: any) => <div key={n.id} className="news-card">
              <div className="row"><span className="score">{n.score}</span>{n.is_demo && <Badge kind="demo">DEMO</Badge>}</div>
              <div>{n.title}</div><Link className="small" href="/news">Xem chứng cứ & chọn →</Link></div>)}
            {!(data?.shortlist?.[p] || []).length && <p className="muted small">Chưa có tin shortlist.</p>}
          </Card>))}
      </div>
    </>
  );
}
