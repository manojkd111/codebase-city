import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs on :8000. Proxying keeps the browser same-origin, so the frontend
// never hard-codes a backend host and the WebSocket URL is just location.host.
// CBC_API points the proxy at another API (e.g. a second instance with a scratch database).
const api = process.env.CBC_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
