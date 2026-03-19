import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Forward browser errors to terminal — enables AI agent debugging
  logging: {
    browserToTerminal: true,
  },
  experimental: {
    serverActions: {
      bodySizeLimit: "2mb",
    },
    // Bundle all segment data into single prefetch response
    prefetchInlining: true,
  },
};

export default nextConfig;
