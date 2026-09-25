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

function resolveNuravedaDir() {
  const candidates = [
    process.env.NURAVEDA_DIR,
    ENV.NURAVEDA_DIR,
    path.resolve(ROOT, "..", "ai-voice-agent"),
    path.resolve(ROOT, "..", "ai-voice-agent-purity"),
    path.join(ROOT, "ai-voice-agent"),
  ].filter(Boolean);
  for (const c of candidates) {
    const abs = path.resolve(c);
    if (fs.existsSync(path.join(abs, "src", "server.js"))) return abs;
    if (fs.existsSync(path.join(abs, "src", "livekit-agent.js"))) return abs;
  }
  return null;
}

const NURAVEDA_DIR = resolveNuravedaDir();

function mustExist(label, filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`[ecosystem] ${label} missing at ${filePath}`);
  }
  return filePath;
}

mustExist("backend run_server.py", path.join(BACKEND_DIR, "run_server.py"));
mustExist("backend worker.py", path.join(BACKEND_DIR, "worker.py"));
if (NURAVEDA_DIR) {
  mustExist("nuraveda server", path.join(NURAVEDA_DIR, "src", "server.js"));
  mustExist("nuraveda livekit agent", path.join(NURAVEDA_DIR, "src", "livekit-agent.js"));
} else {
  console.warn(
    "[ecosystem] NURAVEDA_DIR not found -- omitting nuraveda-voice apps. " +
      "Set NURAVEDA_DIR to enable LiveKit. Purity API/worker still start."
  );
}

const apps = [
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
        SMART_OUTREACH_ENABLED: "0",
        AUTO_OUTREACH_ENABLED: "0",
        AISENSY_ENABLED: "0",
        AI_CALLING_ENABLED: "0",
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
        SMART_OUTREACH_ENABLED: "0",
        AUTO_OUTREACH_ENABLED: "1",
        AUTO_AI_CALLING_ENABLED: "0",
        AISENSY_ENABLED: "0",
        AI_CALLING_ENABLED: "1",
        SENDER_EMAIL: ENV.SENDER_EMAIL || "connect@purepantryprovisions.com",
        SENDER_NAME: ENV.SENDER_NAME || "Hiten Jain | Pure Pantry Provisions",
        ZOHO_APP_PASSWORD: need("ZOHO_APP_PASSWORD"),
        CEREBRAS_API_KEY: need("CEREBRAS_API_KEY"),
        GOOGLE_MAPS_API_KEY: need("GOOGLE_MAPS_API_KEY"),
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
];

if (NURAVEDA_DIR) {
  apps.push(
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
        LIVEKIT_INIT_TIMEOUT_MS: process.env.LIVEKIT_INIT_TIMEOUT_MS || ENV.LIVEKIT_INIT_TIMEOUT_MS || "60000",
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
        LIVEKIT_INIT_TIMEOUT_MS: process.env.LIVEKIT_INIT_TIMEOUT_MS || ENV.LIVEKIT_INIT_TIMEOUT_MS || "60000",
        NURAVEDA_PROFILE: ENV.NURAVEDA_PROFILE || "purity-coffee-b2b",
        SARVAM_TTS_MODEL: ENV.SARVAM_TTS_MODEL || "bulbul:v3",
        SARVAM_TTS_SPEAKER: ENV.SARVAM_TTS_SPEAKER || "shreya",
        TTS_PACE: ENV.TTS_PACE || "0.96",
        TTS_TEMPERATURE: ENV.TTS_TEMPERATURE || "0.6",
        TTS_STREAMING: ENV.TTS_STREAMING || "1",
        ENDPOINTING_MIN_MS: ENV.ENDPOINTING_MIN_MS || "400",
        VAD_MIN_SILENCE_MS: ENV.VAD_MIN_SILENCE_MS || "260",
        MIN_INTERRUPTION_MS: ENV.MIN_INTERRUPTION_MS || "500",
        MIN_INTERRUPTION_WORDS: ENV.MIN_INTERRUPTION_WORDS || "1",
        AEC_WARMUP_MS: ENV.AEC_WARMUP_MS || "3000",
        MAX_AGENT_RESPONSE_WORDS: ENV.MAX_AGENT_RESPONSE_WORDS || "15",
      },
    }
  );
}

module.exports = { apps };
