import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone", // a self-contained server for the container image
  poweredByHeader: false,
  reactStrictMode: true,
  agentRules: false, // `next dev` would otherwise write an AGENTS.md into the repository
};

export default config;
