"use client";
import React, { useEffect, useState, useCallback } from "react";
import { Zap, Mail, MessageCircle, TrendingUp, Target, AlertCircle, RefreshCw, ChevronRight, Phone, Building2 } from "lucide-react";

interface EngineStatus {
  email_ready: number;
  wa_ready: number;
  followup_due: number;
  gov_ready: number;
  emails_today: number;
  top_leads: TopLead[];
  revenue_gap: number;
  won_this_week: number;
  weekly_target: number;
  pipeline_by_division: Record<string, number>;
  actions_needed: number;
}

interface TopLead {
  id: number;
  company: string;
  city: string;
  status: string;
  score: number;
  estimated_value: number;
  email: string | null;
  phone: string | null;
  whatsapp_number: string | null;
  division: string;
  recommended_action: string | null;
  contact_name: string | null;
}

function fmt(v: number) {
  if (v >= 10_000_000) return `₹${(v / 10_000_000).toFixed(1)}Cr`;
  if (v >= 100_000) return `₹${(v / 100_000).toFixed(1)}L`;
  if (v >= 1_000) return `₹${(v / 1_000).toFixed(0)}K`;
  return `₹${v.toLocaleString("en-IN")}`;
}

function statusColor(s: string) {
  if (["ORDER_WON"].includes(s)) return "text-green-400";
  if (["PROPOSAL_SENT", "MEETING_COMPLETED"].includes(s)) return "text-blue-400";
  if (["SAMPLE_SENT", "MEETING_BOOKED"].includes(s)) return "text-purple-400";
  if (["REPLIED"].includes(s)) return "text-cyan-400";
  if (["EMAIL_SENT"].includes(s)) return "text-amber-400";
  return "text-gray-400";
}

