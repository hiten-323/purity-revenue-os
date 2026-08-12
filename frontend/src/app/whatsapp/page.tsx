"use client";
import React, { useEffect, useState, useCallback, useMemo } from "react";
import { MessageCircle, ExternalLink, Check, RefreshCw, ShieldAlert, TrendingUp, Clock } from "lucide-react";

/**
 * WhatsApp Send Queue — the honest way to work the follow-up backlog.
 *
 * wa.me cannot send on our behalf: it opens WhatsApp with the text pre-filled and
 * the founder presses send. So this is a strict two-step loop per lead:
 *
 *   1. Send    -> prepares the link (status "prepared") and opens WhatsApp.
 *                 The lead is NOT marked sent — nothing has gone out yet.
 *   2. Confirm -> the founder states they actually sent it. Only this records a
 *                 real WHATSAPP_SENT founder action and queues the AI-call step.
 *
 * Bulk "approve all" is deliberately absent: it would mark 109 messages sent
 * while sending none, and the learning engine trains on founder actions.
 */

interface FollowUp {
  reminder_id: number;
  lead_id: number;
  company: string;
  city: string;
  margin: number;
  current_status: string;
  channel_approved: boolean;
  message: string;
  due_at: string;
}

function rs(n: number) {
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(2) + " Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + " L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + " K";
  return "₹" + Math.round(n);
}

function daysOverdue(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  return d <= 0 ? "due today" : `${d}d overdue`;
}

