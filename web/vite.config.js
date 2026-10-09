import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173 (team decision 3). The app talks to same-origin paths, proxied:
//   /api/*   -> the agent (AGENT_URL, default http://127.0.0.1:8000)
//   /voice/* -> the Pipecat voice bot (VOICE_URL, default http://127.0.0.1:7860)
// so the browser needs no CORS and a phone on the LAN can use `npm run dev -- --host`.
const AGENT = process.env.AGENT_URL || "http://127.0.0.1:8000";
const VOICE = process.env.VOICE_URL || "http://127.0.0.1:7860";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: AGENT, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/voice": { target: VOICE, changeOrigin: true, rewrite: (p) => p.replace(/^\/voice/, "") },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      "/api": { target: AGENT, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/voice": { target: VOICE, changeOrigin: true, rewrite: (p) => p.replace(/^\/voice/, "") },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.js"],
    include: ["src/**/*.test.{js,jsx}"],
    css: false,
  },
});
