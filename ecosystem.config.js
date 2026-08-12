// Credentials come from backend/.env, never from this file.
//
// Seven live secrets were inlined here and this file is tracked in git, so
// every key was readable in history — the same exposure as backend/.env, which
// has now been untracked. This file stays tracked because it holds the process
// definitions, which belong in version control; only the secrets move out.
//
// Parsed by hand rather than via `dotenv` on purpose: pm2 must always be able
// to read this config, and a missing node_module would stop every process from
// starting. A missing .env now yields empty strings and a loud warning instead.
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
    console.error(`[ecosystem] could not read ${file}: ${e.message} — ` +
                  `processes will start WITHOUT credentials`);
  }
  return out;
}

const ENV = loadEnv(path.join(__dirname, "backend", ".env"));
const need = (k) => {
  const v = ENV[k] || process.env[k] || "";
  if (!v) console.error(`[ecosystem] ${k} is missing from backend/.env`);
  return v;
};

const PYTHON = "C:\\Users\\hiten\\AppData\\Local\\Programs\\Python\\Python311\\python.exe";
const CLOUDFLARED = "C:\\Program Files (x86)\\cloudflared\\cloudflared.exe";
const BACKEND_DIR = "C:\\Users\\hiten\\Desktop\\ppp\\claude\\CODE\\purity_beans_ai\\jules_session\\backend";
const FRONTEND_DIR = "C:\\Users\\hiten\\Desktop\\ppp\\claude\\CODE\\purity_beans_ai\\jules_session\\frontend";

module.exports = {
  apps: [
    {
      name: "purity-api",
      script: PYTHON,
      // run_server.py forces the Windows SelectorEventLoop. With the default
      // Proactor loop, uvicorn's accept coroutine dies on WinError 64 ("Accept
      // failed on a socket") — the port stays LISTENING and pm2 shows "online"
      // while the API silently stops accepting every connection.
      args: "run_server.py",
      cwd: BACKEND_DIR,
      interpreter: "none",
      autorestart: true,
      restart_delay: 1000,
      max_restarts: 50,
      min_uptime: 3000,
      env: {
        PYTHONUNBUFFERED: "1",
        // Continuous in-process enrichment starves the ASGI event loop
        // (CPU-bound HTML parsing holds the GIL) and made the dashboard
        // unresponsive after sleep. Off by default; enrichment still runs
        // on-demand from discovery/page actions. Set to "1" to re-enable.
        AUTO_WARM_ENABLED: "0",
        SENDER_EMAIL: "connect@purepantryprovisions.com",
        SENDER_NAME: "Hiten Jain | Pure Pantry Provisions",
        ZOHO_APP_PASSWORD: need("ZOHO_APP_PASSWORD"),
        CEREBRAS_API_KEY: need("CEREBRAS_API_KEY"),
        SHOPIFY_STORE: "purepantryprovisions.myshopify.com",
        SHOPIFY_TOKEN: need("SHOPIFY_TOKEN"),
        GOOGLE_MAPS_API_KEY: need("GOOGLE_MAPS_API_KEY"),
      },
    },
    {
      // Auto-Warm enrichment in its OWN process. It used to run as a thread
      // inside purity-api, where its CPU-bound HTML parsing held the GIL and
      // froze the ASGI event loop (hence AUTO_WARM_ENABLED=0 above). Isolated
      // here it can run continuously without ever touching API latency.
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
        AUTO_WARM_ENABLED: "1",   // enabled ONLY in this isolated process
        SENDER_EMAIL: "connect@purepantryprovisions.com",
        SENDER_NAME: "Hiten Jain | Pure Pantry Provisions",
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
