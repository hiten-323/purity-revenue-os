import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // 127.0.0.1, NOT localhost: Node 18+ resolves "localhost" to ::1 (IPv6)
        // first, but uvicorn binds 0.0.0.0 (IPv4 only), so those attempts got
        // ECONNREFUSED. That made the API proxy fail intermittently and the
        // dashboard flap to "Offline" even while the API was healthy.
        destination: "http://127.0.0.1:8003/api/:path*",
      },
    ];
  },
};

export default nextConfig;
