import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The ops API (127.0.0.1:8820) serves the built app; in dev, Vite proxies the read-only API to it.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8820" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
});
