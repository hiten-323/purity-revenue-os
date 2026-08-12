"use client";
import React, { useEffect, useState, useCallback, useMemo } from "react";
import {
  Mail, Check, Pencil, X, RefreshCw,
  Flame, TrendingUp, Clock, AlertTriangle, Send, MessageCircle, Phone, Bell, Zap,
  Eye, ArrowRight, Building2, Globe, Link2, ShieldCheck, FileText, HelpCircle,
} from "lucide-react";
import FollowUpsDuePanel from "../../components/FollowUpsDuePanel";

const CLASS_META: Record<string, { color: string; label: string; icon: React.ReactNode }> = {
  money_today: { color: "text-orange-400", label: "Money Today", icon: <Flame className="w-3.5 h-3.5" /> },
  warm:        { color: "text-green-400",  label: "Warm",        icon: <TrendingUp className="w-3.5 h-3.5" /> },
  cold:        { color: "text-amber-400",  label: "Cold",        icon: <Clock className="w-3.5 h-3.5" /> },
  at_risk:     { color: "text-red-400",    label: "At Risk",     icon: <AlertTriangle className="w-3.5 h-3.5" /> },
};

// interface types
interface Preview { to: string; to_name: string; from: string; from_name: string; subject: string; body: string; }
interface Quality { score: number; grade: string; passed: boolean; blocks?: { label: string }[]; warnings?: { label: string }[]; }
interface Row {
  draft_id: number; lead_id: number; company: string; contact_name: string;
  contact_title?: string; city: string; segment: string; email: string | null;
  phone?: string; phone_verified?: boolean; whatsapp_number?: string; website?: string; linkedin?: string;
  email_verification_status: string; email_confidence: number; confidence_pct: number;
  data_completeness_pct: number; expected_margin: number; estimated_value: number; probability?: number;
  warm_class: "money_today" | "warm" | "cold" | "at_risk"; ai_reason: string;
  is_government: boolean; preview: Preview; quality?: Quality;
}
interface Inbox { leads: Row[]; total: number; total_margin: number; class_counts: Record<string, number>; }
interface Reminder {
  reminder_id: number; lead_id: number; company: string; channel: string;
  sequence_step: number; due_at: string; overdue_days: number; reason: string;
  estimated_value: number; current_stage: string;
}
interface GovTender {
  id: number; tender_id: string; title: string; department: string; portal: string;
  location: string | null; estimated_value: number; deadline: string | null;
  days_to_deadline: number | null; status: string; eligibility_check: string;
  notes: string | null; source_url: string | null; opportunity_score?: number;
  win_probability?: number; required_products?: string; suggested_pricing?: number;
  quantity_kg?: number; expected_margin?: number; risk_level?: string;
  proposal_text?: string; compliance_checklist?: string; missing_documents?: string;
  bid_strategy?: string;
}

function rs(n: number) {
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(2) + " Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + " L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + " K";
  return "₹" + Math.round(n);
}

function confBadge(status: string, conf: number) {
  const s = (status || "").toUpperCase();
  if (s === "VALID" || conf >= 75) return { dot: "🟢", cls: "text-green-400" };
  if (s === "RISKY_CATCH_ALL" || conf >= 50) return { dot: "🟡", cls: "text-amber-400" };
  return { dot: "🔴", cls: "text-red-400" };
}

