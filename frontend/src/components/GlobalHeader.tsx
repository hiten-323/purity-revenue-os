"use client";
import React, { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";

// Reports moved out of the nav — analytics belong on the homepage/dashboard.
const NAV = [
  { href: "/dashboard",    label: "Founder Console" },
  { href: "/dashboard#approval-center", label: "Approval Center" },
  { href: "/whatsapp",     label: "WhatsApp Queue" },
  { href: "/government",   label: "Government" },
  { href: "/settings",     label: "Settings" },
];

export default function GlobalHeader() {
  const path = usePathname();
  const [live, setLive]           = useState(false);
  const [lastSync, setLastSync]   = useState<string | null>(null);
  const [countdown, setCountdown] = useState(30);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  const [retrying, setRetrying]   = useState(false);
  const [progress, setProgress]   = useState(0);
  const [reconnected, setReconnected] = useState(false);

  // Stable refs so ping useCallback can always access latest values
  const progressRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const progressValRef = useRef(0);

  const stopProgressBar = useCallback(() => {
    if (progressRef.current) { clearInterval(progressRef.current); progressRef.current = null; }
  }, []);

  const startProgressBar = useCallback(() => {
    stopProgressBar();
    progressValRef.current = 0;
    setProgress(0);
    progressRef.current = setInterval(() => {
      const p = progressValRef.current;
      const next = p < 60 ? p + 6 : p < 80 ? p + 2 : p < 92 ? p + 0.4 : p;
      progressValRef.current = Math.min(next, 92);
      setProgress(progressValRef.current);
    }, 120);
  }, [stopProgressBar]);

  const finishProgressBar = useCallback((success: boolean) => {
    stopProgressBar();
    if (success) {
      setProgress(100);
      setTimeout(() => setProgress(0), 700);
    } else {
      setProgress(0);
    }
  }, [stopProgressBar]);

  const ping = useCallback(async (manual = false) => {
    if (manual) {
      setRetrying(true);
      setReconnected(false);
      startProgressBar();
    }
    // Single source of truth for connection state: this ping is authoritative
    // and is broadcast so other surfaces (e.g. the dashboard sidebar) can't
    // disagree — previously each kept its own `live` on a different interval,
    // so the sidebar showed "Offline" while the header showed "Live".
    const broadcast = (isLive: boolean) => {
      setLive(isLive);
      window.dispatchEvent(new CustomEvent("purity:connection", { detail: { live: isLive } }));
    };
    try {
      // Ping the STATIC no-DB endpoint, not /b2b/kpis. kpis is a CPU-heavy
      // aggregate over every lead — pinging it every 5-30s just to render a
      // "Live" dot was a large share of the API load, and when the API got slow
      // the ping timed out -> "offline" -> poll faster -> slower still.
      const res = await fetch(`/api/v1/ping`, { signal: AbortSignal.timeout(8000) });
      if (res.ok) {
        broadcast(true);
        setLastSync(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
        setCountdown(30);
        if (manual) {
          finishProgressBar(true);
          setReconnected(true);
          setTimeout(() => window.location.reload(), 800);
        }
      } else {
        broadcast(false);
        if (manual) finishProgressBar(false);
      }
    } catch {
      broadcast(false);
      if (manual) finishProgressBar(false);
    } finally {
      if (manual) setRetrying(false);
    }
  }, [startProgressBar, finishProgressBar]);

  useEffect(() => {
    let cancelled = false;
    let pingTimer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (delay: number) => {
      if (cancelled) return;
      pingTimer = setTimeout(async () => {
        if (!cancelled) {
          await ping();
          schedule(live ? 60_000 : 20_000);   // was 30s/5s — 5s while degraded was a death spiral
        }
      }, delay);
    };

    (async () => {
      await ping();
      schedule(live ? 60_000 : 20_000);   // was 30s/5s — 5s while degraded was a death spiral
    })();

    const ticker = setInterval(() => {
      setCountdown(c => (c <= 1 ? 30 : c - 1));
    }, 1_000);

    // Live pending-approval badge for the Approval Inbox nav item
    const fetchPending = () => fetch("/api/v1/b2b/email/approval-inbox")
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d && typeof d.total === "number") setPendingApprovals(d.total); })
      .catch(() => {});
    fetchPending();
    // approval-inbox is a heavy aggregate; it only feeds a badge count -> poll rarely
    const pendingTimer = setInterval(fetchPending, 180_000);   // was 30s

    const onRefresh = () => { ping(); fetchPending(); };
    window.addEventListener("dashboard:refresh", onRefresh);

    return () => {
      cancelled = true;
      if (pingTimer) clearTimeout(pingTimer);
      clearInterval(ticker);
      clearInterval(pendingTimer);
      window.removeEventListener("dashboard:refresh", onRefresh);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ping]);

  // Status text
  const statusText = retrying
    ? `Connecting… ${Math.round(progress)}%`
    : reconnected
    ? "Connected!"
    : live
    ? lastSync
      ? `Synced ${lastSync}`
      : "Live"
    : "Offline";

  return (
    <header className="sticky top-0 z-50 bg-gray-950/95 backdrop-blur border-b border-gray-800">
      {/* Progress bar — only visible while reconnecting */}
      {progress > 0 && (
        <div className="absolute top-0 left-0 right-0 h-[2px] bg-gray-800 z-10 overflow-hidden">
          <div
            className={`h-full ${progress >= 100 ? "bg-green-400" : "bg-amber-400"}`}
            style={{ width: `${progress}%`, transition: "width 0.1s linear" }}
          />
        </div>
      )}

      <div className="max-w-[1600px] mx-auto px-4 h-12 flex items-center gap-4">

        {/* Brand */}
        <Link href="/dashboard" className="flex items-center gap-2 shrink-0 mr-2">
          <Image
            src="/company_logo.png"
            alt="Pure Pantry Provisions"
            width={28}
            height={28}
            className="rounded-sm object-contain"
          />
          <span className="text-sm font-black text-gray-100 tracking-tight hidden sm:inline hover:text-amber-400 transition-colors">
            Pure Pantry <span className="text-amber-400">Provisions</span>
          </span>
          <span className="text-sm font-black text-amber-400 sm:hidden">P3</span>
        </Link>

        {/* Nav links — desktop */}
        <nav className="hidden md:flex items-center gap-1 flex-1">
          {NAV.map(({ href, label }) => {
            const active = href.includes("#")
              ? (path === "/dashboard" && typeof window !== "undefined" && window.location.hash === href.substring(href.indexOf("#")))
              : (path === href || (href !== "/dashboard" && path.startsWith(href) && !href.includes("#")));
            const showBadge = href.includes("approval-center") && pendingApprovals > 0;
            return (
              <Link
                key={href}
                href={href}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-1.5 ${
                  active
                    ? "bg-amber-500/15 text-amber-400 border border-amber-500/30"
                    : "text-gray-400 hover:text-gray-100 hover:bg-gray-800"
                }`}
              >
                {label}
                {showBadge && (
                  <span className="min-w-[16px] h-4 px-1 rounded-full bg-amber-500 text-gray-950 text-[9px] font-black flex items-center justify-center">
                    {pendingApprovals}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>

        <div className="flex-1 md:hidden" />

        {/* Status area */}
        <div className="flex items-center gap-3 shrink-0 text-[10px]">
          {live && !retrying && (
            <span className="hidden sm:inline text-gray-600 tabular-nums">
              next sync <span className="text-gray-400 font-bold">{countdown}s</span>
            </span>
          )}

          <span className={`hidden sm:inline font-medium ${
            reconnected   ? "text-green-400" :
            retrying      ? "text-amber-400" :
            live          ? "text-gray-500"  :
            lastSync      ? "text-gray-500"  : "text-red-400/70"
          }`}>
            {statusText}
          </span>

          {live && !retrying ? (
            <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full font-bold border bg-green-500/10 text-green-400 border-green-500/30">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
              Live
            </span>
          ) : (
            <button
              onClick={() => !retrying && ping(true)}
              disabled={retrying}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full font-bold border transition-all min-w-[100px] justify-center ${
                retrying
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-300 opacity-90 cursor-wait"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 cursor-pointer"
              }`}
            >
              <RefreshCw className={`w-3 h-3 shrink-0 ${retrying ? "animate-spin" : ""}`} />
              {retrying
                ? progress >= 100
                  ? <span className="text-green-400">Done!</span>
                  : `${Math.round(progress)}%`
                : "Reconnect"}
            </button>
          )}
        </div>

        {/* Mobile hamburger */}
        <button
          className="md:hidden p-1.5 rounded-lg text-gray-400 hover:text-gray-100 hover:bg-gray-800"
          onClick={() => setMobileOpen(o => !o)}
          aria-label="Menu"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {mobileOpen
              ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            }
          </svg>
        </button>
      </div>

      {/* Mobile nav drawer */}
      {mobileOpen && (
        <div className="md:hidden border-t border-gray-800 bg-gray-950 px-4 py-3 flex flex-col gap-1">
          {NAV.map(({ href, label }) => {
            const active = href.includes("#")
              ? (path === "/dashboard" && typeof window !== "undefined" && window.location.hash === href.substring(href.indexOf("#")))
              : (path === href || (href !== "/dashboard" && path.startsWith(href) && !href.includes("#")));
            const showBadge = href.includes("approval-center") && pendingApprovals > 0;
            return (
              <Link
                key={href}
                href={href}
                onClick={() => setMobileOpen(false)}
                className={`px-3 py-2 rounded-lg text-sm font-semibold transition-all flex items-center justify-between ${
                  active
                    ? "bg-amber-500/15 text-amber-400"
                    : "text-gray-400 hover:text-gray-100 hover:bg-gray-800"
                }`}
              >
                {label}
                {showBadge && (
                  <span className="min-w-[18px] h-4 px-1 rounded-full bg-amber-500 text-gray-950 text-[10px] font-black flex items-center justify-center">
                    {pendingApprovals}
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </header>
  );
}
