"use client";
import Link from "next/link";
import { fmtDate } from "@/lib/api";
import { Badge, Card, ErrorBox, StateBadge, useLoad } from "@/components/ui";

export default function Stories() {
  const { data, error } = useLoad<any[]>("/api/stories", d => (d || []).some((s: any) => s.running) ? 3000 : null);
  return (
    <>
      <h2>Video đang làm</h2>
      <ErrorBox error={error} />
      <Card>
        <table><thead><tr><th>Chủ đề</th><th>Trụ cột</th><th>Chế độ</th><th>Trạng thái</th><th>Bản</th><th>Cập nhật</th></tr></thead>
          <tbody>{(data || []).map(s => <tr key={s.id}>
            <td><Link href={`/stories/${s.id}`}>{s.topic}</Link> {s.is_demo && <Badge kind="demo">DEMO</Badge>} {s.running && <Badge kind="warn">đang chạy…</Badge>}</td>
            <td>{s.pillar}</td><td>{s.mode}</td><td><StateBadge state={s.state} /></td><td>v{s.revision}</td><td className="small">{fmtDate(s.updated_at)}</td></tr>)}</tbody></table>
        {!data?.length && <p className="muted">Chưa có video. Chọn một tin ở trang Săn tin.</p>}
      </Card>
    </>
  );
}