export default function RevenueCommandCenter() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [emailCounts, setEmailCounts] = useState<{ verified: number; needs_approval: number; invalid: number; unverified: number; total: number } | null>(null);
  const [firing, setFiring] = useState(false);
  const [waFiring, setWaFiring] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" | "info" } | null>(null);
  const [lastFired, setLastFired] = useState<string | null>(null);

  const showToast = (msg: string, type: "ok" | "err" | "info" = "info") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 5000);
  };

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/v1/b2b/revenue-engine/status");
      if (r.ok) setStatus(await r.json());
    } catch { /* silent */ }
    try {
      const r2 = await fetch("/api/v1/b2b/email/queue");
      if (r2.ok) { const d = await r2.json(); setEmailCounts(d.counts); }
    } catch { /* silent */ }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, [load]);

  const fireEmailEngine = async () => {
    if (firing) return;
    setFiring(true);
    showToast("Drafting emails for your Approval Inbox…", "info");
    try {
      const r = await fetch("/api/v1/b2b/send-outreach-now", { method: "POST" });
      const data = await r.json();
      const drafted = data.drafted || 0;
      setLastFired(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      showToast(
        drafted > 0
          ? `✓ ${drafted} drafts queued — review them in the Approval Inbox below`
          : "No new leads to draft — all eligible leads already have drafts pending",
        drafted > 0 ? "ok" : "info"
      );
      await load();
    } catch {
      showToast("Draft generation failed — check backend", "err");
    } finally {
      setFiring(false);
    }
  };

  const fireWhatsApp = async () => {
    if (waFiring) return;
    setWaFiring(true);
    try {
      const r = await fetch("/api/v1/b2b/revenue-engine/fire-whatsapp", { method: "POST" });
      const data = await r.json();
      showToast(`${data.drafted || 0} WhatsApp drafts ready — review and send from the Follow-up Center`, "ok");
      await load();
    } catch {
      showToast("WhatsApp draft generation failed", "err");
    } finally {
      setWaFiring(false);
    }
  };

  const gapPct = status ? Math.min(100, (status.won_this_week / status.weekly_target) * 100) : 0;
  const totalReady = (status?.email_ready || 0) + (status?.gov_ready || 0);

  return (
    <div className="mb-6 space-y-3">

      {/* Toast */}
      {toast && (
        <div className={`fixed top-16 right-4 z-[9999] px-4 py-3 rounded-xl border text-sm font-medium shadow-2xl max-w-sm ${
          toast.type === "ok"  ? "bg-green-950 border-green-500/40 text-green-200" :
          toast.type === "err" ? "bg-red-950   border-red-500/40   text-red-200"   :
                                 "bg-gray-900  border-gray-700     text-gray-200"
        }`}>
          {toast.msg}
        </div>
      )}

      {/* ── FIRE PANEL ─────────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-amber-500/20 rounded-xl p-5 relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-amber-500/5 via-transparent to-transparent pointer-events-none" />

        <div className="flex flex-col md:flex-row md:items-center gap-4">
          {/* Left — label */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <Zap className="w-4 h-4 text-amber-400" />
              <span className="text-xs font-black uppercase tracking-widest text-amber-400">Revenue Engine</span>
              {lastFired && <span className="text-[10px] text-gray-600">· last fired {lastFired}</span>}
            </div>
            <p className="text-gray-400 text-xs leading-relaxed">
              <span className="text-white font-bold">{totalReady} leads</span> ready for first-touch outreach ·{" "}
              <span className="text-white font-bold">{status?.wa_ready || 0}</span> WhatsApp numbers available ·{" "}
              <span className="text-amber-400 font-bold">{status?.followup_due || 0}</span> follow-ups overdue
            </p>
          </div>

          {/* Right — fire buttons */}
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            <button
              onClick={fireWhatsApp}
              disabled={waFiring || !status?.wa_ready}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-bold bg-green-500/15 border border-green-500/40 text-green-400 hover:bg-green-500/25 disabled:opacity-40 transition-all"
            >
              <MessageCircle className={`w-3.5 h-3.5 ${waFiring ? "animate-pulse" : ""}`} />
              {waFiring ? "Drafting…" : `Draft WhatsApp ${status?.wa_ready || 0}`}
            </button>

            {/* Verification status breakdown — clicking scrolls to approval center */}
            {emailCounts && (emailCounts.verified > 0 || emailCounts.needs_approval > 0) ? (
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[10px] font-black px-2.5 py-2 rounded-xl bg-green-500/10 border border-green-500/25 text-green-400">✓ {emailCounts.verified} Verified</span>
                <span className="text-[10px] font-black px-2.5 py-2 rounded-xl bg-amber-500/10 border border-amber-500/25 text-amber-400">⚠ {emailCounts.needs_approval} Need Approval</span>
                {emailCounts.invalid > 0 && <span className="text-[10px] font-black px-2.5 py-2 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400">✕ {emailCounts.invalid} Invalid</span>}
                <a
                  href="#email-approval"
                  className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-sm font-black bg-amber-500 hover:bg-amber-400 text-gray-950 shadow-lg shadow-amber-500/30 transition-all"
                >
                  <Mail className="w-4 h-4" /> Review Emails
                </a>
              </div>
            ) : (
              <button
                onClick={fireEmailEngine}
                disabled={firing}
                className={`flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-black transition-all shadow-lg ${
                  firing
                    ? "bg-amber-600/30 border border-amber-500/30 text-amber-300 cursor-wait"
                    : "bg-amber-500 hover:bg-amber-400 text-gray-950 shadow-amber-500/30"
                }`}
              >
                {firing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                {firing ? "Drafting…" : `Draft Emails for Approval (${totalReady})`}
              </button>
            )}
          </div>
        </div>

        {/* Automation scoreboard */}
        <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-2">
          {[
            { label: "Emails Sent Today", value: status?.emails_today || 0, color: "text-blue-400", icon: <Mail className="w-3 h-3" /> },
            { label: "Verified Emails", value: emailCounts?.verified || 0, color: "text-green-400", icon: <Mail className="w-3 h-3" /> },
            { label: "Need Approval", value: emailCounts?.needs_approval || 0, color: (emailCounts?.needs_approval || 0) > 0 ? "text-amber-400" : "text-gray-500", icon: <AlertCircle className="w-3 h-3" /> },
            { label: "Follow-ups Overdue", value: status?.followup_due || 0, color: status?.followup_due ? "text-red-400" : "text-gray-500", icon: <AlertCircle className="w-3 h-3" /> },
          ].map(card => (
            <div key={card.label} className="bg-gray-950/60 rounded-lg px-3 py-2 flex items-center gap-2">
              <span className={card.color}>{card.icon}</span>
              <div>
                <p className={`text-base font-black leading-none ${card.color}`}>{card.value}</p>
                <p className="text-[10px] text-gray-600 mt-0.5">{card.label}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── REVENUE GAP + TOP CLOSES ───────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">

        {/* Revenue gap meter */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Target className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-xs font-black uppercase tracking-wider text-gray-400">Weekly Revenue Gap</span>
          </div>
          <div className="mb-2">
            <div className="flex justify-between text-[11px] mb-1.5">
              <span className="text-gray-500">Won this week</span>
              <span className="font-bold text-green-400">{fmt(status?.won_this_week || 0)}</span>
            </div>
            <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-green-600 to-green-400 rounded-full transition-all duration-700"
                style={{ width: `${gapPct}%` }}
              />
            </div>
            <div className="flex justify-between text-[10px] mt-1">
              <span className="text-gray-600">₹0</span>
              <span className="text-gray-600">Target {fmt(status?.weekly_target ?? 0)}</span>
            </div>
          </div>
          {(status?.revenue_gap || 0) > 0 ? (
            <div className="mt-3 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              <p className="text-xs text-red-400 font-bold">{fmt(status?.revenue_gap || 0)} gap remaining</p>
              <p className="text-[10px] text-red-400/70 mt-0.5">
                Need ~{status?.actions_needed ?? 0} more outreach actions
              </p>
            </div>
          ) : (
            <div className="mt-3 bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-2">
              <p className="text-xs text-green-400 font-bold">Weekly target hit!</p>
            </div>
          )}

          {/* Pipeline by division */}
          {status?.pipeline_by_division && (
            <div className="mt-3 space-y-1.5">
              <p className="text-[10px] uppercase tracking-wider text-gray-600 font-bold">Pipeline by segment</p>
              {Object.entries(status.pipeline_by_division)
                .filter(([, v]) => v > 0)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 4)
                .map(([div, val]) => (
                  <div key={div} className="flex items-center justify-between text-[11px]">
                    <span className="text-gray-500 capitalize">{div}</span>
                    <span className="font-bold text-gray-300">{fmt(val)}</span>
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* Today's top 5 closes */}
        <div className="md:col-span-2 bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="w-3.5 h-3.5 text-green-400" />
            <span className="text-xs font-black uppercase tracking-wider text-gray-400">Today&apos;s Money — Top Closes</span>
          </div>

          {!status?.top_leads?.length ? (
            <p className="text-xs text-gray-600 py-4 text-center">No active leads yet — fire the engine above</p>
          ) : (
            <div className="space-y-2">
              {status.top_leads.map((lead, i) => (
                <div key={lead.id} className="flex items-center gap-3 bg-gray-950/60 rounded-lg px-3 py-2.5 hover:bg-gray-800/50 transition-colors">
                  {/* Rank */}
                  <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-black shrink-0 ${
                    i === 0 ? "bg-amber-500/20 text-amber-400" :
                    i === 1 ? "bg-gray-700 text-gray-300" :
                    "bg-gray-800 text-gray-500"
                  }`}>{i + 1}</span>

                  {/* Company */}
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-bold text-gray-100 truncate">{lead.company}</p>
                    <p className={`text-[10px] ${statusColor(lead.status)}`}>
                      {lead.status.replace(/_/g, " ")} · {lead.city}
                    </p>
                  </div>

                  {/* Value */}
                  <div className="text-right shrink-0">
                    <p className="text-xs font-black text-green-400">{fmt(lead.estimated_value)}</p>
                    <p className="text-[10px] text-gray-600">score {lead.score}</p>
                  </div>

                  {/* Quick actions */}
                  <div className="flex gap-1 shrink-0">
                    {lead.phone && (
                      <a
                        href={`tel:${lead.phone}`}
                        className="w-6 h-6 rounded-lg bg-green-500/10 border border-green-500/20 flex items-center justify-center text-green-400 hover:bg-green-500/20 transition-colors"
                        title={`Call ${lead.phone}`}
                      >
                        <Phone className="w-3 h-3" />
                      </a>
                    )}
                    {lead.email && (
                      <a
                        href={`mailto:${lead.email}`}
                        className="w-6 h-6 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400 hover:bg-blue-500/20 transition-colors"
                        title={`Email ${lead.email}`}
                      >
                        <Mail className="w-3 h-3" />
                      </a>
                    )}
                    {lead.whatsapp_number && (
                      <a
                        href={`https://wa.me/${lead.whatsapp_number.replace(/\D/g, "")}`}
                        target="_blank"
                        rel="noreferrer"
                        className="w-6 h-6 rounded-lg bg-green-500/10 border border-green-500/20 flex items-center justify-center text-green-400 hover:bg-green-500/20 transition-colors"
                        title="WhatsApp"
                      >
                        <MessageCircle className="w-3 h-3" />
                      </a>
                    )}
                  </div>

                  <ChevronRight className="w-3 h-3 text-gray-700 shrink-0" />
                </div>
              ))}
            </div>
          )}

          {/* Quick nav */}
          <div className="mt-3 flex gap-2 flex-wrap">
            <a href="/distributors" className="text-[10px] text-amber-400 hover:underline flex items-center gap-0.5">
              → Full CRM Pipeline
            </a>
            <a href="/government" className="text-[10px] text-purple-400 hover:underline flex items-center gap-0.5">
              → Government Pipeline
            </a>
            <a href="/actions" className="text-[10px] text-blue-400 hover:underline flex items-center gap-0.5">
              → Action Queue
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
