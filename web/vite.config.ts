import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/auth": api,
      "/ask": api,
      "/documents": api,
      "/connectors": api,
      "/jobs": api,
      "/health": api,
    },
  },
});
