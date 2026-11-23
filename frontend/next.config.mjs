/** @type {import('next').NextConfig} */
// Xuất tĩnh → backend FastAPI phục vụ trực tiếp (một tiến trình, một cổng). Build: python run.py --build-web
const nextConfig = { output: "export", trailingSlash: true, reactStrictMode: true, images: { unoptimized: true } };
export default nextConfig;
