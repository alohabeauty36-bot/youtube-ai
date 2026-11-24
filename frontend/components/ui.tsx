"use client";
import Link from "next/link";
import { ReactNode, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export const STATE_LABEL: Record<string, string> = {
  DISCOVERED: "Mới chọn", RESEARCHING: "Đang nghiên cứu", FACT_CHECK_PENDING: "Đã kiểm chứng", SCRIPT_DRAFT: "Bản nháp kịch bản",
  SCRIPT_APPROVED_FOR_RENDER: "Kịch bản đã duyệt", TTS_DONE: "Đã có giọng đọc", ASSETS_READY: "Đủ tư liệu", RENDERING: "Đang dựng",
  QA_PENDING: "Chờ QA", READY_TO_UPLOAD: "Sẵn sàng tải RIÊNG TƯ", UPLOADING: "Đang tải lên", UPLOADED_PRIVATE: "Đã tải RIÊNG TƯ",
  TELEGRAM_NOTIFIED: "Đã báo Telegram", EDITOR_REVIEW: "Chờ chủ kênh duyệt", APPROVED_PRIVATE: "Đã duyệt (vẫn riêng tư)",
  REVISION_REQUESTED: "Đã yêu cầu sửa", REJECTED: "Đã loại", PUBLISHED_MANUALLY_OBSERVED: "Chủ kênh đã tự công khai",
  NEEDS_REVIEW: "Cần người xử lý", FAILED_RETRYABLE: "Lỗi tạm thời", FAILED_PERMANENT: "Lỗi",
};

export function StateBadge({ state }: { state: string }) {
  const cls = state.startsWith("FAILED") ? "bad" : state === "NEEDS_REVIEW" ? "warn" :
    ["EDITOR_REVIEW", "APPROVED_PRIVATE", "UPLOADED_PRIVATE", "READY_TO_UPLOAD"].includes(state) ? "ok" : "info";
  return <span className={`badge ${cls}`} title={state}>{STATE_LABEL[state] || state}</span>;
}

export function Badge({ kind = "info", children }: { kind?: "ok" | "warn" | "bad" | "info" | "demo" | "master"; children: ReactNode }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

export function Card({ title, children, right }: { title?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="card">
      {(title || right) && <div className="card-h"><h3>{title}</h3><div>{right}</div></div>}
      {children}
    </section>
  );
}

export function ErrorBox({ error }: { error: any }) {
  if (!error) return null;
  const reasons = error?.detail?.reasons || error?.reasons;
  return (
    <div className="error">
      <b>Lỗi:</b> {String(error.message || error)}
      {Array.isArray(reasons) && <ul>{reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>}
    </div>
  );
}

export function useLoad<T = any>(path: string | null, pollMs?: (d: T | null) => number | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<any>(null);
  const load = useCallback(async () => {
    if (!path) return;
    try { setData(await api<T>(path)); setError(null); } catch (e) { setError(e); }
  }, [path]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const ms = pollMs ? pollMs(data) : null;
    if (!ms) return;
    const t = setTimeout(load, ms);
    return () => clearTimeout(t);
  }, [data, load, pollMs]);
  return { data, error, reload: load, setData };
}

export function StoryTabs({ id, active }: { id: string; active: "editor" | "media" | "review" }) {
  const tabs = [["editor", "Biên tập & Audio", `/story/?id=${id}`], ["media", "Timeline & Tư liệu", `/story/media/?id=${id}`],
    ["review", "Xem trước · SEO · YouTube riêng tư", `/story/review/?id=${id}`]] as const;
  return <nav className="tabs">{tabs.map(([k, l, h]) => <Link key={k} href={h} className={k === active ? "on" : ""}>{l}</Link>)}</nav>;
}

export function DemoBanner({ show }: { show?: boolean }) {
  if (!show) return null;
  return <div className="demo-banner">DEMO — dữ liệu giả lập, adapter giả lập. Không có upload YouTube hay tin nhắn Telegram thật.</div>;
}

/** Kết quả một lượt "Quét tin ngay": tổng số, cảnh báo, và lý do từng nguồn lỗi. */
export function ScanResult({ r }: { r: any }) {
  if (!r) return null;
  const bad = (r.sources || []).filter((s: any) => !s.ok);
  const good = (r.sources || []).filter((s: any) => s.ok);
  return (
    <div className={r.created > 0 ? "hint" : "error"} style={{ marginBottom: 12 }}>
      <div><b>Kết quả quét:</b> lấy {r.fetched ?? 0} bài · thêm mới {r.created ?? 0} · trùng {r.duplicates ?? 0} · chặn {r.blocked ?? 0}
        {good.length > 0 && <> · {good.length} nguồn đạt</>}{bad.length > 0 && <> · {bad.length} nguồn lỗi</>}</div>
      {(r.warnings || []).length > 0 && <ul className="small">{r.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>}
      {bad.length > 0 && <ul className="small">{bad.map((s: any, i: number) => <li key={i}><b>{s.source}</b>: {s.error}</li>)}</ul>}
      {r.created === 0 && r.duplicates > 0 && !bad.length && <div className="small">Không có tin mới so với lần quét trước.</div>}
    </div>
  );
}
