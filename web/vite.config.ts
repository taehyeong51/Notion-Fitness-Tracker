import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        configure(proxy) {
          proxy.on("proxyReq", (request, incoming) => {
            if (
              incoming.headers.origin === "http://127.0.0.1:5173" ||
              incoming.headers.origin === "http://localhost:5173"
            )
              request.setHeader("Origin", "http://127.0.0.1:8000");
          });
        },
      },
      "/healthz": "http://127.0.0.1:8000",
    },
  },
  build: { target: "es2022" },
});
