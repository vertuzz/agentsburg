import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],

  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      // Proxy /api requests to the backend
      "/api": {
        target: "http://backend:8000",
        changeOrigin: true,
        // No rewrite — /api prefix is kept, backend serves at /api
      },
      // Proxy /mcp requests to the backend MCP endpoint
      "/mcp": {
        target: "http://backend:8000",
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
