import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export const DEV_PORT = 3001;

export default defineConfig(({ mode }) => {
  // Third arg "" loads every var, not just the VITE_-prefixed ones, so the
  // proxy target can be set without exposing it to client code.
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.BACKEND_PROXY_TARGET || "http://127.0.0.1:8001";

  // Going through the proxy keeps API requests same-origin in development, so
  // cookies work and CORS never applies.
  const proxy = { "/api": { target: backend, changeOrigin: true } };

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      port: DEV_PORT,
      // Fail loudly instead of silently moving to 3002.
      strictPort: true,
      proxy,
    },
    preview: { port: DEV_PORT, strictPort: true, proxy },
    build: {
      outDir: "dist",
      sourcemap: true,
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.js"],
      css: false,
      restoreMocks: true,
    },
  };
});
