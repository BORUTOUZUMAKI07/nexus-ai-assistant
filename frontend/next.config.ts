import type { NextConfig } from "next";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/conversations/:path*",
        destination: `${BACKEND_URL}/api/v1/conversations/:path*`,
      },
      {
        source: "/api/conversations",
        destination: `${BACKEND_URL}/api/v1/conversations`,
      },
      {
        source: "/api/files/:path*",
        destination: `${BACKEND_URL}/api/v1/files/:path*`,
      },
      {
        source: "/api/files",
        destination: `${BACKEND_URL}/api/v1/files`,
      },
      {
        source: "/api/usage/:path*",
        destination: `${BACKEND_URL}/api/v1/usage/:path*`,
      },
      {
        source: "/api/usage",
        destination: `${BACKEND_URL}/api/v1/usage`,
      },
      {
        source: "/api/settings/:path*",
        destination: `${BACKEND_URL}/api/v1/settings/:path*`,
      },
      {
        source: "/api/settings",
        destination: `${BACKEND_URL}/api/v1/settings`,
      },
      {
        source: "/api/auth/:path*",
        destination: `${BACKEND_URL}/api/v1/auth/:path*`,
      },
    ];
  },
};

export default nextConfig;
