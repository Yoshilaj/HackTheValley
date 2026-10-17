import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Any request to /api/* from the frontend is forwarded to the Python backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
