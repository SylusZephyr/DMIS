import type { NextConfig } from "next";

// The platform API (FastAPI, `python scripts/dmis.py serve`) is proxied under
// the same origin so the browser never needs CORS. Override with DIP_API_URL.
const API = process.env.DIP_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/v2/:path*", destination: `${API}/api/v2/:path*` }];
  },
  images: { remotePatterns: [{ protocol: "https", hostname: "**" }] },
};

export default nextConfig;
