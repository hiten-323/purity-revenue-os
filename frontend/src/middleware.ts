import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/session";

/**
 * Two jobs, in this order: let nobody in without a session, then add the
 * backend's admin secret to requests from whoever got in.
 *
 * WHY THE GATE
 * ------------
 * dashboard.p3online.in is public through the Cloudflare tunnel. This file
 * previously said so itself -- that it "does not authenticate the user",
 * that "anyone who can load the dashboard gets the secret applied on their
 * behalf", and that the real perimeter belonged at the edge. That perimeter
 * was never configured. Measured 2026-09-19: GET /api/v1/b2b/leads answered
 * 200 to an unauthenticated request from the open internet, exposing the
 * whole lead book and every admin route behind it.
 *
 * Cloudflare Access remains the better answer and is unchanged by this: put
 * it in front and this gate simply never sees an anonymous request. It needs
 * an API token this machine does not have, and setting one up means signing
 * into the Cloudflare account, so the control is implemented here instead of
 * being left as a recommendation.
 *
 * FAIL CLOSED
 * -----------
 * Missing configuration blocks rather than allows. A dashboard that is
 * briefly unreachable is an inconvenience; one that is briefly public is the
 * incident this exists to end. The login page names the missing variable so
 * the cause is never a mystery.
 */
const PUBLIC_PATHS = ["/login", "/api/auth/login", "/api/auth/logout"];

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  const sessionSecret = process.env.DASHBOARD_SESSION_SECRET ?? "";
  const authed = await sessionIsValid(
    request.cookies.get(SESSION_COOKIE)?.value,
    sessionSecret,
  );

  if (!authed) {
    // An API client gets a status it can act on; a browser gets the form.
    if (pathname.startsWith("/api/")) {
      return NextResponse.json(
        { detail: "dashboard session required" },
        { status: 401 },
      );
    }
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(login);
  }

  if (!pathname.startsWith("/api/")) return NextResponse.next();

  const secret = process.env.API_ADMIN_SECRET ?? "";
  if (!secret) {
    // Loud rather than silent. Without this the dashboard fails with an
    // opaque 401 from the backend and the cause looks like a backend problem
    // when it is a missing variable on this process.
    console.error(
      "[middleware] API_ADMIN_SECRET is not set on the frontend process — " +
        "proxied /api requests will be rejected by the backend with 401. " +
        "It is supplied by ecosystem.config.js from backend/.env.",
    );
    return NextResponse.next();
  }

  const headers = new Headers(request.headers);
  headers.set("x-api-admin-secret", secret);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Everything except Next's own static output and the icons, which carry no
  // business data and are fetched before any session exists.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|icon.png|apple-icon.png).*)",
  ],
};
