/**
 * Create (or reuse) the LiveKit outbound SIP trunk that carries Purity's calls,
 * and write its id back into ai-voice-agent/.env.
 *
 * WHY THIS IS A SCRIPT AND NOT A CONSOLE CLICK-THROUGH
 * ---------------------------------------------------
 * LIVEKIT_SIP_TRUNK_ID sat at the literal string "PASTE" for days while
 * everything downstream reported itself ready. A trunk created by hand in a
 * console is a fact nobody can re-derive later; this leaves the exact call that
 * made it, so the trunk can be rebuilt after an account change without
 * archaeology.
 *
 * THE SHAPE OF THE THING
 * ----------------------
 *   Purity -> Nuraveda -> LiveKit -> [this trunk] -> Plivo -> PSTN
 *
 * Plivo is the carrier. LiveKit needs to know where to send SIP and how to
 * authenticate, which is what a Plivo Zentrunk OUTBOUND trunk provides.
 *
 * WHAT YOU MUST PUT IN ai-voice-agent/.env FIRST
 * ----------------------------------------------
 *   PLIVO_SIP_ADDRESS   the Zentrunk termination domain, e.g. xxxx.zt.plivo.com
 *   PLIVO_SIP_USERNAME  from the Zentrunk credential list
 *   PLIVO_SIP_PASSWORD  from the same credential list
 *
 * Those are read from the file and never printed. Once the trunk exists LiveKit
 * holds the credential, so PLIVO_SIP_PASSWORD can be removed from .env
 * afterwards if you prefer -- the trunk keeps working.
 *
 * Usage:   node scripts/wire_livekit_trunk.mjs [--dry-run]
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// The agent is a sibling repo we deliberately do not vendor, so its SDK is not
// in this repo's node_modules. ESM ignores NODE_PATH, so resolve it by path.
const AGENT_DIR = existsSync(resolve(process.cwd(), "..", "ai-voice-agent"))
  ? resolve(process.cwd(), "..", "ai-voice-agent")
  : resolve(process.cwd(), "..", "..", "ai-voice-agent");
const ENV_PATH = join(AGENT_DIR, ".env");

const sdkEntry = join(AGENT_DIR, "node_modules", "livekit-server-sdk", "dist", "index.js");
if (!existsSync(sdkEntry)) {
  console.error(`livekit-server-sdk not found at ${sdkEntry}`);
  process.exit(1);
}
const { SipClient } = await import(pathToFileURL(sdkEntry).href);

// The number Plivo KYC approved. E.164, because that is what LiveKit stores and
// what the carrier presents as caller ID.
const FROM_NUMBER = "+918031905525";
const TRUNK_NAME = "purity-plivo-outbound";

const DRY = process.argv.includes("--dry-run");

function readEnv(path) {
  const out = {};
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const i = line.indexOf("=");
    if (i < 1) continue;
    out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

/** Replace or append a key in a .env file, preserving everything else. */
function setEnvValue(path, key, value) {
  const src = readFileSync(path, "utf8");
  const re = new RegExp(`^${key}=.*$`, "m");
  const next = re.test(src)
    ? src.replace(re, `${key}=${value}`)
    : src.replace(/\n*$/, `\n${key}=${value}\n`);
  writeFileSync(path, next, "utf8");
}

const env = readEnv(ENV_PATH);

const missing = ["PLIVO_SIP_ADDRESS", "PLIVO_SIP_USERNAME", "PLIVO_SIP_PASSWORD"]
  .filter((k) => !env[k] || env[k] === "PASTE");
if (missing.length) {
  console.error(
    "Cannot create the trunk. Missing from ai-voice-agent/.env: " +
      missing.join(", ") +
      "\n\nGet them from Plivo -> Zentrunk -> Outbound Trunks:" +
      "\n  1. create an outbound trunk" +
      "\n  2. attach a credential list (username + password)" +
      "\n  3. copy the termination domain and those credentials into .env" +
      "\n\nValues are read from the file and never printed.",
  );
  process.exit(1);
}

const host = (env.LIVEKIT_URL || "")
  .replace(/^wss:/, "https:")
  .replace(/^ws:/, "http:");
const sip = new SipClient(host, env.LIVEKIT_API_KEY, env.LIVEKIT_API_SECRET);

// Idempotent: running this twice must not leave two trunks presenting the same
// caller ID. Duplicate outbound paths are how a system ends up dialling twice.
const existing = (await sip.listSipOutboundTrunk()).find(
  (t) => t.name === TRUNK_NAME,
);
if (existing) {
  console.log(`  trunk already exists: ${existing.sipTrunkId} (${existing.name})`);
  if (!DRY) setEnvValue(ENV_PATH, "LIVEKIT_SIP_TRUNK_ID", existing.sipTrunkId);
  console.log("  LIVEKIT_SIP_TRUNK_ID written. Restart nuraveda-voice to pick it up.");
  process.exit(0);
}

console.log(`  creating outbound trunk "${TRUNK_NAME}"`);
console.log(`    address    : ${env.PLIVO_SIP_ADDRESS}`);
console.log(`    caller id  : ${FROM_NUMBER}`);
console.log(`    auth user  : ${env.PLIVO_SIP_USERNAME}`);
console.log(`    auth pass  : (read from .env, not shown)`);

if (DRY) {
  console.log("\n  --dry-run: nothing was created.");
  process.exit(0);
}

const trunk = await sip.createSipOutboundTrunk(
  TRUNK_NAME,
  env.PLIVO_SIP_ADDRESS,
  [FROM_NUMBER],
  { authUsername: env.PLIVO_SIP_USERNAME, authPassword: env.PLIVO_SIP_PASSWORD },
);

console.log(`\n  created: ${trunk.sipTrunkId}`);
setEnvValue(ENV_PATH, "LIVEKIT_SIP_TRUNK_ID", trunk.sipTrunkId);
console.log("  LIVEKIT_SIP_TRUNK_ID written to ai-voice-agent/.env");
console.log("  next: restart nuraveda-voice (elevated) so the service reads it.");
