import { NextResponse } from "next/server";

import { SESSION_COOKIE, issueSession, passwordMatches } from "@/lib/session";

/**
 * Exchange the dashboard password for a signed session cookie.
 *
 * The password never reaches the browser bundle and is never logged: it is
 * read from DASHBOARD_PASSWORD on the server and compared over HMACs.
 *
 * Throttling is per-process and in-memory, which is the right size for a
 * single self-hosted dashboard: it turns an online guessing attack into a
 * slow one without adding a store to run. It resets on restart, and that is
 * an accepted limit, not an oversight.
 */
const ATTEMPT_WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 8;
const attempts = new Map<string, { n: number; first: number }>();

function tooManyAttempts(ip: string): boolean {
  const now = Date.now();
  const rec = attempts.get(ip);
  if (!rec || now - rec.first > ATTEMPT_WINDOW_MS) {
    attempts.set(ip, { n: 1, first: now });
    return false;
  }
  rec.n += 1;
  return rec.n > MAX_ATTEMPTS;
}

export async function POST(request: Request) {
  const expected = process.env.DASHBOARD_PASSWORD ?? "";
  const secret = process.env.DASHBOARD_SESSION_SECRET ?? "";
  if (!expected || !secret) {
    // Named precisely: the operator needs to know WHICH variable is missing,
    // and a visitor learns nothing useful from the name of a config key.
    return NextResponse.json(
      {
        detail:
          "dashboard login is not configured: set DASHBOARD_PASSWORD and " +
          "DASHBOARD_SESSION_SECRET in frontend/.env.local, then restart purity-beans",
      },
      { status: 503 },
    );
  }

  const ip =
    request.headers.get("cf-connecting-ip") ??
    request.headers.get("x-forwarded-for")?.split(",")[0].trim() ??
    "local";
  if (tooManyAttempts(ip)) {
    return NextResponse.json(
      { detail: "too many attempts — wait 15 minutes" },
      { status: 429 },
    );
  }

  let password = "";
  try {
    password = String(((await request.json()) as { password?: unknown }).password ?? "");
  } catch {
    return NextResponse.json({ detail: "expected JSON body" }, { status: 400 });
  }

  if (!(await passwordMatches(password, expected, secret))) {
    // One message for wrong-password and for empty: nothing to probe.
    return NextResponse.json({ detail: "incorrect password" }, { status: 401 });
  }

  attempts.delete(ip);
  const { value, maxAge } = await issueSession(secret);
  const response = NextResponse.json({ status: "ok" });
  response.cookies.set(SESSION_COOKIE, value, {
    httpOnly: true, // unreadable by page scripts, so an XSS cannot lift it
    sameSite: "lax",
    secure: true, // the dashboard is only reached over https
    path: "/",
    maxAge,
  });
  return response;
}
