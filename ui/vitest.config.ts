import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
      "server-only": fileURLToPath(new URL("./test/server-only.ts", import.meta.url)),
      "next/cache": fileURLToPath(new URL("./test/next-cache.ts", import.meta.url)),
    },
  },
  test: { include: ["lib/**/*.test.ts", "app/**/*.test.ts"], environment: "node" },
});
