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
    throw new Error(`[ecosystem] could not read ${file}: ${e.message}`);
  }
  return out;
}

const ROOT = __dirname;
const ENV = loadEnv(path.join(ROOT, "backend", ".env"));
const need = (k) => {
  const v = ENV[k] || process.env[k] || "";
  if (!v || !v.trim()) {
    throw new Error(`[ecosystem] critical secret ${k} is missing from backend/.env`);
  }
  return v.trim();
};

const PYTHON = process.env.PYTHON_BIN || "python";
const CLOUDFLARED = process.env.CLOUDFLARED_BIN || "cloudflared";
const BACKEND_DIR = path.join(ROOT, "backend");
const FRONTEND_DIR = path.join(ROOT, "frontend");
const NURAVEDA_DIR = path.resolve(ROOT, "..", "ai-voice-agent");

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
        API_ADMIN_SECRET: need("API_ADMIN_SECRET"),
        DND_SUPPRESSION_FILE: need("DND_SUPPRESSION_FILE"),
        AUTO_WARM_ENABLED: "0",
        SMART_OUTREACH_ENABLED: "1",
        AUTO_OUTREACH_ENABLED: "1",
        AISENSY_ENABLED: "0",
        WHATSAPP_TEMPLATE: ENV.WHATSAPP_TEMPLATE || "",
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
        DND_SUPPRESSION_FILE: need("DND_SUPPRESSION_FILE"),
        AUTO_WARM_ENABLED: "1",
        SMART_OUTREACH_ENABLED: "1",
        AUTO_OUTREACH_ENABLED: "1",
        AISENSY_ENABLED: "0",
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
        AUTO_OUTREACH_ENABLED: "0",
        SMART_OUTREACH_ENABLED: "0",
        OUTREACH_INTERVAL_SECONDS: "900",
        OUTREACH_BATCH_SIZE: "20",
        SENDER_EMAIL: ENV.SENDER_EMAIL || "connect@purepantryprovisions.com",
        SENDER_NAME: ENV.SENDER_NAME || "Hiten Jain | Pure Pantry Provisions",
        ZOHO_APP_PASSWORD: need("ZOHO_APP_PASSWORD"),
        AISENSY_ENABLED: "0",
        AISENSY_API_KEY: need("AISENSY_API_KEY"),
        WHATSAPP_TEMPLATE: ENV.WHATSAPP_TEMPLATE || "",
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
      env: {
        NODE_ENV: "production",
        API_ADMIN_SECRET: need("API_ADMIN_SECRET"),
      },
    },
    {
      name: "purity-tunnel",
      script: CLOUDFLARED,
      args: `tunnel --config ${process.env.CLOUDFLARED_CONFIG || path.join(process.env.USERPROFILE || process.env.HOME || "", ".cloudflared", "config.yml")} run purity-beans`,
      interpreter: "none",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
    },
    {
      name: "nuraveda-voice",
      script: "src/server.js",
      cwd: NURAVEDA_DIR,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      min_uptime: 5000,
      env: {
        NODE_ENV: "production",
      },
    },
    {
      name: "nuraveda-voice-agent",
      script: "src/livekit-agent.js",
      args: "start",
      cwd: NURAVEDA_DIR,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      min_uptime: 5000,
      env: {
        NODE_ENV: "production",
      },
    },
  ],
};
