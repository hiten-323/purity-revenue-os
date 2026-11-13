"use client";
import React, { useEffect, useState, useCallback } from "react";
import {
  Zap, Mail, MessageCircle, Phone, CheckCircle,
  RefreshCw, ChevronDown, ChevronRight, Package, FileText, Repeat, Calendar,
} from "lucide-react";
import EmailApprovalCenter from "./EmailApprovalCenter";
import FollowUpCenter from "./FollowUpCenter";

// ─── Types ────────────────────────────────────────────────────────────────────

interface QueueItem {
  lead_id: number;
  company: string;
  city?: string | null;
  contact_name?: string | null;
  division?: string | null;
  current_status?: string | null;
  status?: string | null;
  fps?: number;
  rrs?: number;
  cta_label?: string;
  reason?: string;
  confidence_pct?: number;
  evidence?: string[];
  expected_margin_rs?: number;
  expected_duration_min?: number;
  days_stalled?: number;
  email?: string | null;
  phone?: string | null;
  whatsapp_number?: string | null;
  estimated_value?: number;
}

interface Queue { label: string; count: number; items: QueueItem[]; }
type Queues = Record<string, Queue>;

const QUEUE_ORDER: { key: string; icon: React.ReactNode; workflow: string | null }[] = [
  { key: "email_approval", icon: <Mail className="w-3.5 h-3.5" />,          workflow: null },
  { key: "whatsapp",       icon: <MessageCircle className="w-3.5 h-3.5" />, workflow: "WHATSAPP_CONFIRM" },
  { key: "ai_calls",       icon: <Phone className="w-3.5 h-3.5" />,         workflow: "AI_CALL" },
  { key: "founder_calls",  icon: <Phone className="w-3.5 h-3.5" />,         workflow: "FOUNDER_CALL" },
  { key: "meetings",       icon: <Calendar className="w-3.5 h-3.5" />,      workflow: "MEETING_BOOK" },
  { key: "samples",        icon: <Package className="w-3.5 h-3.5" />,       workflow: "SAMPLE_DISPATCH" },
  { key: "proposals",      icon: <FileText className="w-3.5 h-3.5" />,      workflow: "PROPOSAL_SEND" },
  { key: "orders",         icon: <CheckCircle className="w-3.5 h-3.5" />,   workflow: null },
  { key: "reorders",       icon: <Repeat className="w-3.5 h-3.5" />,        workflow: "REORDER" },
];

