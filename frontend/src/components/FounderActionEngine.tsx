"use client";
import React, { useEffect, useState, useCallback } from "react";
import {
  Zap, Mail, MessageCircle, Phone, CheckCircle, XCircle,
  Clock, Play, SkipForward, RefreshCw, ChevronDown, ChevronRight,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

// V1.1: recommendations come fully-formed from the Decision Engine
// (GET /dashboard/workspace → action_queue). The UI classifies nothing.
interface ActionItem {
  id: string;
  lead_id: number;
  company: string;
  city: string | null;
  contact_name: string | null;
  email: string | null;
  phone: string | null;
  whatsapp: string | null;
  division: string | null;
  status: string | null;
  fps: number;
  action_type: string;
  label: string;
  reason: string;
  can_auto: boolean;
  confidence_pct: number;
  evidence: string[];
  expected_margin_rs: number;
  expected_duration_min: number;
  exec_status: "pending" | "running" | "done" | "skipped" | "error";
  error_msg?: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function rs(n: number) {
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + "L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + "K";
  return "₹" + n;
}

const ACTION_COLOR: Record<string, string> = {
  draft_email:  "border-blue-500/30 bg-blue-500/5",
  whatsapp:     "border-emerald-500/30 bg-emerald-500/5",
  ai_call:      "border-violet-500/30 bg-violet-500/5",
  founder_call: "border-amber-500/30 bg-amber-500/5",
  meeting:      "border-cyan-500/30 bg-cyan-500/5",
  sample:       "border-orange-500/30 bg-orange-500/5",
  proposal:     "border-rose-500/30 bg-rose-500/5",
  close:        "border-green-500/30 bg-green-500/5",
};

const ACTION_ICON: Record<string, React.ReactNode> = {
  draft_email:  <Mail className="w-3.5 h-3.5 text-blue-400" />,
  whatsapp:     <MessageCircle className="w-3.5 h-3.5 text-emerald-400" />,
  ai_call:      <Phone className="w-3.5 h-3.5 text-violet-400" />,
  founder_call: <Phone className="w-3.5 h-3.5 text-amber-400" />,
  meeting:      <Zap className="w-3.5 h-3.5 text-cyan-400" />,
  sample:       <CheckCircle className="w-3.5 h-3.5 text-orange-400" />,
  proposal:     <CheckCircle className="w-3.5 h-3.5 text-rose-400" />,
  close:        <CheckCircle className="w-3.5 h-3.5 text-green-400" />,
};

const LABEL_COLOR: Record<string, string> = {
  draft_email:  "text-blue-400",
  whatsapp:     "text-emerald-400",
  ai_call:      "text-violet-400",
  founder_call: "text-amber-400",
  meeting:      "text-cyan-400",
  sample:       "text-orange-400",
  proposal:     "text-rose-400",
  close:        "text-green-400",
};

// ─── Main Component ───────────────────────────────────────────────────────────

export default function FounderActionEngine() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ActionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState<"all" | "auto" | "founder">("all");
  const [stats, setStats] = useState({ done: 0, skipped: 0, errors: 0 });
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // V1.1: the Decision Engine classifies everything server-side.
      const wsRes = await fetch("/api/v1/dashboard/workspace").catch(() => null);
      const ws = wsRes?.ok ? await wsRes.json().catch(() => null) : null;
      const queue: Record<string, unknown>[] = Array.isArray(ws?.action_queue) ? ws.action_queue : [];

      const mapped: ActionItem[] = queue.map((a) => ({
        id: `${a.lead_id}-${a.current_status}`,
        lead_id: a.lead_id as number,
        company: a.company as string,
        city: (a.city as string) || null,
        contact_name: (a.contact_name as string) || null,
        email: (a.email as string) || null,
        phone: (a.phone as string) || null,
        whatsapp: (a.whatsapp_number as string) || null,
        division: (a.division as string) || null,
        status: (a.current_status as string) || null,
        fps: (a.fps as number) || 0,
        action_type: (a.action_type as string) || "close",
        label: (a.cta_label as string) || "Follow Up",
        reason: (a.reason as string) || "",
        can_auto: Boolean(a.can_auto),
        confidence_pct: (a.confidence_pct as number) || 0,
        evidence: Array.isArray(a.evidence) ? (a.evidence as string[]) : [],
        expected_margin_rs: (a.expected_margin_rs as number) || 0,
        expected_duration_min: (a.expected_duration_min as number) || 0,
        exec_status: "pending" as const,
      }));
      mapped.sort((a, b) => (a.can_auto === b.can_auto ? b.fps - a.fps : a.can_auto ? -1 : 1));
      setItems(mapped);
    } catch {
      showToast("Failed to load action queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const execEmail = async (item: ActionItem): Promise<boolean> => {
    // V1.1: emails are never sent from here — a draft is queued in the
    // Approval Inbox where the founder reviews and approves it.
    if (!item.email) return false;
    const res = await fetch("/api/v1/b2b/email/generate-drafts", { method: "POST" });
    if (!res.ok) return false;
    showToast(`Draft queued for ${item.company} — review in the Approval Inbox`);
    return true;
  };

  const execWhatsApp = async (item: ActionItem): Promise<boolean> => {
    const phone = (item.whatsapp || item.phone || "").replace(/\D/g, "");
    if (!phone) return false;
    // Fetch the segment template from the backend registry (no hardcoded copy)
    const name = (item.contact_name || "").split(" ")[0] || "";
    const tRes = await fetch(
      `/api/v1/templates/${encodeURIComponent(item.division || "corporate")}?name=${encodeURIComponent(name)}&city=${encodeURIComponent(item.city || "")}`
    ).catch(() => null);
    const t = tRes?.ok ? await tRes.json().catch(() => null) : null;
    const msg: string = t?.whatsapp_message || "";
    if (!msg) return false;
    // Founder sends personally from WhatsApp — that click IS the approval
    window.open("https://api.whatsapp.com/send?phone=" + phone + "&text=" + encodeURIComponent(msg), "_blank");
    await fetch("/api/v1/b2b/leads/status", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company: item.company, status: "WHATSAPP_SENT" }),
    });
    return true;
  };

  const runOne = async (item: ActionItem) => {
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, exec_status: "running" } : i));
    let ok = false;
    try {
      if (item.action_type === "draft_email") ok = await execEmail(item);
      else if (item.action_type === "whatsapp") ok = await execWhatsApp(item);
    } catch { ok = false; }
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, exec_status: ok ? "done" : "error", error_msg: ok ? undefined : "Failed" } : i));
    setStats(s => ({ ...s, done: s.done + (ok ? 1 : 0), errors: s.errors + (ok ? 0 : 1) }));
  };

  const skipOne = (item: ActionItem) => {
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, exec_status: "skipped" } : i));
    setStats(s => ({ ...s, skipped: s.skipped + 1 }));
  };

  const runAllAuto = async () => {
    const autoItems = items.filter(i => i.can_auto && i.exec_status === "pending");
    if (!autoItems.length) { showToast("No pending auto-actions"); return; }
    setRunning(true);
    for (const item of autoItems) {
      await runOne(item);
      await new Promise(r => setTimeout(r, 400));
    }
    setRunning(false);
    showToast("All auto-actions complete");
  };

  const visible = items.filter(i =>
    filter === "auto" ? i.can_auto :
    filter === "founder" ? !i.can_auto : true
  );

  const autoCount = items.filter(i => i.can_auto && i.exec_status === "pending").length;
  const founderCount = items.filter(i => !i.can_auto && i.exec_status === "pending").length;
  const doneCount = items.filter(i => i.exec_status === "done").length;
  const totalMargin = items.reduce((s, i) => s + i.expected_margin_rs, 0);

  return (
    <>
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl">
          {toast}
        </div>
      )}

      <div className="mb-6 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">

        {/* Header */}
        <button
          onClick={() => { setOpen(v => !v); if (!open && items.length === 0) load(); }}
          className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-800/40 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="relative">
              <Zap className="w-4 h-4 text-amber-400" />
              {(autoCount + founderCount) > 0 && (
                <span className="absolute -top-1.5 -right-1.5 min-w-[14px] h-3.5 bg-amber-500 rounded-full text-[8px] text-black flex items-center justify-center font-black px-0.5">
                  {autoCount + founderCount}
                </span>
              )}
            </div>
            <div className="text-left">
              <span className="text-sm font-black text-gray-100">Founder Action Engine</span>
              <p className="text-[10px] text-gray-500 mt-0.5">System decides what to do. You approve, skip, or let it run.</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {items.length > 0 && (
              <div className="hidden md:flex items-center gap-3 text-[10px] font-bold">
                {autoCount > 0 && <span className="text-blue-400">{autoCount} auto</span>}
                {founderCount > 0 && <span className="text-amber-400">{founderCount} needs you</span>}
                {doneCount > 0 && <span className="text-green-400">{doneCount} done</span>}
                <span className="text-emerald-400">{rs(totalMargin)} at stake</span>
              </div>
            )}
            {open ? <ChevronDown className="w-4 h-4 text-gray-600" /> : <ChevronRight className="w-4 h-4 text-gray-600" />}
          </div>
        </button>

        {open && (
          <div className="border-t border-gray-800">

            {/* KPI row */}
            <div className="grid grid-cols-4 gap-3 p-4">
              {[
                { label: "Auto-runnable", val: autoCount, color: "text-blue-400", sub: "system can do these" },
                { label: "Needs You", val: founderCount, color: "text-amber-400", sub: "calls, meetings, proposals" },
                { label: "Done Today", val: doneCount, color: "text-green-400", sub: "actions completed" },
                { label: "Margin at Stake", val: rs(totalMargin), color: "text-emerald-400", sub: "across all actions" },
              ].map(s => (
                <div key={s.label} className="bg-gray-950/60 border border-gray-800 rounded-xl p-3 text-center">
                  <p className={"text-lg font-black " + s.color}>{s.val}</p>
                  <p className="text-[9px] font-bold text-gray-400 mt-0.5">{s.label}</p>
                  <p className="text-[8px] text-gray-600">{s.sub}</p>
                </div>
              ))}
            </div>

            {/* Run-all-auto bar */}
            {autoCount > 0 && (
              <div className="mx-4 mb-4 bg-blue-500/5 border border-blue-500/20 rounded-xl px-4 py-3 flex items-center justify-between gap-4">
                <div>
                  <p className="text-[10px] font-black text-blue-400">{autoCount} actions can run automatically</p>
                  <p className="text-[9px] text-gray-500 mt-0.5">Emails become drafts in the Approval Inbox · WhatsApp opens in browser · all logged</p>
                </div>
                <button
                  onClick={runAllAuto}
                  disabled={running || loading}
                  className="shrink-0 flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-[10px] font-black rounded-lg transition-all"
                >
                  {running
                    ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Running…</>
                    : <><Play className="w-3.5 h-3.5" /> Run All Auto ({autoCount})</>}
                </button>
              </div>
            )}

            {/* Toolbar */}
            <div className="flex items-center justify-between px-4 pb-3 gap-2">
              <div className="flex gap-1">
                {(["all", "auto", "founder"] as const).map(f => (
                  <button key={f} onClick={() => setFilter(f)}
                    className={"px-3 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-wider transition-all " +
                      (filter === f ? "bg-gray-700 text-white" : "text-gray-600 hover:text-gray-400")}>
                    {f === "all" ? `All (${items.length})` : f === "auto" ? `Auto (${autoCount})` : `Founder (${founderCount})`}
                  </button>
                ))}
              </div>
              <button onClick={load} disabled={loading} className="flex items-center gap-1 px-2 py-1.5 text-gray-600 hover:text-gray-300 text-[10px] transition-all">
                <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>

            {/* Action list */}
            <div className="px-4 pb-4">
              {loading ? (
                <div className="text-center py-10 text-[10px] text-gray-600 font-black uppercase tracking-widest animate-pulse">
                  Building action queue…
                </div>
              ) : visible.length === 0 ? (
                <div className="text-center py-10">
                  <CheckCircle className="w-7 h-7 text-green-500 mx-auto mb-3" />
                  <p className="text-[11px] font-black text-gray-400">All actions complete</p>
                  <p className="text-[9px] text-gray-600 mt-1">Refresh to check for new leads</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {visible.map(item => {
                    const colorCls = ACTION_COLOR[item.action_type] || "border-gray-700 bg-gray-900";
                    const labelCls = LABEL_COLOR[item.action_type] || "text-gray-400";
                    const isDone = item.exec_status === "done";
                    const isSkipped = item.exec_status === "skipped";
                    const isRunning = item.exec_status === "running";
                    const isError = item.exec_status === "error";

                    return (
                      <div key={item.id}
                        className={`rounded-xl border px-4 py-3 flex items-center gap-3 transition-all ${colorCls} ${isDone ? "opacity-40" : ""} ${isSkipped ? "opacity-25" : ""}`}>
                        <div className="shrink-0">
                          {isRunning ? <RefreshCw className="w-3.5 h-3.5 text-blue-400 animate-spin" /> :
                           isDone    ? <CheckCircle className="w-3.5 h-3.5 text-green-400" /> :
                           isError   ? <XCircle className="w-3.5 h-3.5 text-red-400" /> :
                           isSkipped ? <SkipForward className="w-3.5 h-3.5 text-gray-600" /> :
                           ACTION_ICON[item.action_type]}
                        </div>

                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-[10px] font-black text-gray-200">{item.company}</span>
                            {item.city && <span className="text-[8px] text-gray-600">{item.city}</span>}
                            {item.can_auto && (
                              <span className="text-[7px] font-black px-1.5 py-0.5 bg-blue-500/10 text-blue-400 border border-blue-500/20 rounded uppercase tracking-wider">AUTO</span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                            <span className={"text-[9px] font-black " + labelCls}>{item.label}</span>
                            <span className="text-[8px] text-gray-600">{item.reason}</span>
                          </div>
                          {item.error_msg && <p className="text-[8px] text-red-400 mt-0.5">{item.error_msg}</p>}
                        </div>

                        <div className="text-right shrink-0 hidden sm:block">
                          <p className="text-[9px] font-black text-emerald-400">{rs(item.expected_margin_rs)}</p>
                          <p className="text-[8px] text-gray-600">margin · {item.confidence_pct}% conf</p>
                        </div>

                        {item.exec_status === "pending" && (
                          <div className="flex gap-1.5 shrink-0">
                            {item.can_auto ? (
                              <>
                                <button onClick={() => runOne(item)} disabled={running}
                                  className={`text-[8px] font-black px-2.5 py-1.5 rounded-lg border transition-all ${labelCls} border-current bg-current/10 hover:bg-current/20`}>
                                  Run
                                </button>
                                <button onClick={() => skipOne(item)}
                                  className="text-[8px] font-black px-2 py-1.5 text-gray-600 hover:text-gray-400 transition-all">
                                  Skip
                                </button>
                              </>
                            ) : (
                              <>
                                {item.action_type === "founder_call" && (item.phone || item.whatsapp) && (
                                  <a href={"tel:" + (item.phone || item.whatsapp || "").replace(/\D/g, "")}
                                    className="text-[8px] font-black px-2.5 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-all">
                                    Call
                                  </a>
                                )}
                                <button onClick={() => skipOne(item)}
                                  className="text-[8px] font-black px-2 py-1.5 text-gray-600 hover:text-gray-400 transition-all">
                                  Skip
                                </button>
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Progress summary */}
              {(stats.done > 0 || stats.errors > 0) && (
                <div className="mt-4 flex gap-3">
                  <div className="flex-1 bg-green-500/5 border border-green-500/20 rounded-xl p-3 text-center">
                    <p className="text-lg font-black text-green-400">{stats.done}</p>
                    <p className="text-[9px] text-gray-500">Done</p>
                  </div>
                  <div className="flex-1 bg-gray-800/40 border border-gray-700 rounded-xl p-3 text-center">
                    <p className="text-lg font-black text-gray-400">{stats.skipped}</p>
                    <p className="text-[9px] text-gray-500">Skipped</p>
                  </div>
                  {stats.errors > 0 && (
                    <div className="flex-1 bg-red-500/5 border border-red-500/20 rounded-xl p-3 text-center">
                      <p className="text-lg font-black text-red-400">{stats.errors}</p>
                      <p className="text-[9px] text-gray-500">Errors</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
