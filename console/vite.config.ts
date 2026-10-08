import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built bundle is served by the FastAPI controller: index.html for unknown
// paths, /assets/* statically. Keep base '/' and default asset paths.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../controller/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      // Controller reached via `kubectl port-forward svc/controller 18000:8000`.
      "/api": { target: "http://localhost:18000", changeOrigin: true },
      "/eval": { target: "http://localhost:18000", changeOrigin: true },
    },
  },
});
