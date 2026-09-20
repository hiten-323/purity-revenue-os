import { NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/session";

/** Drop the session cookie. Stateless sessions cannot be revoked server-side,
 *  so rotating DASHBOARD_SESSION_SECRET is what invalidates every session at
 *  once -- this only ends the one in this browser. */
export async function POST() {
  const response = NextResponse.json({ status: "ok" });
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: 0,
  });
  return response;
}
