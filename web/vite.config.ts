import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API to the running Dailies server; `npm run build` writes dist/, which the server serves.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 800 },
  server: {
    port: 5178,
    proxy: { "/api": "http://127.0.0.1:8765", "/media": "http://127.0.0.1:8765" },
  },
});