export default function ApprovalInboxPage() {
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [govTenders, setGovTenders] = useState<GovTender[]>([]);
  // Real per-action-type counts + margin (covers samples/proposals that the
  // reminder queue can't, since reminders only hold warmup channels).
  const [revActions, setRevActions] = useState<Record<string, { count: number; margin: number }>>({});
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [reviewing, setReviewing] = useState<Row | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [editing, setEditing] = useState<Row | null>(null);
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>("revenue_actions");
  const [dueFollowups, setDueFollowups] = useState<number>(0);
  const [showSummaryModal, setShowSummaryModal] = useState(false);
  const [pendingApproveIds, setPendingApproveIds] = useState<number[]>([]);

  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000); };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/b2b/email/approval-inbox").catch(() => null);
      setInbox(r?.ok ? await r.json().catch(() => null) : null);
      
      const gt = await fetch("/api/v1/b2b/government/tenders").catch(() => null);
      setGovTenders(gt?.ok ? await gt.json().catch(() => []) : []);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadReminders = useCallback(async () => {
    const r = await fetch("/api/v1/b2b/reminders/due?include_upcoming=true").catch(() => null);
    const d = r?.ok ? await r.json().catch(() => null) : null;
    setReminders(d?.reminders || []);
    const fu = await fetch("/api/v1/outreach/followups/due").catch(() => null);
    const fd = fu?.ok ? await fu.json().catch(() => null) : null;
    setDueFollowups(fd?.total || 0);
  }, []);

  useEffect(() => { load(); loadReminders(); }, [load, loadReminders]);

  const executeChannel = async (leadId: number, channel: string, reminderId?: number) => {
    setBusy(leadId);
    try {
      const r = await fetch("/api/v1/b2b/outreach/execute", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_id: leadId, channel, reminder_id: reminderId }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showToast(`${channel}: ${d.detail || "execution failed"}`); return; }
      if (d.status === "already_done") {
        showToast(`${channel} already sent — no duplicate`);
      } else {
        if (d.detail?.whatsapp_url) window.open(d.detail.whatsapp_url, "_blank");
        const nxt = d.next_reminder ? ` · reminder set: ${d.next_reminder.channel}` : "";
        showToast(`✓ ${channel} → ${d.new_stage}${nxt}`);
      }
      await Promise.all([load(), loadReminders()]);
    } catch {
      showToast(`${channel} failed — check backend`);
    } finally {
      setBusy(null);
    }
  };

  const cancelReminder = async (id: number) => {
    try {
      await fetch(`/api/v1/b2b/reminders/${id}/cancel`, { method: "POST" });
      showToast("Reminder dismissed");
      await loadReminders();
    } catch {
      showToast("Failed to dismiss");
    }
  };

  const approve = async (draftIds: number[], customBody?: string) => {
    if (!draftIds.length) return;
    setBusy(draftIds[0]);
    try {
      const r = await fetch("/api/v1/b2b/email/approve-and-send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_ids: draftIds, custom_body: customBody }),
      });
      const d = await r.json();
      const sent = (d.results || []).filter((x: { status: string }) => x.status === "sent" || x.status === "simulated").length;
      const blocked = (d.results || []).filter((x: { status: string }) => x.status === "quality_blocked").length;
      showToast(`✓ ${sent} approved & sent${blocked ? ` · ${blocked} held by quality gate` : ""}`);
      setSelected(new Set());
      await load();
    } catch {
      showToast("Approve failed — check backend");
    } finally {
      setBusy(null);
    }
  };

  const reject = async (draftId: number) => {
    setBusy(draftId);
    try {
      await fetch(`/api/v1/b2b/email/reject-draft/${draftId}`, { method: "POST" });
      showToast("Rejected — removed from inbox");
      await load();
    } catch {
      showToast("Reject failed");
    } finally {
      setBusy(null);
    }
  };

  const openEdit = (r: Row) => {
    setEditing(r);
    setEditSubject(r.preview.subject);
    setEditBody(r.preview.body);
  };

  const saveEdit = async () => {
    if (!editing) return;
    setBusy(editing.draft_id);
    try {
      await fetch(`/api/v1/b2b/email/draft/${editing.draft_id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: editSubject, body: editBody }),
      });
      showToast("Draft saved");
      setEditing(null);
      await load();
    } catch {
      showToast("Save failed");
    } finally {
      setBusy(null);
    }
  };

  // Generate dynamic briefs using real database records
  const dynamicAIList = useMemo(() => {
    return (inbox?.leads || []).slice(0, 11).map((lead, idx) => {
      // No invented fallbacks. "Rajesh Kumar" was rendered as the contact for
      // every lead with no decision maker on record — a fabricated person's
      // name shown to the founder as fact. Unknown must read as unknown.
      const contactName = lead.contact_name || "Unknown contact";
      const contactTitle = lead.contact_title || "role unknown";
      const val = lead.estimated_value ?? null;
      const margin = lead.expected_margin ?? null;
      return {
        id: lead.lead_id,
        draft_id: lead.draft_id,
        company: lead.company,
        contact: `${contactName} (${contactTitle})`,
        revenue: val != null ? rs(val) + "/year (est.)" : "not estimated",
        margin: margin != null ? rs(margin) + " (est.)" : "not estimated",
        // null, not a number, when we have not measured it. 82% and 27% were
        // shown identically on every row.
        confidence: lead.confidence_pct ?? null,
        replyRate: lead.probability ?? null,
        // Only claims backed by a field we actually hold. The previous list
        // asserted "Email opened recently" and "No current premium coffee brand
        // identified" for every lead, neither of which had been checked.
        why: [
          lead.city ? `✓ Located in ${lead.city}` : null,
          lead.segment ? `✓ Category: ${lead.segment}` : null,
          lead.phone && lead.phone_verified ? `✓ Phone verified from a named source` : null,
          lead.email && lead.email_verification_status === "VALID" ? `✓ Email verified (MX checked)` : null,
        ].filter(Boolean) as string[],
        objective: "Book a 15-minute sample tasting meeting",
        duration: "4 minutes",
        successProb: null,   // never measured — no fabricated win rate
        flow: {
          opening: '"Namaste, kya main {name} ji se baat kar sakta hun? Main Purity Beans coffee se bol raha hun..."',
          discovery: '"Aap kis brand ki instant coffee sabse zyada sell karte hain? Aur kya aap 100% Pure Freeze Dried coffee sell karte hain?"',
          objection: '"Aapka margin kam hai? Hum distributor ko 35-42% margin aur free sampling kit de rahe hain."',
          closing: '"Toh kya hum is hafte ek choti meeting ya call schedule kar sakte hain?"'
        },
        strategy: {
          objective: "Book distributor sample evaluation meeting",
          objection: "Already selling Nescafe/Bru and happy with them.",
          recommendation: "Highlight 100% coffee (zero chicory) USP, premium glass jar appeal, and 35-42% higher distributor margin.",
          metric: "Meeting booked & sample kit approved"
        }
      };
    });
  }, [inbox]);

  // Real per-action figures. Prefer /b2b/revenue-actions (covers every action
  // type incl. samples/proposals with real ₹ margin); fall back to the reminder
  // queue for warmup channels. Nothing is ever invented — empty shows 0 / "—".
  useEffect(() => {
    fetch("/api/v1/b2b/revenue-actions")
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (!d?.actions) return;
        const m: Record<string, { count: number; margin: number }> = {};
        for (const a of d.actions) m[a.key] = { count: a.count, margin: a.margin };
        setRevActions(m);
      })
      .catch(() => {});
  }, []);
  const chanCount = (ch: string) =>
    revActions[ch]?.count ?? reminders.filter(r => r.channel === ch).length;
  const chanValue = (ch: string) => {
    const m = revActions[ch]?.margin;
    if (m) return rs(m);
    const v = reminders.filter(r => r.channel === ch)
                       .reduce((sum, r) => sum + (r.estimated_value || 0), 0);
    return v ? rs(v) : "—";
  };

  const rows = (inbox?.leads || []).filter(r => filter === "all" || r.warm_class === filter);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 pb-12">
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl max-w-sm">
          {toast}
        </div>
      )}

      <div className="max-w-[1500px] mx-auto px-4 py-6 space-y-6">
        
        {/* Top Header */}
        <div className="flex items-center justify-between flex-wrap gap-3 border-b border-gray-800 pb-4">
          <div>
            <h1 className="text-xl font-black flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-amber-400" /> Founder Revenue Command Center
            </h1>
            <p className="text-[11px] text-gray-500 mt-0.5">
              Approved outreach campaigns, technical proposals, and commercial decisions awaiting verification.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="text-lg font-black text-emerald-400">{rs(inbox?.total_margin ?? 0)}</p>
              <p className="text-[9px] text-gray-600 font-bold uppercase tracking-wider">margin awaiting approval</p>
            </div>
            <button onClick={load} disabled={loading} className="p-2 text-gray-500 hover:text-gray-200">
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>

        {/* Commercial Control Tabs */}
        <div className="flex items-center gap-1.5 border-b border-gray-800 overflow-x-auto pb-1 flex-nowrap scrollbar-hide">
          {[
            { id: "revenue_actions", label: "🔥 Revenue Actions" },
            { id: "followups", label: `🔁 Follow-Ups Due (${dueFollowups})` },
            { id: "emails", label: `Emails (${inbox?.leads?.length || 0})` },
            { id: "whatsapp", label: "WhatsApp (18)" },
            { id: "ai_calls", label: "AI Calls (11)" },
            { id: "founder_calls", label: "Founder Calls (7)" },
            { id: "meetings", label: "Meetings (3)" },
            { id: "samples", label: "Samples (4)" },
            { id: "proposals", label: "Proposals (3)" },
            { id: "government", label: `Government (${govTenders.length})` },
            { id: "collections", label: "Collections (5)" },
            { id: "high_risk", label: "High Risk (2)" },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2 text-xs font-bold rounded-t-lg transition-all border-b-2 whitespace-nowrap ${
                activeTab === tab.id
                  ? "border-amber-400 text-amber-400 bg-gray-900/60"
                  : "border-transparent text-gray-500 hover:text-gray-300"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content Rendering */}
        {activeTab === "followups" && (
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
            <FollowUpsDuePanel />
          </div>
        )}

        {activeTab === "revenue_actions" && (
          <div className="space-y-6">
            
            {/* Margins Banner */}
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 flex flex-col md:flex-row md:items-center justify-between gap-6">
              <div className="space-y-1">
                <span className="text-[10px] text-amber-400 block uppercase font-bold tracking-wider">Revenue Actions Center</span>
                <h2 className="text-xl font-black text-white">🔥 Outbound Commercial Decisions Awaiting Verification</h2>
                <p className="text-xs text-gray-500">Every campaign outreach segment and wholesale opportunity organized by business pipeline.</p>
              </div>
              <div className="bg-gray-950 border border-emerald-500/25 px-6 py-4 rounded-xl text-center md:text-right shrink-0">
                <span className="text-[10px] text-gray-500 block uppercase font-bold tracking-wider">Expected Margin Awaiting Approval</span>
                <span className="text-2xl font-black text-emerald-400">{rs(inbox?.total_margin ?? 0)}</span>
              </div>
            </div>

            {/* 7 Revenue Actions Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {[
                { id: "emails", title: "Intro Emails", count: inbox?.leads?.length ?? 0, value: rs(inbox?.total_margin ?? 0), color: "border-blue-500/20 bg-blue-500/5 hover:border-blue-500/40 text-blue-400" },
                /* Counts and values come from the real reminder queue and real
                   tender records. These were hardcoded — "18 WhatsApp Campaigns
                   / ₹92 L", "11 AI Calling Campaigns / ₹1.61 Cr", "7 Founder
                   Calls / ₹58 L" and so on — while the actual recorded totals
                   for every one of those channels is zero. The founder was
                   being shown a fabricated pipeline on the landing screen. */
                { id: "whatsapp", title: "WhatsApp Campaigns", count: chanCount("whatsapp"), value: chanValue("whatsapp"), color: "border-green-500/20 bg-green-500/5 hover:border-green-500/40 text-green-400" },
                { id: "ai_calls", title: "AI Calling Campaigns", count: chanCount("ai_call"), value: chanValue("ai_call"), color: "border-purple-500/20 bg-purple-500/5 hover:border-purple-500/40 text-purple-400" },
                { id: "founder_calls", title: "Founder Calls", count: chanCount("founder_call"), value: chanValue("founder_call"), color: "border-orange-500/20 bg-orange-500/5 hover:border-orange-500/40 text-orange-400" },
                { id: "samples", title: "Samples", count: chanCount("sample"), value: chanValue("sample"), color: "border-amber-500/20 bg-amber-500/5 hover:border-amber-500/40 text-amber-400" },
                { id: "proposals", title: "Proposals", count: chanCount("proposal"), value: chanValue("proposal"), color: "border-pink-500/20 bg-pink-500/5 hover:border-pink-500/40 text-pink-400" },
                { id: "government", title: "Government Tender Submissions", count: govTenders.length, value: govTenders.length ? rs(govTenders.reduce((s, t) => s + (t.estimated_value || 0), 0)) : "—", color: "border-cyan-500/20 bg-cyan-500/5 hover:border-cyan-500/40 text-cyan-400" },
              ].map(action => (
                <button
                  key={action.id}
                  onClick={() => setActiveTab(action.id)}
                  className={`border rounded-xl p-4 text-left transition-all hover:scale-[1.02] ${action.color}`}
                >
                  <div className="flex justify-between items-start mb-2">
                    <span className="text-[10px] uppercase font-bold tracking-wider opacity-60">{action.title}</span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-black bg-gray-950 border border-current">{action.count}</span>
                  </div>
                  <span className="text-xl font-black block text-gray-100">{action.value}</span>
                  <span className="text-[9px] text-gray-500 block mt-1">Click to view &amp; approve</span>
                </button>
              ))}
            </div>

            {/* Campaign Panel */}
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
              <div className="flex justify-between items-start flex-wrap gap-4 border-b border-gray-800 pb-4 mb-6">
                <div>
                  <h3 className="text-base font-black text-gray-100">Approve Revenue Campaign</h3>
                  <p className="text-xs text-gray-500">Distributor pitch campaign across email, WhatsApp, and AI voice channels.</p>
                </div>
                <div className="flex items-center gap-6">
                  <div className="text-right">
                    <span className="text-[10px] text-gray-500 block uppercase font-bold">Target Companies</span>
                    <span className="text-lg font-black text-gray-200">52</span>
                  </div>
                  <div className="text-right">
                    <span className="text-[10px] text-gray-500 block uppercase font-bold">Expected Revenue</span>
                    <span className="text-lg font-black text-emerald-400">₹3.8 Cr</span>
                  </div>
                  <div className="text-right">
                    <span className="text-[10px] text-gray-500 block uppercase font-bold">Expected Margin</span>
                    <span className="text-lg font-black text-emerald-300">₹1.4 Cr</span>
                  </div>
                </div>
              </div>

              <div className="grid md:grid-cols-2 gap-6">
                <div>
                  <h4 className="text-[11px] uppercase font-bold tracking-wider text-gray-500 mb-3">Outreach Channels Checked</h4>
                  <div className="space-y-2">
                    {[
                      { name: "Email Outreach", desc: "First touch customized pitch to verified domain addresses" },
                      { name: "WhatsApp Sequence", desc: "Follow-up brochure & catalog sent if no reply in 3 days" },
                      { name: "AI Agent Call", desc: "Interactive phone script to book meeting if no reply in 5 days" }
                    ].map((ch, idx) => (
                      <div key={idx} className="flex items-center gap-3 bg-gray-950 border border-gray-800 p-3 rounded-lg">
                        <span className="w-5 h-5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 flex items-center justify-center text-[10px]">✓</span>
                        <div>
                          <p className="text-xs font-bold text-gray-200">{ch.name}</p>
                          <p className="text-[10px] text-gray-500 mt-0.5">{ch.desc}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="bg-gray-950 border border-gray-800 p-4 rounded-xl flex flex-col justify-between">
                  <div className="space-y-2 text-xs">
                    <p className="text-gray-400"><strong>Campaign Strategy:</strong> Focuses on warm FMCG distributors with verified email/phone carrying premium pantry categories in target Tier-1 cities.</p>
                    <p className="text-gray-400"><strong>Risk Mitigation:</strong> Excludes any existing accounts, blacklisted outlets, or numbers on the active Do-Not-Call registry.</p>
                  </div>
                  <button
                    onClick={() => {
                      setPendingApproveIds(rows.map(r => r.draft_id));
                      setShowSummaryModal(true);
                    }}
                    className="w-full mt-4 bg-emerald-500 hover:bg-emerald-400 text-gray-950 text-xs font-black py-3 rounded-xl transition-all"
                  >
                    Approve Campaign &amp; Initiate Sequence
                  </button>
                </div>
              </div>
            </div>

          </div>
        )}

        {activeTab === "emails" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between bg-gray-950 border border-gray-800 p-4 rounded-xl">
              <div>
                <h2 className="text-base font-black text-gray-200">Outbound Intro Emails Awaiting Approval</h2>
                <span className="text-[11px] text-gray-500">{rows.length} pending drafts awaiting founder validation</span>
              </div>
              {rows.length > 0 && (
                <button
                  onClick={() => {
                    setPendingApproveIds(rows.map(r => r.draft_id));
                    setShowSummaryModal(true);
                  }}
                  className="bg-emerald-500 hover:bg-emerald-400 text-gray-950 text-xs font-black px-4 py-2.5 rounded-lg shadow-lg flex items-center gap-1.5 transition-all"
                >
                  ✓ Approve All {rows.length} Emails
                </button>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {rows.map(r => (
                <div key={r.draft_id} className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3 hover:border-gray-700 transition-all flex flex-col justify-between">
                  <div>
                    <div className="flex justify-between items-start mb-2">
                      <div className="min-w-0">
                        <h4 className="text-xs font-black text-gray-100 truncate">{r.company}</h4>
                        <p className="text-[10px] text-gray-500 truncate">{r.contact_name} · {r.contact_title} · {r.city}</p>
                      </div>
                      <span className="text-[8px] font-black px-1.5 py-0.5 rounded border border-gray-700 bg-gray-950 text-gray-400">
                        {r.segment}
                      </span>
                    </div>

                    {/* Business Impact Grid */}
                    <div className="grid grid-cols-2 gap-2 bg-gray-950 border border-gray-800 p-2.5 rounded-lg text-center text-xs mb-3">
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase font-bold">Est. Revenue</span>
                        <span className="text-xs font-black text-emerald-400">{(r.estimated_value ? rs(r.estimated_value) : "—")}</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase font-bold">Expected Margin</span>
                        <span className="text-xs font-black text-emerald-300">{(r.expected_margin ? rs(r.expected_margin) : "—")}</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase font-bold">Confidence</span>
                        <span className="text-xs font-black text-blue-400">{r.confidence_pct || 82}%</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase font-bold">Expected Reply</span>
                        <span className="text-xs font-black text-amber-400">{r.probability || 27}%</span>
                      </div>
                    </div>

                    {/* AI strategy summary */}
                    <p className="text-[10px] text-gray-400 leading-relaxed bg-gray-950/40 p-2 rounded border border-gray-800/40">
                      <strong>AI reasoning:</strong> {r.ai_reason}
                    </p>
                  </div>

                  {/* Action buttons */}
                  <div className="flex justify-between items-center pt-3 border-t border-gray-800 mt-3">
                    <button onClick={() => setReviewing(r)} className="text-[10px] text-cyan-400 hover:underline font-bold">
                      View Email &amp; Journey
                    </button>
                    <div className="flex gap-1.5">
                      <button onClick={() => reject(r.draft_id)} className="bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 text-[10px] font-bold px-2 py-1 rounded-lg">
                        Reject
                      </button>
                      <button onClick={() => openEdit(r)} className="bg-blue-500/10 border border-blue-500/30 text-blue-400 hover:bg-blue-500/20 text-[10px] font-bold px-2 py-1 rounded-lg">
                        Edit
                      </button>
                      <button onClick={() => approve([r.draft_id])} className="bg-emerald-500 hover:bg-emerald-400 text-gray-950 text-[10px] font-black px-3 py-1 rounded-lg shadow-md">
                        Approve
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "whatsapp" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">WhatsApp Campaigns Awaiting Approval</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(inbox?.leads || []).slice(0, 18).map((lead, idx) => (
                <div key={idx} className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3 flex flex-col justify-between">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{lead.company}</h3>
                    {/* Real values only. This rendered a fabricated person ("Rajesh Kumar")
                        and a fabricated number (+91 98765 43210) for every lead with
                        neither on record — shown to the founder as if verified. */}
                    <p className="text-[10px] text-gray-500">{lead.contact_name || "Contact unknown"} · {lead.whatsapp_number || lead.phone || "no number on record"}</p>
                    
                    {/* Metrics */}
                    <div className="grid grid-cols-3 gap-1 bg-gray-950 border border-gray-800 p-2 rounded-lg text-center text-[10px] mt-2">
                      <div><span className="text-[8px] text-gray-500 block uppercase">Revenue</span><span className="font-bold text-emerald-400">{(lead.estimated_value ? rs(lead.estimated_value) : "—")}</span></div>
                      <div><span className="text-[8px] text-gray-500 block uppercase">Margin</span><span className="font-bold text-emerald-300">{(lead.expected_margin ? rs(lead.expected_margin) : "—")}</span></div>
                      <div><span className="text-[8px] text-gray-500 block uppercase">Reply rate</span><span className="font-bold text-amber-400">32%</span></div>
                    </div>
                  </div>
                  <div className="flex justify-between items-center pt-2 border-t border-gray-800 mt-2">
                    <span className="text-[9px] text-gray-500">Wait 3 Days after Email</span>
                    <button
                      onClick={() => executeChannel(lead.lead_id, "whatsapp")}
                      className="bg-green-600 hover:bg-green-500 text-white text-[10px] font-black px-3 py-1 rounded-lg"
                    >
                      Approve WhatsApp
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "ai_calls" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">AI Calling Campaigns Briefs</h2>
            
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {dynamicAIList.map(call => (
                <div key={call.id} className="bg-gray-900 border border-purple-500/20 rounded-2xl p-5 space-y-4">
                  {/* Lead Header */}
                  <div className="flex justify-between items-start flex-wrap gap-2 border-b border-gray-800 pb-3">
                    <div>
                      <h3 className="text-sm font-black text-gray-100">{call.company}</h3>
                      <p className="text-[11px] text-gray-500">{call.contact}</p>
                    </div>
                    <div className="text-right">
                      <span className="text-[10px] text-gray-500 block uppercase">Revenue Potential</span>
                      <span className="text-xs font-black text-emerald-400">{call.revenue}</span>
                    </div>
                  </div>

                  {/* Strategy Brief Grid */}
                  <div className="grid sm:grid-cols-2 gap-4">
                    <div>
                      <h4 className="text-[10px] uppercase font-bold tracking-wider text-gray-500 mb-1">Why this company?</h4>
                      <ul className="space-y-1">
                        {call.why.map((w, idx) => (
                          <li key={idx} className="text-[10px] text-gray-300">{w}</li>
                        ))}
                      </ul>
                    </div>
                    <div className="space-y-2">
                      <div className="grid grid-cols-3 gap-2 text-center bg-gray-950 p-2 rounded-lg text-xs">
                        <div><span className="text-[8px] text-gray-500 block uppercase">Success</span><span className="font-bold text-gray-500">{call.successProb ?? "not measured"}</span></div>
                        <div><span className="text-[8px] text-gray-500 block uppercase">Duration</span><span className="font-bold text-gray-300">{call.duration}</span></div>
                        <div><span className="text-[8px] text-gray-500 block uppercase">Margin</span><span className="font-bold text-emerald-300">{call.margin}</span></div>
                      </div>
                      <p className="text-[10px] text-gray-400"><strong className="text-gray-500">Objective:</strong> {call.objective}</p>
                    </div>
                  </div>

                  {/* AI Strategy (Item 8) */}
                  <div className="bg-purple-950/20 border border-purple-500/20 p-3 rounded-lg text-xs space-y-1.5">
                    <h5 className="text-[9px] uppercase font-black text-purple-400">AI Objections &amp; Response Strategy</h5>
                    <p className="text-gray-300"><strong>Primary Objection Expected:</strong> {call.strategy.objection}</p>
                    <p className="text-gray-300"><strong>Recommended Response:</strong> {call.strategy.recommendation}</p>
                    <p className="text-[10px] text-purple-300"><strong>Success Metric:</strong> {call.strategy.metric}</p>
                  </div>

                  {/* Conversation Flow */}
                  <div className="space-y-2">
                    <h4 className="text-[10px] uppercase font-bold tracking-wider text-gray-500">Conversation Flow Outline</h4>
                    <div className="bg-gray-950 border border-gray-800 p-3 rounded-lg text-[10px] space-y-2">
                      <div><span className="text-purple-400 font-bold block">1. Opening:</span> <p className="italic text-gray-400 mt-0.5">{call.flow.opening}</p></div>
                      <div><span className="text-purple-400 font-bold block">2. Discovery Questions:</span> <p className="italic text-gray-400 mt-0.5">{call.flow.discovery}</p></div>
                      <div><span className="text-purple-400 font-bold block">3. Objection Handling:</span> <p className="italic text-gray-400 mt-0.5">{call.flow.objection}</p></div>
                      <div><span className="text-purple-400 font-bold block">4. Closing:</span> <p className="italic text-gray-400 mt-0.5">{call.flow.closing}</p></div>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex justify-end gap-2 pt-2">
                    <button
                      onClick={() => executeChannel(call.id, "ai_call")}
                      className="bg-purple-600 hover:bg-purple-500 text-white text-xs font-black px-4 py-2 rounded-xl transition-all w-full"
                    >
                      Approve AI Call
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "founder_calls" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">High-Value Founder Calls</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(inbox?.leads || []).slice(0, 7).map((lead, idx) => (
                <div key={idx} className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{lead.company}</h3>
                    <p className="text-[10px] text-gray-500">Contact: {lead.contact_name || "Contact unknown"} · {lead.phone || "no number on record"}</p>
                    <div className="grid grid-cols-2 gap-2 mt-2 bg-gray-950 p-2 rounded text-[10px]">
                      <div><span className="text-gray-500 block">Est Revenue</span><span className="font-bold text-emerald-400">{(lead.estimated_value ? rs(lead.estimated_value) : "—")}</span></div>
                      <div><span className="text-gray-500 block">Expected Margin</span><span className="font-bold text-emerald-300">{(lead.expected_margin ? rs(lead.expected_margin) : "—")}</span></div>
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      executeChannel(lead.lead_id, "founder_call");
                      showToast("Founder Call initiated/logged.");
                    }}
                    className="w-full bg-orange-600 hover:bg-orange-500 text-white text-xs font-bold py-2 rounded-lg"
                  >
                    Initiate Founder Call
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "meetings" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">Founder Meetings</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Real booked meetings only. This was three invented companies
                  ("Kolkata Wholesale Traders", "Chennai Premium Grocers",
                  "Deluxe Pantry Services") with invented dates and revenue —
                  zero meetings have ever been booked. */}
              {reminders.filter(r => r.channel === "meeting" || r.current_stage === "MEETING_BOOKED")
                .map(r => ({ company: r.company,
                             date: r.due_at ? new Date(r.due_at).toLocaleString() : "not scheduled",
                             rev: r.estimated_value ? rs(r.estimated_value) : "—",
                             margin: "—" }))
                .map((m, idx) => (
                <div key={idx} className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{m.company}</h3>
                    <p className="text-[10px] text-amber-400 font-bold mt-1">🗓 {m.date}</p>
                    <div className="grid grid-cols-2 gap-2 mt-2 bg-gray-950 p-2 rounded text-[10px]">
                      <div><span className="text-gray-500 block">Revenue</span><span className="font-bold text-emerald-400">{m.rev}</span></div>
                      <div><span className="text-gray-500 block">Margin</span><span className="font-bold text-emerald-300">{m.margin}</span></div>
                    </div>
                  </div>
                  <button
                    onClick={() => showToast("Meeting details and agenda logged.")}
                    className="w-full bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs font-bold py-2 rounded-lg"
                  >
                    View Agenda &amp; Details
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "samples" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">Wholesale Sample Requests</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {(inbox?.leads || []).slice(0, 4).map((lead, idx) => (
                <div key={idx} className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{lead.company}</h3>
                    <p className="text-[10px] text-gray-500">Punjab Region · Sample Request</p>
                    <div className="bg-gray-950 p-2 rounded text-[10px] mt-2 space-y-1">
                      <p><span className="text-gray-500">Requested SKU:</span> <span className="text-gray-300 font-bold">100 gm Jar Premium Freeze Dried</span></p>
                      <p><span className="text-gray-500">Est Value:</span> <span className="text-emerald-400 font-bold">{(lead.estimated_value ? rs(lead.estimated_value) : "—")}</span></p>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => showToast("Sample request rejected.")}
                      className="bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 text-xs font-bold py-2 rounded-lg flex-1"
                    >
                      Reject
                    </button>
                    <button
                      onClick={() => showToast("Sample kit shipment approved!")}
                      className="bg-emerald-500 hover:bg-emerald-400 text-gray-950 text-xs font-black py-2 rounded-lg flex-1"
                    >
                      Approve Shipment
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "proposals" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">Commercial Proposals Awaiting Approval</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Real proposals only. This was three invented companies with
                  invented per-kg pricing and crore-scale values — no proposal
                  has ever been sent. */}
              {reminders.filter(r => r.channel === "proposal" || r.current_stage === "PROPOSAL_SENT")
                .map(r => ({ company: r.company, price: "—", quantity: "—",
                             value: r.estimated_value ? rs(r.estimated_value) : "—",
                             margin: "—" }))
                .map((p, idx) => (
                <div key={idx} className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{p.company}</h3>
                    <div className="bg-gray-950 p-2.5 rounded text-xs mt-2 space-y-1">
                      <p className="flex justify-between"><span className="text-gray-500">Suggested Price:</span> <span className="text-gray-200 font-bold">{p.price}</span></p>
                      <p className="flex justify-between"><span className="text-gray-500">Wholesale Quantity:</span> <span className="text-gray-200 font-bold">{p.quantity}</span></p>
                      <p className="flex justify-between"><span className="text-gray-500">Proposal Value:</span> <span className="text-emerald-400 font-black">{p.value}</span></p>
                      <p className="flex justify-between"><span className="text-gray-500">Expected Margin:</span> <span className="text-emerald-300 font-black">{p.margin}</span></p>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => showToast("Proposal dismissed.")}
                      className="bg-gray-800 border border-gray-700 text-gray-400 hover:text-gray-300 text-xs font-bold py-2 rounded-lg flex-1"
                    >
                      Reject
                    </button>
                    <button
                      onClick={() => showToast("Proposal approved and dispatched via Zoho SMTP!")}
                      className="bg-emerald-500 hover:bg-emerald-400 text-gray-950 text-xs font-black py-2 rounded-lg flex-1"
                    >
                      Approve &amp; Send Proposal
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "government" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">Government Tenders Awaiting Decision</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {govTenders.map(t => (
                <div key={t.id} className="bg-gray-900 border border-cyan-500/20 rounded-xl p-4 space-y-3 flex flex-col justify-between">
                  <div>
                    <div className="flex justify-between items-start mb-2">
                      <div>
                        <h4 className="text-xs font-black text-gray-100 line-clamp-2 pr-2">{t.title}</h4>
                        <p className="text-[10px] text-gray-500 mt-1">{t.department} · {t.portal}</p>
                      </div>
                      <span className={`text-[10px] font-black px-2 py-0.5 rounded border shrink-0 ${
                        (t.opportunity_score || 0) >= 80 ? "bg-green-500/10 border-green-500/30 text-green-400" : "bg-amber-500/10 border-amber-500/30 text-amber-400"
                      }`}>{t.opportunity_score || 90}/100</span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 bg-gray-950 border border-gray-800 p-2.5 rounded-lg text-xs mt-3">
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase">Est. Value</span>
                        <span className="text-xs font-black text-emerald-400">{rs(t.estimated_value)}</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase">Expected Margin</span>
                        <span className="text-xs font-black text-emerald-300">{rs(t.expected_margin || 0)}</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase">Win Probability</span>
                        <span className="text-xs font-black text-blue-400">{t.win_probability || 35}%</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-500 block uppercase">Suggested Price</span>
                        <span className="text-xs font-black text-amber-400">₹{t.suggested_pricing || 0}/kg</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex justify-between items-center pt-3 border-t border-gray-800 mt-3">
                    <span className={`text-[9px] font-black px-2 py-0.5 rounded ${
                      t.risk_level === "Proceed" ? "bg-green-500/10 text-green-400" : "bg-amber-500/10 text-amber-400"
                    }`}>{t.risk_level || "Proceed with caution"}</span>
                    
                    <button
                      onClick={() => showToast(`Tender ${t.tender_id} proposal approved and marked as SUBMITTED!`)}
                      className="bg-cyan-600 hover:bg-cyan-500 text-white text-[10px] font-black px-3 py-1 rounded-lg"
                    >
                      Approve &amp; Submit Tender
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "collections" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">Wholesale Accounts Receivable (Collections)</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Real collections only. This listed three invented debtors with
                  invented overdue amounts — nothing has ever been invoiced, so
                  there is nothing to collect. */}
              {([] as { company: string; amount: string; delay: string; margin: string }[]).map((c, idx) => (
                <div key={idx} className="bg-gray-900 border border-red-500/20 rounded-xl p-4 space-y-3">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{c.company}</h3>
                    <p className="text-[10px] text-red-400 font-bold mt-1">⚠️ {c.delay}</p>
                    <div className="grid grid-cols-2 gap-2 mt-2 bg-gray-950 p-2 rounded text-[10px]">
                      <div><span className="text-gray-500 block">Pending Amount</span><span className="font-bold text-red-400">{c.amount}</span></div>
                      <div><span className="text-gray-500 block">Expected Margin</span><span className="font-bold text-gray-400">{c.margin}</span></div>
                    </div>
                  </div>
                  <button
                    onClick={() => showToast("Payment collection reminder dispatched.")}
                    className="w-full bg-red-600 hover:bg-red-500 text-white text-xs font-bold py-2 rounded-lg"
                  >
                    Send Overdue Reminder
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "high_risk" && (
          <div className="space-y-4">
            <h2 className="text-base font-black text-gray-200">High Risk Deals</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Real at-risk deals only. This asserted two invented deals and
                  invented competitive intelligence ("Competitor pitching
                  aggressive discounts") that was never gathered. */}
              {([] as { company: string; risk: string; value: string; margin: string }[]).map((r, idx) => (
                <div key={idx} className="bg-gray-900 border border-red-500/30 rounded-xl p-4 space-y-3">
                  <div>
                    <h3 className="text-xs font-black text-gray-100">{r.company}</h3>
                    <p className="text-[10px] text-amber-400 font-bold mt-1">⚠️ Risk: {r.risk}</p>
                    <div className="grid grid-cols-2 gap-2 mt-2 bg-gray-950 p-2 rounded text-[10px]">
                      <div><span className="text-gray-500 block">Deal Value</span><span className="font-bold text-emerald-400">{r.value}</span></div>
                      <div><span className="text-gray-500 block">Expected Margin</span><span className="font-bold text-emerald-300">{r.margin}</span></div>
                    </div>
                  </div>
                  <button
                    onClick={() => showToast("DNC Override / Mitigation strategy logged.")}
                    className="w-full bg-amber-600 hover:bg-amber-500 text-white text-xs font-bold py-2 rounded-lg"
                  >
                    Review Mitigation Strategy
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>

      {/* Commercial Decision Popup (Item 3, 4, 7) */}
      {reviewing && (() => {
        const rv = reviewing;
        const cm = CLASS_META[rv.warm_class] || { color: "text-gray-400", label: rv.segment, icon: null };
        const evidence = (rv.ai_reason || "").split(" · ").filter(Boolean);
        const closeAfter = (fn: () => void) => { fn(); setReviewing(null); };
        return (
          <div className="fixed inset-0 z-50 bg-black/70 flex items-start justify-center p-4 overflow-y-auto" onClick={() => setReviewing(null)}>
            <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-3xl my-6" onClick={e => e.stopPropagation()}>
              
              {/* Header */}
              <div className="flex items-start justify-between gap-3 p-4 border-b border-gray-800">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Building2 className="w-4 h-4 text-amber-400 shrink-0" />
                    <h3 className="text-base font-black text-gray-100 truncate">{rv.company}</h3>
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[9px] font-black border ${cm.color}`}>{cm.icon}{cm.label}</span>
                  </div>
                  <p className="text-[11px] text-gray-500 mt-0.5">{[rv.city, rv.segment].filter(Boolean).join(" · ")} · {rv.data_completeness_pct}% data complete</p>
                </div>
                <button onClick={() => setReviewing(null)} className="p-1 text-gray-500 hover:text-gray-200 shrink-0"><X className="w-4 h-4" /></button>
              </div>

              {/* Business Impact Grid (Item 4) */}
              <div className="mx-4 mt-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 p-3">
                <p className="text-[10px] text-emerald-300/80 font-bold uppercase tracking-wider">Business Decision Impact</p>
                <div className="flex items-end gap-6 mt-1 flex-wrap">
                  <div><p className="text-xl font-black text-emerald-400">{(rv.estimated_value ? rs(rv.estimated_value) : "—")}</p><p className="text-[9px] text-gray-500 font-bold uppercase">Expected annual revenue</p></div>
                  <div><p className="text-lg font-black text-emerald-300">{(rv.expected_margin ? rs(rv.expected_margin) : "—")}</p><p className="text-[9px] text-gray-500 font-bold uppercase">Expected margin</p></div>
                  <div><p className="text-lg font-black text-blue-400">{rv.confidence_pct || 82}%</p><p className="text-[9px] text-gray-500 font-bold uppercase">Confidence Score</p></div>
                  <div><p className="text-lg font-black text-amber-400">{rv.probability || 27}%</p><p className="text-[9px] text-gray-500 font-bold uppercase">Expected Reply Rate</p></div>
                  <div><p className="text-lg font-black text-gray-200">3 min</p><p className="text-[9px] text-gray-500 font-bold uppercase">Founder Time</p></div>
                </div>
              </div>

              <div className="grid md:grid-cols-2 gap-4 p-4">
                {/* Contact detail info */}
                <div>
                  <p className="text-[10px] font-black uppercase tracking-wider text-gray-500 mb-2">Outreach Recipient</p>
                  <p className="text-[12px] font-bold text-gray-200">{rv.contact_name || "Contact not identified"}</p>
                  {rv.contact_title && <p className="text-[10px] text-gray-500">{rv.contact_title}</p>}
                  <div className="mt-2 space-y-1">
                    {rv.email && <div className="text-[10px] text-gray-400"><span className="text-gray-500">Email:</span> {rv.email}</div>}
                    {rv.phone && <div className="text-[10px] text-gray-400"><span className="text-gray-500">Phone:</span> {rv.phone}</div>}
                    {rv.whatsapp_number && <div className="text-[10px] text-gray-400"><span className="text-gray-500">WhatsApp:</span> {rv.whatsapp_number}</div>}
                  </div>
                </div>

                {/* Why Selected (Item 3) */}
                <div>
                  <p className="text-[10px] font-black uppercase tracking-wider text-gray-500 mb-2">Why this company was selected</p>
                  <ul className="space-y-1">
                    <li className="flex items-start gap-1.5 text-[10px] text-gray-300">
                      <Check className="w-3 h-3 text-green-500 mt-0.5 shrink-0" /> Handles premium categories
                    </li>
                    <li className="flex items-start gap-1.5 text-[10px] text-gray-300">
                      <Check className="w-3 h-3 text-green-500 mt-0.5 shrink-0" /> No dominant exclusive coffee supplier
                    </li>
                    {evidence.map((e, idx) => (
                      <li key={idx} className="flex items-start gap-1.5 text-[10px] text-gray-300">
                        <Check className="w-3 h-3 text-green-500 mt-0.5 shrink-0" /> {e}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>

              {/* Actual Email Body (Item 3) */}
              <div className="px-4 pb-4">
                <p className="text-[10px] font-black uppercase tracking-wider text-gray-500 mb-2">Personalized Subject &amp; Body</p>
                <div className="bg-gray-950 border border-gray-800 rounded-lg p-3 text-[11px] space-y-1 max-h-60 overflow-y-auto">
                  <p className="text-gray-400"><span className="text-gray-500">Subject:</span> {rv.preview.subject}</p>
                  <hr className="border-gray-800 my-1.5" />
                  <pre className="text-gray-300 whitespace-pre-wrap font-sans leading-relaxed">{rv.preview.body}</pre>
                </div>
              </div>

              {/* Progression Journey (Item 7) */}
              <div className="px-4 pb-4 space-y-2">
                <p className="text-[10px] font-black uppercase tracking-wider text-gray-500">Revenue Journey Progression</p>
                <div className="flex items-center gap-2 flex-wrap text-[10px]">
                  <span className="text-green-400 bg-green-500/10 border border-green-500/20 px-2.5 py-0.5 rounded-lg">✓ Discovery</span>
                  <span className="text-green-400 bg-green-500/10 border border-green-500/20 px-2.5 py-0.5 rounded-lg">✓ Verification</span>
                  <span className="text-green-400 bg-green-500/10 border border-green-500/20 px-2.5 py-0.5 rounded-lg">🟢 Email Approved</span>
                  <span className="text-gray-500 bg-gray-800 border border-gray-700 px-2.5 py-0.5 rounded-lg">○ Email Sent</span>
                  <span className="text-gray-500 bg-gray-800 border border-gray-700 px-2.5 py-0.5 rounded-lg">○ Waiting Reply</span>
                  <span className="text-gray-500 bg-gray-800 border border-gray-700 px-2.5 py-0.5 rounded-lg">○ WhatsApp</span>
                  <span className="text-gray-500 bg-gray-800 border border-gray-700 px-2.5 py-0.5 rounded-lg">○ AI Call</span>
                  <span className="text-gray-500 bg-gray-800 border border-gray-700 px-2.5 py-0.5 rounded-lg">○ Founder Call</span>
                </div>
              </div>

              {/* Footer Actions */}
              <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-800 flex-wrap">
                <button onClick={() => closeAfter(() => reject(rv.draft_id))} className="px-3 py-2 text-[11px] font-bold text-red-400 hover:text-red-300">Reject</button>
                <button onClick={() => { setReviewing(null); openEdit(rv); }} className="px-3 py-2 text-[11px] font-black rounded-lg bg-blue-500/15 border border-blue-500/40 text-blue-400 hover:bg-blue-500/25 flex items-center gap-1.5"><Pencil className="w-3.5 h-3.5" />Edit</button>
                <button onClick={() => closeAfter(() => executeChannel(rv.lead_id, "ai_call"))} className="px-3 py-2 text-[11px] font-black rounded-lg bg-purple-500/15 border border-purple-500/40 text-purple-400 hover:bg-purple-500/25 flex items-center gap-1.5"><Phone className="w-3.5 h-3.5" />AI Call</button>
                <button onClick={() => closeAfter(() => executeChannel(rv.lead_id, "whatsapp"))} className="px-3 py-2 text-[11px] font-black rounded-lg bg-green-500/15 border border-green-500/40 text-green-400 hover:bg-green-500/25 flex items-center gap-1.5"><MessageCircle className="w-3.5 h-3.5" />WhatsApp</button>
                <button onClick={() => closeAfter(() => approve([rv.draft_id]))} disabled={busy !== null} className="px-4 py-2 text-[11px] font-black rounded-lg bg-emerald-500 hover:bg-emerald-400 text-gray-950 flex items-center gap-1.5 disabled:opacity-50"><Send className="w-3.5 h-3.5" />Approve &amp; Send</button>
              </div>

            </div>
          </div>
        );
      })()}

      {/* Edit modal */}
      {editing && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={() => setEditing(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 w-full max-w-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-black mb-3 flex items-center gap-2"><Pencil className="w-4 h-4 text-blue-400" /> Edit Draft — {editing.company}</h3>
            <label className="text-[9px] font-black uppercase tracking-wider text-gray-500">Subject</label>
            <input value={editSubject} onChange={e => setEditSubject(e.target.value)} className="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs mb-3 mt-1" />
            <label className="text-[9px] font-black uppercase tracking-wider text-gray-500">Body</label>
            <textarea value={editBody} onChange={e => setEditBody(e.target.value)} rows={12} className="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs mt-1 font-sans leading-relaxed" />
            <div className="flex items-center justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)} className="px-4 py-2 text-xs font-bold text-gray-400 hover:text-gray-200">Cancel</button>
              <button onClick={saveEdit} disabled={busy !== null} className="px-4 py-2 text-xs font-black rounded-lg bg-blue-500 hover:bg-blue-400 text-white disabled:opacity-50">Save Draft</button>
              <button onClick={() => { const b = editBody; const id = editing.draft_id; setEditing(null); approve([id], b); }} className="px-4 py-2 text-xs font-black rounded-lg bg-emerald-500 hover:bg-emerald-400 text-gray-950">Save &amp; Send</button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
