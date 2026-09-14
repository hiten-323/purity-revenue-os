import { NextResponse, type NextRequest } from "next/server";

/**
 * Attach the API admin secret to proxied /api requests, on the server.
 *
 * WHY THIS EXISTS
 * ---------------
 * Every state-changing /api/v1 route is now gated by app/api/auth.py, wired as
 * middleware in the backend's main.py. It fails closed: no secret means 503,
 * a wrong or missing header means 401. The dashboard calls those routes from
 * the browser via the rewrite in next.config.ts, so without this every button
 * in the UI returns 401.
 *
 * WHY IT IS NOT A NEXT_PUBLIC_ VARIABLE
 * -------------------------------------
 * That prefix inlines a value into the browser bundle. The secret would then
 * be readable by anyone who opens devtools on dashboard.p3online.in, which is
 * publicly reachable through the Cloudflare tunnel. Middleware runs on the
 * server, so the header is added after the request leaves the browser and the
 * value is never shipped to it.
 *
 * WHAT THIS DOES NOT DO
 * ---------------------
 * It does not authenticate the user. Anyone who can load the dashboard gets
 * the secret applied on their behalf, so the dashboard remains an open proxy
 * to the same routes.
 *
 * Checked rather than assumed: cloudflared's ingress also lists
 * api.p3online.in -> :8003, but that hostname is NXDOMAIN, so the backend is
 * not directly reachable today. dashboard.p3online.in does resolve and is
 * public. So the secret is defence in depth -- it closes direct API access the
 * moment that DNS record is ever created -- and NOT the perimeter.
 *
 * The perimeter is a real identity check in front of dashboard.p3online.in
 * (Cloudflare Access, or equivalent), configured at the edge rather than here.
 */
export function middleware(request: NextRequest) {
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
  // Only the proxied API surface. Pages and static assets are untouched, so
  // this adds no work to ordinary navigation.
  matcher: "/api/:path*",
};
