import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const api = process.env.API_URL ?? "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${api}/:path*`,
      },
      // eBay OAuth callback comes directly to the browser — proxy to backend
      {
        source: "/auth/ebay/callback",
        destination: `${api}/auth/ebay/callback`,
      },
      // Legal pages served by the backend
      {
        source: "/privacy-policy",
        destination: `${api}/privacy-policy`,
      },
      {
        source: "/terms",
        destination: `${api}/terms`,
      },
    ];
  },
};

export default nextConfig;
