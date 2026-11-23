"use client";
import { fmtDate } from "@/lib/api";
import { Badge, Card, ErrorBox, useLoad } from "@/components/ui";

export default function Analytics() {
  const a = useLoad<any>("/api/analytics");
  const c = useLoad<any>("/api/costs");
  const au = useLoad<any[]>("/api/audit?limit=60");
  return (
    <>
      <h2>Phân tích & chi phí</h2>
      <ErrorBox error={a.error || c.error} />
      <div className="grid g3">
        <Card title="Đã tải RIÊNG TƯ"><div className="kpi">{a.data?.uploaded_private ?? 0}</div></Card>
        <Card title="Chủ kênh đã tự công khai (quan sát)"><div className="kpi">{a.data?.observed_public_by_owner ?? 0}</div></Card>
        <Card title="Cơ cấu trụ cột"><div className="row">{(a.data?.pillar_mix || []).map((p: any) => <Badge key={p.pillar}>{p.pillar}: {p.count}</Badge>)}</div></Card>
      </div>
      <Card title="YouTube Analytics (chỉ đọc)"><p className="small muted">{a.data?.note} Ứng dụng không tự sửa tiêu đề/thumbnail của video đang công khai.</p></Card>
      <div className="grid g2">
        <Card title="Chi phí API theo dịch vụ"><table><thead><tr><th>Dịch vụ</th><th>Đơn vị</th><th>Số lượng</th><th>USD ước tính</th></tr></thead><tbody>
          {(c.data?.by_service || []).map((r: any, i: number) => <tr key={i}><td>{r.service} {r.is_demo && <Badge kind="demo">DEMO</Badge>}</td><td>{r.unit_type}</td><td>{r.units}</td><td>{r.usd}</td></tr>)}</tbody></table>
          {!c.data?.by_service?.length && <p className="small muted">Chưa có chi phí (chế độ DEMO không gọi API trả phí).</p>}</Card>
        <Card title="Thời gian tính toán theo job"><table><thead><tr><th>Job</th><th>Số lần</th><th>Giây</th></tr></thead><tbody>
          {(c.data?.compute || []).map((r: any) => <tr key={r.job_type}><td>{r.job_type}</td><td>{r.runs}</td><td>{r.seconds}</td></tr>)}</tbody></table></Card>
      </div>
      <Card title="Nhật ký kiểm toán (audit log)"><table><tbody>{(au.data || []).map((x, i) => <tr key={i}><td className="small">{fmtDate(x.at)}</td><td className="small">{x.actor}</td><td className="small">{x.action}</td><td className="small muted">{x.entity_type} {x.entity_id?.slice?.(0, 8)} {JSON.stringify(x.details).slice(0, 140)}</td></tr>)}</tbody></table></Card>
    </>
  );
}
