import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit / component tests (npm test). End-to-end tests live in e2e/ and run with Playwright (npm run test:e2e).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/unit/setup.ts"],
    restoreMocks: true,
  },
});