function rs(n: number) {
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(1) + "Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + "L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + "K";
  return "₹" + n;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ActionQueueCenter() {
  const [open, setOpen] = useState(true);
  const [queues, setQueues] = useState<Queues | null>(null);
  const [tab, setTab] = useState("email_approval");
  const [loading, setLoading] = useState(false);
  const [busyLead, setBusyLead] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000); };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/workflow/queues").catch(() => null);
      const d = r?.ok ? await r.json().catch(() => null) : null;
      setQueues(d?.queues || null);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const runWorkflow = async (workflowType: string, item: QueueItem, payload: Record<string, unknown> = {}) => {
    setBusyLead(item.lead_id);
    try {
      const r = await fetch("/api/v1/workflow/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow_type: workflowType, lead_id: item.lead_id, payload }),
      });
      const d = await r.json();
      if (d.status === "completed") showToast(`✓ ${item.company} — done`);
      else if (d.status === "policy_blocked") showToast(`⛔ ${item.company}: ${d.reason}`);
      else showToast(`✗ ${item.company}: ${d.error || "failed"}`);
      await load();
    } catch { showToast("Workflow failed — check backend"); }
    finally { setBusyLead(null); }
  };

  const openWhatsApp = async (item: QueueItem) => {
    const phone = (item.whatsapp_number || item.phone || "").replace(/\D/g, "");
    if (!phone) { showToast("No WhatsApp number on file"); return; }
    const name = (item.contact_name || "").split(" ")[0] || "";
    const tRes = await fetch(
      `/api/v1/templates/${encodeURIComponent(item.division || "corporate")}?name=${encodeURIComponent(name)}&city=${encodeURIComponent(item.city || "")}`
    ).catch(() => null);
    const t = tRes?.ok ? await tRes.json().catch(() => null) : null;
    const msg: string = t?.whatsapp_message || "";
    window.open(`https://wa.me/${phone}?text=${encodeURIComponent(msg)}`, "_blank");
  };

  const totalPending = queues
    ? Object.values(queues).reduce((s, q) => s + (q.count || 0), 0)
    : 0;

  const active = queues?.[tab];
  const activeCfg = QUEUE_ORDER.find(q => q.key === tab);

  return (
    <div className="mb-6 bg-gray-900 border border-amber-500/20 rounded-xl overflow-hidden">
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl max-w-sm">
          {toast}
        </div>
      )}

      {/* Header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-800/40 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="relative">
            <Zap className="w-4 h-4 text-amber-400" />
            {totalPending > 0 && (
              <span className="absolute -top-1.5 -right-1.5 min-w-[14px] h-3.5 bg-amber-500 rounded-full text-[8px] text-black flex items-center justify-center font-black px-0.5">
                {totalPending}
              </span>
            )}
          </div>
          <div className="text-left">
            <span className="text-sm font-black text-gray-100">Action Queue — Operating Center</span>
            <p className="text-[10px] text-gray-500 mt-0.5">All execution happens here. Nothing sends without your approval.</p>
          </div>
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-gray-600" /> : <ChevronRight className="w-4 h-4 text-gray-600" />}
      </button>

      {open && (
        <div className="border-t border-gray-800">
          {/* Queue tabs */}
          <div className="flex items-center gap-1 px-4 pt-3 pb-2 overflow-x-auto">
            {QUEUE_ORDER.map(q => {
              const queue = queues?.[q.key];
              const isActive = tab === q.key;
              return (
                <button
                  key={q.key}
                  onClick={() => setTab(q.key)}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-[9px] font-black whitespace-nowrap transition-all ${
                    isActive ? "bg-amber-500/15 border border-amber-500/40 text-amber-400"
                             : "text-gray-600 hover:text-gray-400 border border-transparent"
                  }`}
                >
                  {q.icon}
                  {queue?.label || q.key}
                  {(queue?.count || 0) > 0 && (
                    <span className={`min-w-[14px] h-3.5 rounded-full text-[8px] flex items-center justify-center font-black px-1 ${
                      isActive ? "bg-amber-500 text-black" : "bg-gray-800 text-gray-400"
                    }`}>
                      {queue?.count}
                    </span>
                  )}
                </button>
              );
            })}
            <button onClick={load} disabled={loading} className="ml-auto p-2 text-gray-600 hover:text-gray-300">
              <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>

          {/* Tab content */}
          <div className="px-4 pb-4">
            {tab === "email_approval" ? (
              <EmailApprovalCenter />
            ) : tab === "whatsapp" ? (
              <div className="space-y-4">
                <FollowUpCenter />
                {active && active.items.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-[9px] font-black uppercase tracking-widest text-gray-500">First-touch WhatsApp (Decision Engine)</p>
                    {active.items.map(item => (
                      <QueueRow key={item.lead_id} item={item} busy={busyLead === item.lead_id}>
                        <button onClick={() => openWhatsApp(item)}
                          className="text-[8px] font-black px-2.5 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20">
                          Open WhatsApp
                        </button>
                        <button onClick={() => runWorkflow("WHATSAPP_CONFIRM", item)}
                          className="text-[8px] font-black px-2.5 py-1.5 rounded-lg border border-gray-700 text-gray-400 hover:text-gray-200">
                          Mark Sent
                        </button>
                      </QueueRow>
                    ))}
                  </div>
                )}
              </div>
            ) : !active || active.items.length === 0 ? (
              <div className="text-center py-10">
                <CheckCircle className="w-7 h-7 text-green-500 mx-auto mb-3" />
                <p className="text-[11px] font-black text-gray-400">
                  {queues ? "Queue empty — nothing waiting here" : "No real data available."}
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {active.items.map(item => (
                  <QueueRow key={item.lead_id} item={item} busy={busyLead === item.lead_id}>
                    {tab === "founder_calls" && (item.phone || item.whatsapp_number) && (
                      <a href={"tel:" + (item.phone || item.whatsapp_number || "").replace(/\D/g, "")}
                        className="text-[8px] font-black px-2.5 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20">
                        Call Now
                      </a>
                    )}
                    {activeCfg?.workflow && (
                      <button
                        onClick={() => runWorkflow(activeCfg.workflow!, item)}
                        disabled={busyLead === item.lead_id}
                        className="text-[8px] font-black px-2.5 py-1.5 rounded-lg border border-blue-500/30 bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 disabled:opacity-50">
                        {busyLead === item.lead_id ? "Running…" : (item.cta_label || active.label)}
                      </button>
                    )}
                  </QueueRow>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Row ──────────────────────────────────────────────────────────────────────

function QueueRow({ item, busy, children }: { item: QueueItem; busy: boolean; children: React.ReactNode }) {
  return (
    <div className={`rounded-xl border border-gray-800 bg-gray-950/50 px-4 py-3 flex items-center gap-3 ${busy ? "opacity-60" : ""}`}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-black text-gray-200">{item.company}</span>
          {item.city && <span className="text-[8px] text-gray-600">{item.city}</span>}
          {(item.current_status || item.status) && (
            <span className="text-[7px] font-black px-1.5 py-0.5 bg-gray-800 text-gray-500 rounded uppercase">{item.current_status || item.status}</span>
          )}
        </div>
        {item.reason && <p className="text-[8px] text-gray-600 mt-0.5">{item.reason}</p>}
        {item.evidence && item.evidence.length > 0 && (
          <p className="text-[8px] text-gray-700 mt-0.5">{item.evidence.join(" · ")}</p>
        )}
      </div>
      <div className="text-right shrink-0 hidden sm:block">
        <p className="text-[9px] font-black text-emerald-400">
          {rs(item.expected_margin_rs ?? Math.round((item.estimated_value || 0) * 0.31))}
        </p>
        <p className="text-[8px] text-gray-600">
          margin{typeof item.confidence_pct === "number" ? ` · ${item.confidence_pct}% conf` : ""}
        </p>
      </div>
      <div className="flex gap-1.5 shrink-0">{children}</div>
    </div>
  );
}
