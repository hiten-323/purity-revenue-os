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

const PYTHON = process.env.PYTHON_BIN || "python";
const CLOUDFLARED = process.env.CLOUDFLARED_BIN || "cloudflared";
const BACKEND_DIR = path.join(ROOT, "backend");
const FRONTEND_DIR = path.join(ROOT, "frontend");
// Sibling repo, not vendored — see the nuraveda-voice app below.
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
        // Every state-changing /api/v1 route is refused without this
        // (app/api/auth.py, wired as middleware in main.py). It fails CLOSED:
        // unset means 503 on all 103 mutating routes, not open access.
        //
        // Scope, measured rather than assumed: cloudflared's ingress lists
        // api.p3online.in -> :8003, but that hostname is NXDOMAIN, so the
        // backend is NOT directly reachable from the internet today. What IS
        // public is dashboard.p3online.in, which proxies /api to the same
        // routes -- see frontend/src/middleware.ts for why that means this
        // secret is defence in depth, not the perimeter.
        API_ADMIN_SECRET: need("API_ADMIN_SECRET"),
        DND_SUPPRESSION_FILE: need("DND_SUPPRESSION_FILE"),
        AUTO_WARM_ENABLED: "0",
        SMART_OUTREACH_ENABLED: "0",
        AUTO_OUTREACH_ENABLED: "0",
        // The gateway router can reach the transport from an API route, so
        // the same kill switch has to exist here or "off" would only be off
        // in the worker.
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
        // The do-not-call scrub list. preference_registry fails closed without
        // it, so no cold call is authorised until this points at a real file.
        DND_SUPPRESSION_FILE: need("DND_SUPPRESSION_FILE"),
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
        // The WhatsApp transport is AiSensy's API-campaign endpoint. It stays
        // OFF here: the number is shared with seven Live transactional
        // campaigns carrying real orders, and their quality rating is the
        // same rating a cold-prospecting complaint damages.
        AISENSY_ENABLED: "0",
        AISENSY_API_KEY: need("AISENSY_API_KEY"),
        // A LIVE AiSensy API campaign name, and it must be a MARKETING one
        // created for prospecting. Pointing this at Order Confirmed or
        // Abandoned Cart would send cold B2B outreach through a UTILITY
        // template — a Meta policy violation, and the fastest way to lose
        // the rating the order flows depend on.
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
        // Injected into proxied /api requests by frontend/src/middleware.ts so
        // the dashboard's own buttons keep working against the gated API.
        //
        // Deliberately NOT NEXT_PUBLIC_: that prefix inlines a value into the
        // browser bundle, which would publish the secret to anyone who opens
        // devtools on dashboard.p3online.in.
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
      // The AI voice agent. A separate repo, deliberately a SIBLING of this
      // one — it is upstream code we do not own, and vendoring it would make
      // every upstream change a merge.
      //
      // It is listed here rather than started with `pm2 start` because a
      // dump does not survive a reboot on this machine; only this file does.
      // That is the same trap that once left production running the legacy
      // jules_session tree after a restart.
      //
      // Its own .env supplies LiveKit, Neon and the shared secret. Nothing
      // from this file is injected: two sources for one value is how the
      // Purity/Nuraveda secret pair drifted apart twice in one afternoon.
      name: "nuraveda-voice",
      script: "src/server.js",
      cwd: NURAVEDA_DIR,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 20,
      min_uptime: 5000,
      env: {
        // DISPATCH_MODE is NOT set here on purpose. It lives in the agent's
        // own .env at dry_run, and flipping it to "live" should be a
        // deliberate edit to that file — not something this config can do
        // silently on a restart.
        NODE_ENV: "production",
      },
    },
    {
      // The actual conversational worker (STT/LLM/TTS loop). server.js only
      // queues a dispatch job with LiveKit Cloud and originates the SIP leg
      // — it never joins the room itself. Without this process registered
      // and running, LiveKit has nothing to hand the dispatch job to: the
      // phone rings and the SIP call connects (server.js's job succeeded),
      // but no agent ever joins to speak. Found 2026-09-15 via a real test
      // call to the founder's own number that rang and stayed silent —
      // this was never listed here before that call, on any process.
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
