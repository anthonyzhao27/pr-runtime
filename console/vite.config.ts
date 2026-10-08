import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built bundle is served by the FastAPI controller: index.html for unknown
// paths, /assets/* statically. Keep base '/' and default asset paths.
// Controller reached via `kubectl port-forward svc/controller 18000:8000`.
// Override with PR_RUNTIME_API=http://host:port for a different target.
const API = process.env.PR_RUNTIME_API ?? "http://localhost:18000";

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
      "/api": { target: API, changeOrigin: true },
      "/eval": { target: API, changeOrigin: true },
    },
  },
});
