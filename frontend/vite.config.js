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
                target: "http://127.0.0.1:8000",
                changeOrigin: true,
                // No rewrite — /api prefix is kept, backend serves at /api
            },
            // Proxy /v1 requests to the backend REST API
            "/v1": {
                target: "http://127.0.0.1:8000",
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: "dist",
        sourcemap: false,
    },
});
