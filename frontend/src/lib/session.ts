/**
 * Dashboard session cookie: signed, stateless, verified on the server.
 *
 * WHY THIS EXISTS
 * ---------------
 * dashboard.p3online.in is public through the Cloudflare tunnel, and
 * middleware adds API_ADMIN_SECRET to every proxied /api request. Until this
 * gate, that meant anyone on the internet could read the whole lead book and
 * reach admin routes with the secret applied on their behalf. Measured
 * 2026-09-19: GET /api/v1/b2b/leads answered 200 with no credential at all.
 * middleware.ts's own comment already said the dashboard "remains an open
 * proxy to the same routes" and that the real perimeter belongs at the edge.
 *
 * Cloudflare Access is still the better perimeter and remains the right
 * long-term answer; it needs an API token this machine does not have. This is
 * the same control, implemented where we do have reach.
 *
 * SHAPE
 * -----
 *   cookie = "<expiry-epoch-seconds>.<base64url HMAC-SHA256(secret, "v1|<expiry>")>"
 *
 * Stateless on purpose: no session store to run, and Edge middleware can
 * verify it without a network call. The trade-off is that a stolen cookie
 * stays valid until it expires -- rotating DASHBOARD_SESSION_SECRET
 * invalidates every session at once.
 *
 * Web Crypto rather than node:crypto, so the identical code runs in Edge
 * middleware and in the Node route handler.
 */
const ENCODER = new TextEncoder();
const TTL_SECONDS = 60 * 60 * 24 * 14; // two weeks
export const SESSION_COOKIE = "purity_session";

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    ENCODER.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

function b64url(bytes: ArrayBuffer): string {
  const bin = String.fromCharCode(...new Uint8Array(bytes));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sign(secret: string, expiry: number): Promise<string> {
  const mac = await crypto.subtle.sign(
    "HMAC",
    await hmacKey(secret),
    ENCODER.encode(`v1|${expiry}`),
  );
  return b64url(mac);
}

export async function issueSession(
  secret: string,
): Promise<{ value: string; maxAge: number }> {
  const expiry = Math.floor(Date.now() / 1000) + TTL_SECONDS;
  return { value: `${expiry}.${await sign(secret, expiry)}`, maxAge: TTL_SECONDS };
}

/** Constant time: returning early leaks how much of the signature matched. */
function sameString(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function sessionIsValid(
  cookie: string | undefined,
  secret: string,
): Promise<boolean> {
  if (!cookie || !secret) return false;
  const dot = cookie.indexOf(".");
  if (dot < 1) return false;
  const expiry = Number(cookie.slice(0, dot));
  if (!Number.isFinite(expiry) || expiry <= Math.floor(Date.now() / 1000)) return false;
  return sameString(cookie.slice(dot + 1), await sign(secret, expiry));
}

/**
 * Compare over HMACs rather than the raw strings: equal-length digests mean
 * the comparison cannot leak the password's length either.
 */
export async function passwordMatches(
  supplied: string,
  expected: string,
  secret: string,
): Promise<boolean> {
  if (!expected) return false;
  const k = await hmacKey(secret);
  const a = b64url(await crypto.subtle.sign("HMAC", k, ENCODER.encode(supplied)));
  const b = b64url(await crypto.subtle.sign("HMAC", k, ENCODER.encode(expected)));
  return sameString(a, b);
}
