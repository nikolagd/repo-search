import fs from "node:fs";
import path from "node:path";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function parseEnvValue(filePath, key) {
  if (!fs.existsSync(filePath)) {
    return "";
  }

  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);

  for (const line of lines) {
    const trimmed = line.trim();

    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const match = trimmed.match(/^([^=]+)=(.*)$/);

    if (match?.[1]?.trim() !== key) {
      continue;
    }

    return match[2].trim().replace(/^["']|["']$/g, "");
  }

  return "";
}

function readApiToken() {
  if (process.env.API_TOKEN) {
    return process.env.API_TOKEN;
  }

  const candidates = [
    path.resolve(process.cwd(), ".env"),
    path.resolve(process.cwd(), "..", ".env"),
  ];

  for (const filePath of candidates) {
    const token = parseEnvValue(filePath, "API_TOKEN");

    if (token) {
      return token;
    }
  }

  return "";
}

const apiToken = readApiToken();

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            if (apiToken) {
              proxyReq.setHeader("X-API-Key", apiToken);
            }
          });
        },
      },
    },
  },
});
