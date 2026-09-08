// Credentials come from backend/.env, never from this file.
const fs = require("fs");
const path = require("path");

function loadEnv(file) {
  const out = {};
  try {
    for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const i = line.indexOf("=");
      if (i < 1) continue;
      out[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
    }
  } catch (e) {
    console.error(`[ecosystem] could not read ${file}: ${e.message} — processes start WITHOUT credentials`);
  }
  return out;
}

const ROOT = __dirname;
const ENV = loadEnv(path.join(ROOT, "backend", ".env"));
const need = (k) => {
  const v = ENV[k] || process.env[k] || "";
  if (!v) console.error(`[ecosystem] ${k} is missing from backend/.env`);
  return v;
};

const PYTHON = "C:\\Users\\hiten\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";
const CLOUDFLARED = "C:\\Program Files (x86)\\cloudflared\\cloudflared.exe";
const BACKEND_DIR = path.join(ROOT, "backend");
const FRONTEND_DIR = path.join(ROOT, "frontend");

module.exports = {
  apps: [
    {
      name: "purity-api",
      script: PYTHON,
      args: "run_server.py",
      cwd: BACKEND_DIR,
      interpreter: "none",
      autorestart: true,
      restart_delay: 1000,
      max_restarts: 50,
      min_uptime: 3000,
      env: {
        PYTHONUNBUFFERED: "1",
        AUTO_WARM_ENABLED: "0",
        SMART_OUTREACH_ENABLED: "0",
        AUTO_OUTREACH_ENABLED: "0",
        SENDER_EMAIL: ENV.SENDER_EMAIL || "connect@purepantryprovisions.com",
        SENDER_NAME: ENV.SENDER_NAME || "Hiten Jain | Pure Pantry Provisions",
        ZOHO_APP_PASSWORD: need("ZOHO_APP_PASSWORD"),
        CEREBRAS_API_KEY: need("CEREBRAS_API_KEY"),
        SHOPIFY_STORE: ENV.SHOPIFY_STORE || "",
        SHOPIFY_TOKEN: need("SHOPIFY_TOKEN"),
        GOOGLE_MAPS_API_KEY: need("GOOGLE_MAPS_API_KEY"),
      },
    },
    {
      name: "purity-worker",
      script: PYTHON,
      args: "worker.py",
      cwd: BACKEND_DIR,
      interpreter: "none",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      min_uptime: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
        AUTO_WARM_ENABLED: "1",
        SMART_OUTREACH_ENABLED: "0",
        AUTO_OUTREACH_ENABLED: "0",
        SENDER_EMAIL: ENV.SENDER_EMAIL || "connect@purepantryprovisions.com",
        SENDER_NAME: ENV.SENDER_NAME || "Hiten Jain | Pure Pantry Provisions",
        ZOHO_APP_PASSWORD: need("ZOHO_APP_PASSWORD"),
        CEREBRAS_API_KEY: need("CEREBRAS_API_KEY"),
        GOOGLE_MAPS_API_KEY: need("GOOGLE_MAPS_API_KEY"),
      },
    },
    {
      name: "purity-outreach",
      script: PYTHON,
      args: "smart_outreach_worker.py",
      cwd: BACKEND_DIR,
      interpreter: "none",
      autorestart: false,
      env: {
        PYTHONUNBUFFERED: "1",
        // Fail closed. This stays OFF until the controlled send gate is passed.
        AUTO_OUTREACH_ENABLED: "0",
        SMART_OUTREACH_ENABLED: "0",
        OUTREACH_INTERVAL_SECONDS: "900",
        OUTREACH_BATCH_SIZE: "20",
        SENDER_EMAIL: ENV.SENDER_EMAIL || "connect@purepantryprovisions.com",
        SENDER_NAME: ENV.SENDER_NAME || "Hiten Jain | Pure Pantry Provisions",
        ZOHO_APP_PASSWORD: need("ZOHO_APP_PASSWORD"),
        AISENSY_API_KEY: need("AISENSY_API_KEY"),
        AISENSY_CAMPAIGN_NAME: ENV.AISENSY_CAMPAIGN_NAME || "",
      },
    },
    {
      name: "purity-beans",
      script: "node",
      args: "node_modules/next/dist/bin/next start -p 3001 -H 0.0.0.0",
      cwd: FRONTEND_DIR,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
      env: { NODE_ENV: "production" },
    },
    {
      name: "purity-tunnel",
      script: CLOUDFLARED,
      args: "tunnel --config C:\\Users\\hiten\\.cloudflared\\config.yml run purity-beans",
      interpreter: "none",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
    },
  ],
};
