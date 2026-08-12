"use client";
import React, { useEffect, useState, useCallback } from "react";
import { MessageCircle, Phone, User, RefreshCw, ExternalLink, Clock, ShieldAlert } from "lucide-react";

// Follow-Ups Due — auto-advanced next touches from the warmup sequence.
// The system schedules these after each send; here the founder approves &
// executes them. Nothing auto-sends.

interface FollowUp {
  reminder_id: number; lead_id: number; company: string; channel: string;
  sequence_step: number; due_at: string; reason: string; current_status: string;
  city: string; margin: number; channel_approved: boolean; message: string;
}
interface DueData { total: number; by_channel: Record<string, FollowUp[]>; items: FollowUp[]; }

const CH_META: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  whatsapp:     { label: "WhatsApp", icon: <MessageCircle className="w-3.5 h-3.5" />, color: "text-green-400" },
  ai_call:      { label: "AI Call",  icon: <Phone className="w-3.5 h-3.5" />,        color: "text-blue-400" },
  founder_call: { label: "Founder Call", icon: <User className="w-3.5 h-3.5" />,     color: "text-amber-400" },
};

function rs(n: number) {
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(2) + "Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + "L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + "K";
  return "₹" + Math.round(n);
}
function daysAgo(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  return d <= 0 ? "due today" : `${d}d overdue`;
}

export default function FollowUpsDuePanel() {
  const [data, setData] = useState<DueData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000); };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/outreach/followups/due").catch(() => null);
      setData(r?.ok ? await r.json().catch(() => null) : null);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const execute = async (f: FollowUp) => {
    setBusy(f.reminder_id);
    try {
      const r = await fetch("/api/v1/b2b/outreach/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_id: f.lead_id, channel: f.channel, reminder_id: f.reminder_id }),
      });
      const d = await r.json();
      if (f.channel === "whatsapp" && d.detail?.whatsapp_url) {
        window.open(d.detail.whatsapp_url, "_blank");
        showToast(`WhatsApp opened for ${f.company} — next step scheduled`);
      } else if (d.status === "executed") {
        showToast(`${CH_META[f.channel]?.label} done for ${f.company} — next step scheduled`);
      } else {
        showToast(d.message || d.detail || "Executed");
      }
      await load();
    } catch { showToast("Execute failed — check backend"); }
    finally { setBusy(null); }
  };

  const items = data?.items || [];

  return (
    <div>
      {toast && <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl max-w-sm">{toast}</div>}

      <div className="flex items-center justify-between mb-4">
        <p className="text-[11px] text-gray-500">
          Auto-scheduled next touches from the warmup sequence. Approve &amp; execute — the system never auto-sends.
        </p>
        <button onClick={load} disabled={loading} className="p-2 text-gray-500 hover:text-gray-200"><RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /></button>
      </div>

      {loading && !data ? (
        <div className="text-center py-16 text-[11px] text-gray-600 font-black uppercase tracking-widest animate-pulse">Loading follow-ups…</div>
      ) : items.length === 0 ? (
        <div className="text-center py-16">
          <Clock className="w-8 h-8 text-gray-600 mx-auto mb-3" />
          <p className="text-sm font-black text-gray-400">No follow-ups due</p>
          <p className="text-[10px] text-gray-600 mt-1">After you send intro emails, WhatsApp follow-ups appear here in 3 days if there&apos;s no reply.</p>
        </div>
      ) : (
        <div className="border border-gray-800 rounded-xl overflow-hidden">
          <div className="grid grid-cols-[1.4fr_1fr_1.8fr_90px_150px] gap-2 px-3 py-2 bg-gray-900/60 text-[9px] font-black uppercase tracking-wider text-gray-600 border-b border-gray-800">
            <div>Company</div><div>Next Channel</div><div>Message / Reason</div><div>Margin</div><div>Action</div>
          </div>
          {items.map(f => {
            const m = CH_META[f.channel] || { label: f.channel, icon: null, color: "text-gray-400" };
            return (
              <div key={f.reminder_id} className="grid grid-cols-[1.4fr_1fr_1.8fr_90px_150px] gap-2 px-3 py-2.5 items-center border-b border-gray-800/60 last:border-0 hover:bg-gray-900/40">
                <div className="min-w-0">
                  <p className="text-[11px] font-black text-gray-200 truncate">{f.company}</p>
                  <p className="text-[9px] text-gray-500">{f.city || f.current_status} · <span className="text-amber-400/80">{daysAgo(f.due_at)}</span></p>
                </div>
                <div className={`flex items-center gap-1.5 text-[10px] font-black ${m.color}`}>
                  {m.icon} {m.label}
                  {!f.channel_approved && <span title="Script not pre-approved"><ShieldAlert className="w-3 h-3 text-amber-500" /></span>}
                </div>
                <div className="text-[9px] text-gray-500 leading-tight pr-2 truncate">{f.message ? f.message.split("\n")[0] : f.reason}</div>
                <div className="text-[11px] font-black text-emerald-400">{rs(f.margin)}</div>
                <div>
                  {f.channel_approved ? (
                    <button onClick={() => execute(f)} disabled={busy === f.reminder_id}
                      className="flex items-center gap-1.5 text-[10px] font-black px-3 py-1.5 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/25 disabled:opacity-50">
                      {f.channel === "whatsapp" ? <><ExternalLink className="w-3 h-3" /> Send</> : <>Approve</>}
                    </button>
                  ) : (
                    <a href="#scripts" className="text-[9px] font-black px-2.5 py-1.5 rounded-lg border border-amber-500/40 text-amber-400 hover:bg-amber-500/10">Approve script first</a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
