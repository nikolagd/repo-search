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

function readRootEnvValue(key) {
  if (process.env[key]) {
    return process.env[key];
  }

  const candidates = [
    path.resolve(process.cwd(), ".env"),
    path.resolve(process.cwd(), "..", ".env"),
  ];

  for (const filePath of candidates) {
    const value = parseEnvValue(filePath, key);

    if (value) {
      return value;
    }
  }

  return "";
}

const apiToken = readRootEnvValue("API_TOKEN");
const apiProxyTarget = readRootEnvValue("API_PROXY_TARGET") || "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget,
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
