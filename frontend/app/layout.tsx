import "./globals.css";
import Link from "next/link";
import type { ReactNode } from "react";

export const metadata = { title: "EV Creator Studio", description: "Tòa soạn video AI có người duyệt cuối" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <div className="shell">
          <aside className="side">
            <h1>⚡ EV Creator Studio</h1>
            <small>Tòa soạn video có người duyệt cuối</small>
            <nav>
              <Link href="/">Bảng điều khiển</Link>
              <Link href="/news">Săn tin</Link>
              <Link href="/stories">Video đang làm</Link>
              <Link href="/analytics">Phân tích & chi phí</Link>
              <Link href="/errors">Nhật ký lỗi</Link>
              <Link href="/settings">Thiết lập & tài khoản</Link>
            </nav>
            <div className="lock">🔒 Mọi video được tải lên YouTube ở trạng thái <b>RIÊNG TƯ</b>. Ứng dụng không có chức năng công khai —
              chủ kênh tự đổi trạng thái trên YouTube Studio.</div>
          </aside>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
