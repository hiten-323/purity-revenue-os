"use client";
import React, { useEffect, useState } from "react";

/**
 * Which backend is this dashboard actually talking to?
 *
 * Two API processes ran side by side for a day — one current, one serving code
 * days old — and nothing on screen said which was answering. Every "verified"
 * claim during that period was unprovable. This reads /version through the same
 * proxy the rest of the app uses, so what it reports is what the Approval
 * Center is getting, not what a config file says it should get.
 */
interface Ver {
  commit?: string;
  commit_on_disk?: string;
  matches_disk?: boolean;
  port?: number;
  build_time?: string;
  pid?: number;
}

export default function BackendIdentity() {
  const [v, setV] = useState<Ver | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetch("/api/v1/version")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setV)
      .catch(() => setErr(true));
  }, []);

  if (err)
    return (
      <div className="fixed bottom-2 right-2 z-50 rounded-lg border border-red-500/40 bg-red-950/90 px-2.5 py-1.5 text-[9px] font-mono text-red-300">
        backend unreachable — the dashboard is showing nothing live
      </div>
    );
  if (!v) return null;

  // A mismatch means this process is older than the working tree. Say so
  // loudly: it is the difference between a tested fix and an untested one.
  const stale = v.matches_disk === false;
  return (
    <div
      className={`fixed bottom-2 right-2 z-50 rounded-lg border px-2.5 py-1.5 font-mono text-[9px] leading-relaxed ${
        stale
          ? "border-amber-500/50 bg-amber-950/90 text-amber-300"
          : "border-gray-800 bg-gray-950/90 text-gray-500"
      }`}
      title={`built ${v.build_time ?? "unknown"} · pid ${v.pid ?? "?"}`}
    >
      <div>
        API <span className="text-gray-300">localhost:{v.port ?? "?"}</span>
      </div>
      <div>
        commit <span className="text-gray-300">{v.commit ?? "unknown"}</span>
        {stale && (
          <span className="ml-1 text-amber-400">
            ≠ disk {v.commit_on_disk} — RESTART
          </span>
        )}
      </div>
    </div>
  );
}
