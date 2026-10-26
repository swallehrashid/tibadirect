import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server forwards /api to the Flask server, so the browser sees one origin in development
// as it does in production and cookies need no cross-site settings.
const apiTarget = process.env.VITE_API_PROXY ?? "http://localhost:5000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": { target: apiTarget, changeOrigin: false } },
  },
  // The only chunk over the default 500 kB warning is AG Grid, which loads on its own, after the page is usable.
  build: { sourcemap: false, target: "es2022", chunkSizeWarningLimit: 1300 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
    testTimeout: 15_000,
  },
});