export default function WhatsAppQueuePage() {
  const [items, setItems] = useState<FollowUp[]>([]);
  const [loading, setLoading] = useState(true);
  const [opened, setOpened] = useState<Set<number>>(new Set());   // link opened, awaiting confirm
  const [busy, setBusy] = useState<number | null>(null);
  const [doneCount, setDoneCount] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const say = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3500); };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/outreach/followups/due").catch(() => null);
      const d = r?.ok ? await r.json().catch(() => null) : null;
      const wa: FollowUp[] = (d?.by_channel?.whatsapp || []);
      // Highest margin first — work the money, not the list order.
      wa.sort((a, b) => (b.margin || 0) - (a.margin || 0));
      setItems(wa);
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  // Step 1: prepare + open WhatsApp. Does NOT mark the lead sent.
  const send = async (f: FollowUp) => {
    setBusy(f.lead_id);
    try {
      const r = await fetch("/api/v1/b2b/outreach/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_id: f.lead_id, channel: "whatsapp", reminder_id: f.reminder_id }),
      });
      const d = await r.json();
      const url = d?.detail?.whatsapp_url;
      if (!url) { say(d?.detail || d?.message || "Could not prepare the message"); return; }
      window.open(url, "_blank", "noopener");
      setOpened(prev => new Set(prev).add(f.lead_id));
      say(`WhatsApp opened for ${f.company} — press send, then confirm here.`);
    } catch { say("Network error preparing the message"); }
    finally { setBusy(null); }
  };

  // Step 2: the founder confirms a REAL send. Only now is it recorded.
  const confirm = async (f: FollowUp) => {
    setBusy(f.lead_id);
    try {
      const r = await fetch(`/api/v1/b2b/leads/${f.lead_id}/mark-whatsapp`, { method: "POST" });
      const d = await r.json();
      if (d?.status === "confirmed" || d?.status === "already_confirmed") {
        setItems(prev => prev.filter(x => x.lead_id !== f.lead_id));
        setDoneCount(n => n + 1);
        const nxt = d?.next_reminder?.channel;
        say(`✓ Recorded for ${f.company}${nxt ? ` · ${nxt} queued next` : ""}`);
      } else { say("Could not record the send"); }
    } catch { say("Network error recording the send"); }
    finally { setBusy(null); }
  };

  const totalMargin = useMemo(() => items.reduce((s, i) => s + (i.margin || 0), 0), [items]);
  const replied = (s: string) => !["EMAIL_SENT", "DISCOVERED", "COLD"].includes(s);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* No <GlobalHeader/> here — layout.tsx renders it for every route, so
          mounting it again drew two identical nav bars stacked on this page. */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl max-w-sm">{toast}</div>
      )}

      <div className="max-w-[1100px] mx-auto px-4 py-6">
        <div className="flex items-start justify-between flex-wrap gap-3 mb-5">
          <div>
            <h1 className="text-xl font-black flex items-center gap-2">
              <MessageCircle className="w-5 h-5 text-green-400" /> WhatsApp Send Queue
            </h1>
            <p className="text-[11px] text-gray-500 mt-0.5">
              Highest margin first. Send opens WhatsApp — nothing is recorded until you confirm you actually sent it.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-lg font-black text-emerald-400">{rs(totalMargin)}</p>
              <p className="text-[9px] text-gray-600 font-bold uppercase tracking-wider">margin in queue · {items.length} left</p>
            </div>
            {doneCount > 0 && (
              <div className="text-right">
                <p className="text-lg font-black text-green-400">{doneCount}</p>
                <p className="text-[9px] text-gray-600 font-bold uppercase tracking-wider">sent this session</p>
              </div>
            )}
            <button onClick={load} disabled={loading} className="p-2 text-gray-500 hover:text-gray-200">
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>

        {loading && items.length === 0 ? (
          <div className="text-center py-16 text-[11px] text-gray-600 font-black uppercase tracking-widest animate-pulse">Loading queue…</div>
        ) : items.length === 0 ? (
          <div className="text-center py-16">
            <Check className="w-8 h-8 text-green-500 mx-auto mb-3" />
            <p className="text-sm font-black text-gray-400">Queue clear</p>
            <p className="text-[10px] text-gray-600 mt-1">New follow-ups appear 3 days after an intro email goes unanswered.</p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {items.map(f => {
              const isOpen = opened.has(f.lead_id);
              const warm = replied(f.current_status);
              return (
                <div key={f.lead_id} className={`bg-gray-900 border rounded-xl p-4 ${isOpen ? "border-green-500/40" : "border-gray-800"}`}>
                  <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="text-xs font-black text-gray-100">{f.company}</h3>
                        <span className={`text-[8px] font-black px-1.5 py-0.5 rounded border ${
                          warm ? "bg-green-500/15 text-green-400 border-green-500/30"
                               : "bg-amber-500/15 text-amber-400 border-amber-500/30"}`}>
                          {warm ? "REPLIED — warm" : "NO REPLY — re-engage"}
                        </span>
                        {!f.channel_approved && (
                          <span className="flex items-center gap-1 text-[8px] font-black text-amber-400">
                            <ShieldAlert className="w-3 h-3" /> script not approved
                          </span>
                        )}
                      </div>
                      <p className="text-[9px] text-gray-500 mt-0.5 flex items-center gap-2">
                        {f.city || "—"} · <Clock className="w-2.5 h-2.5" /> {daysOverdue(f.due_at)}
                      </p>
                      <pre className="text-[10px] text-gray-400 whitespace-pre-wrap font-sans mt-2 bg-gray-950 border border-gray-800 rounded-lg p-2.5 leading-relaxed">
{f.message}
                      </pre>
                    </div>

                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <div className="text-right">
                        <p className="text-sm font-black text-emerald-400 flex items-center gap-1">
                          <TrendingUp className="w-3 h-3" />{rs(f.margin)}
                        </p>
                        <p className="text-[8px] text-gray-600 uppercase font-bold">est. margin</p>
                      </div>
                      {!isOpen ? (
                        <button onClick={() => send(f)} disabled={busy === f.lead_id || !f.channel_approved}
                          className="flex items-center gap-1.5 text-[10px] font-black px-3 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white disabled:opacity-40 disabled:cursor-not-allowed">
                          <ExternalLink className="w-3 h-3" /> {busy === f.lead_id ? "Opening…" : "Send"}
                        </button>
                      ) : (
                        <div className="flex flex-col items-end gap-1">
                          <button onClick={() => confirm(f)} disabled={busy === f.lead_id}
                            className="flex items-center gap-1.5 text-[10px] font-black px-3 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-gray-950 disabled:opacity-50">
                            <Check className="w-3 h-3" /> {busy === f.lead_id ? "Saving…" : "I sent it"}
                          </button>
                          <button onClick={() => send(f)} className="text-[8px] text-gray-500 hover:text-gray-300 underline">reopen</button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
