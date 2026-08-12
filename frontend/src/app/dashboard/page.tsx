"use client";
import React, { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import ApprovalCenterSection from "../../components/ApprovalCenterSection";
import {
  TrendingUp, AlertTriangle, Target, Zap,
  Bot, FileText, RefreshCw, Coffee, ShieldAlert,
  Phone, Play, Shield, BarChart2, DollarSign,
  CheckSquare, Plus, Flame, CheckCircle2, ChevronRight,
  TrendingDown, Info, ShieldCheck, Mail, Building2, ChevronDown, ChevronUp
} from "lucide-react";

// -- Types ----------------------------------------------------

interface B2BLead {
  id: number;
  company: string;
  division: string;
  city: string;
  status: string;
  score: number;
  estimated_value: number;
  contact_name: string;
  contact_title: string;
  email: string;
  phone: string;
  lead_source: string;
  contact_persona?: string;
  qualification_notes: string;
  probability?: number;
  sample_sku?: string;
  recommended_action?: string;
  proposal_suggested_margin?: number;
  proposal_suggested_price?: number;
  proposal_discount_percent?: number;
  proposal_text?: string;
  expected_monthly_consumption_kg?: number;
  email_sequence_stage?: number;
  email_sequence_last_sent?: string;
  do_not_call?: boolean;
  call_attempts?: number;
  call_status?: string;
  call_quality_score?: number;
  call_summary?: string;
  call_recording_url?: string;
  human_answered?: boolean;
  sample_requested?: boolean;
  meeting_requested?: boolean;
  lead_temperature_score?: number;
  lead_temperature_tier?: string;
  current_brand?: string;
  price_per_kg?: number;
  address?: string;
}

interface B2BKpis {
  pipeline_value_inr: number;
  expected_revenue_inr: number;
  expected_margin_inr: number;
  meetings_booked: number;
  samples_sent: number;
  orders_won: number;
  leads_discovered: number;
  revenue_velocity_inr: number;
  revenue_today_inr: number;
  revenue_this_week_inr: number;
  revenue_this_month_inr: number;
  revenue_by_source: Record<string, number>;
  revenue_at_risk_inr: number;
  forecasts: Record<string, { revenue: number; margin: number; cash_collection: number }>;
  leakage: {
    total_revenue_leaking: number;
    total_margin_leaking: number;
    categories: Record<string, { count: number; revenue: number; margin: number; label: string }>;
  };
  cash_this_week?: { total: number; leads: Array<{ company: string; value: number }> };
  channel_profitability?: Record<string, { won_revenue: number; true_net_margin: number; founder_hours: number; roi_per_hour: number }>;
  sku_roi?: Record<string, { sent: number; won: number; conversion_rate: number; revenue: number; roi_multiplier: number }>;
}

interface DecisionEngineData {
  revenue_waiting: number;
  founder_hours: number;
  top_decisions: Array<{
    opportunity_id: number;
    company_name: string;
    recommended_action: string;
    expected_margin: number;
    success_probability: number;
    strategic_weight: number;
    founder_time_hours: number;
    urgency: number;
    score: number;
    stage: string;
    evidence: string[];
  }>;
  pending_approvals_count: number;
  blocked_opportunities_count: number;
}

interface CompletedEvent {
  event_type: string;
  actor: string;
  channel: string;
  before_status: string;
  after_status: string;
  payload: { company?: string };
  created_at: string;
}

const EMPTY_KPIS: B2BKpis = {
  pipeline_value_inr: 0,
  expected_revenue_inr: 0,
  expected_margin_inr: 0,
  meetings_booked: 0,
  samples_sent: 0,
  orders_won: 0,
  leads_discovered: 0,
  revenue_velocity_inr: 0,
  revenue_today_inr: 0,
  revenue_this_week_inr: 0,
  revenue_this_month_inr: 0,
  revenue_by_source: {},
  revenue_at_risk_inr: 0,
  forecasts: {},
  leakage: { total_revenue_leaking: 0, total_margin_leaking: 0, categories: {} },
  cash_this_week: { total: 0, leads: [] },
  channel_profitability: {},
  sku_roi: {}
};

const fmt = (n: number | string | null | undefined) => {
  if (typeof n === "string") return n;
  const v = n ?? 0;
  return v >= 10_000_000 ? `₹${(v / 10_000_000).toFixed(1)} Cr`
       : v >= 100_000    ? `₹${(v / 100_000).toFixed(1)} L`
       : `₹${v.toLocaleString("en-IN")}`;
};

function formatActionDescription(action: string | null | undefined): string {
  if (!action) return "Initiate founder follow-up call.";
  let text = action.trim();
  if (text.toUpperCase() === text) {
    text = text.toLowerCase();
    text = text.charAt(0).toUpperCase() + text.slice(1);
  }
  return text;
}

function getActionTabLink(action: string | null | undefined): string {
  if (!action) return "#approval-center";
  const act = action.toLowerCase();
  if (act.includes("tender") || act.includes("gem")) return "/government";
  return "#approval-center";
}

interface ActiveUpgrade {
  tool_name: string;
  cost: number;
  roi: number;
}

interface AgentPnlItem {
  name: string;
  revenue_influenced: number;
  commission_earned: number;
  commission_spent: number;
  net_balance: number;
  active_upgrades: ActiveUpgrade[];
}

interface AgentToolUpgradeItem {
  id: number;
  agent_key: string;
  tool_name: string;
  upgrade_cost: number;
  projected_uplift: number;
  roi_multiplier: number;
  status: string;
  description: string;
  approved_at: string | null;
}

export default function Dashboard() {
  const [b2bLeads, setB2bLeads] = useState<B2BLead[]>([]);
  const [kpis, setKpis] = useState<B2BKpis>(EMPTY_KPIS);
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reconnecting, setReconnecting] = useState(false);
  const [decisionEngine, setDecisionEngine] = useState<DecisionEngineData | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" | "info" } | null>(null);
  const [selfLearningSyncing, setSelfLearningSyncing] = useState(false);
  // Server-side dashboard memory + learned patterns. Served from the DB, so it
  // is identical whenever/wherever the dashboard is opened.
  const [memory, setMemory] = useState<{
    status: string;
    total_founder_actions: number;
    confident_patterns: number;
    min_sample_for_confidence: number;
    last_learned_at: string | null;
  } | null>(null);
  
  const showToast = (msg: string, type: "ok" | "err" | "info" = "info") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };
  // Starts null, not `new Date()`. Initialising with a clock reading makes the
  // server and the browser render different text — the server produced 19:31
  // (UTC, 24h) while the browser produced 07:30 pm (IST, 12h), which is what
  // broke hydration on the approval centre. There is no correct value for
  // "now" during SSR, so we render nothing until the client mounts.
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  // Clean UI states
  const [queues, setQueues] = useState<Record<string, { label?: string; count?: number }>>({});
  interface ApprovalInboxDraft {
    lead_id: number;
    draft_id: number;
    preview?: {
      subject?: string;
      body?: string;
    };
  }

  const [approvalInbox, setApprovalInbox] = useState<{ leads?: ApprovalInboxDraft[]; total?: number } | null>(null);
  const [completedEvents, setCompletedEvents] = useState<CompletedEvent[]>([]);
  interface TreasuryData {
    realised_revenue: number;
    realised_margin: number | string;
    ai_reinvestment_reserve: number | string;
    current_software_cost: number;
    available_for_reinvestment: number | string;
    reinvestment_rate: number;
    reinvestment_enabled: boolean;
    bootstrap_mode: boolean;
  }

  interface AgentPerformanceItem {
    agent_id: string;
    agent_name: string;
    objective: string;
    actions_completed: number;
    opportunities_influenced: number;
    qualified_replies: number;
    meetings_generated: number;
    samples_generated: number;
    proposals_generated: number;
    orders_influenced: number;
    realised_revenue: number;
    realised_margin: number;
    founder_minutes_used: number;
    cost: number;
    roi: number;
    attribution_confidence: string;
  }

  interface SubscriptionProposalItem {
    id: number;
    tool_name: string;
    agent_id: string;
    bottleneck_removed: string;
    monthly_cost: number;
    projected_uplift: number;
    payback_period_months: number;
    confidence: string;
    justification_status: string;
    status: string;
    approved_at: string | null;
  }

  const [agentPnl, setAgentPnl] = useState<Record<string, AgentPnlItem> | null>(null);
  const [agentUpgrades, setAgentUpgrades] = useState<SubscriptionProposalItem[]>([]);
  const [treasury, setTreasury] = useState<TreasuryData | null>(null);
  const [agents, setAgents] = useState<AgentPerformanceItem[]>([]);
  const [currentTab, setCurrentTab] = useState<"founder" | "discovery" | "analytics">("founder");

  const [selectedOpportunity, setSelectedOpportunity] = useState<B2BLead | null>(null);
  const [opportunityNotes, setOpportunityNotes] = useState<string>("");
  const [savingNotes, setSavingNotes] = useState(false);


  const handleSaveNotes = async (oppId: number) => {
    setSavingNotes(true);
    try {
      const res = await fetch(`/api/v1/b2b/leads/${oppId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: opportunityNotes })
      });
      if (res.ok) {
        showToast("Notes saved successfully", "ok");
        setB2bLeads(prev => prev.map(l => l.id === oppId ? { ...l, qualification_notes: opportunityNotes } : l));
        if (selectedOpportunity && selectedOpportunity.id === oppId) {
          setSelectedOpportunity({ ...selectedOpportunity, qualification_notes: opportunityNotes });
        }
      } else {
        showToast("Failed to save notes", "err");
      }
    } catch {
      showToast("Network error saving notes", "err");
    } finally {
      setSavingNotes(false);
    }
  };

  const handleSaveTreasurySettings = async (rate: number, enabled: boolean, bootstrap: boolean) => {
    try {
      const res = await fetch("/api/v1/b2b/agent-funding/treasury/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reinvestment_rate: rate,
          reinvestment_enabled: enabled,
          bootstrap_mode: bootstrap
        })
      });
      if (res.ok) {
        showToast("Treasury settings updated successfully.", "ok");
        fetchData(true);
      } else {
        showToast("Failed to update settings.", "err");
      }
    } catch {
      showToast("Network error updating settings.", "err");
    }
  };

  const handleApproveProposal = async (proposalId: number, nextStatus: string) => {
    try {
      const res = await fetch(`/api/v1/b2b/agent-funding/proposals/${proposalId}/action?status=${nextStatus}`, {
        method: "POST"
      });
      if (res.ok) {
        showToast(`Proposal updated to ${nextStatus}`, "ok");
        fetchData(true);
      } else {
        showToast("Failed to update proposal.", "err");
      }
    } catch {
      showToast("Network error updating proposal.", "err");
    }
  };

  const fetchData = (silent = false) => {
    if (!silent) setLoading(true);
    window.dispatchEvent(new Event("dashboard:refresh"));

    fetch("/api/v1/b2b/leads")
      .then(r => r.json())
      .then(json => { setB2bLeads(json.leads || []); })
      .catch(() => { setB2bLeads([]); });

    // Dashboard memory + learned patterns (server-side => same on every device)
    fetch("/api/v1/dashboard/memory")
      .then(r => (r.ok ? r.json() : null))
      .then(json => { if (json) setMemory(json); })
      .catch(() => {});

    fetch("/api/v1/b2b/kpis")
      .then(r => r.json())
      .then(json => { setKpis(json); setLive(true); setReconnecting(false); setLastRefresh(new Date()); })
      // Don't flip the sidebar to "Offline" here: GlobalHeader's ping is the
      // authoritative connection signal (see the purity:connection listener).
      // A transient failure of this one fetch used to contradict the header.
      .catch(() => { setKpis(EMPTY_KPIS); })
      .finally(() => { if (!silent) setLoading(false); });

    fetch("/api/v1/workflow/queues")
      .then(r => r.ok ? r.json() : null)
      .then(json => { if (json?.queues) setQueues(json.queues); })
      .catch(() => {});

    fetch("/api/v1/b2b/email/approval-inbox")
      .then(r => r.ok ? r.json() : null)
      .then(json => { setApprovalInbox(json); })
      .catch(() => {});

    fetch("/api/v1/workflow/events")
      .then(r => r.ok ? r.json() : [])
      .then(json => { setCompletedEvents(json || []); })
      .catch(() => {});

    fetch("/api/v1/dashboard/decision-engine")
      .then(r => r.ok ? r.json() : null)
      .then(json => { if (json) setDecisionEngine(json); })
      .catch(() => {});

    fetch("/api/v1/b2b/agent-funding/pnl")
      .then(r => r.ok ? r.json() : null)
      .then(json => { if (json) setAgentPnl(json); })
      .catch(() => {});

    fetch("/api/v1/b2b/agent-funding/treasury")
      .then(r => r.ok ? r.json() : null)
      .then(json => { if (json) setTreasury(json); })
      .catch(() => {});

    fetch("/api/v1/b2b/agent-funding/agents")
      .then(r => r.ok ? r.json() : [])
      .then(json => { if (json) setAgents(json); })
      .catch(() => {});

    fetch("/api/v1/b2b/agent-funding/proposals")
      .then(r => r.ok ? r.json() : [])
      .then(json => { setAgentUpgrades(json || []); })
      .catch(() => {});
  };

  const triggerSelfLearning = async () => {
    setSelfLearningSyncing(true);
    try {
      // This used to run on EVERY dashboard mount and cost ~139s of heavy work
      // (sync-zoho 13s + score-leads 88s + actions/recalculate 35s), saturating
      // the API's connection pool so the dashboard could not even render itself.
      // It is now an explicit, on-demand action — the periodic worker already
      // re-learns from real outcomes every 10 minutes.
      //
      // NOTE: /b2b/outreach/sequence-advance is intentionally NOT called — it
      // fabricated opens, clicks and ~25% random "REPLIED" flips with invented
      // customer quotes. It is now disabled (410).
      await fetch("/api/v1/b2b/email/sync-zoho");            // real replies (Zoho IMAP)
      await fetch("/api/v1/dashboard/memory/learn", { method: "POST" });  // cheap (~3s)

      showToast("🧠 Memory synced from real outcomes.", "ok");
      fetchData(true);
    } catch (err) {
      console.error("Dashboard self-learning sync failed", err);
    } finally {
      setSelfLearningSyncing(false);
    }
  };

  useEffect(() => {
    // Render the dashboard FIRST. The sync is deferred so a cold load never
    // waits on it, and the 10-min worker re-learns regardless.
    const t = setTimeout(() => triggerSelfLearning(), 4000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handle hash changes and smooth scrolling on load/hash change
  useEffect(() => {
    const handleHashScroll = () => {
      const hash = window.location.hash;
      if (hash) {
        const id = hash.replace("#", "");
        const element = document.getElementById(id);
        if (element) {
          setTimeout(() => {
            element.scrollIntoView({ behavior: "smooth" });
          }, 150);
        }
      }
    };

    // Run once on mount
    handleHashScroll();

    // Listen to hashchange events
    window.addEventListener("hashchange", handleHashScroll);
    return () => {
      window.removeEventListener("hashchange", handleHashScroll);
    };
  }, []);

  // Follow GlobalHeader's authoritative connection ping so the sidebar can
  // never say "Offline" while the header says "Live" (they used to keep two
  // independent `live` states on different polling intervals).
  useEffect(() => {
    const onConnection = (e: Event) => {
      const detail = (e as CustomEvent<{ live: boolean }>).detail;
      if (detail && typeof detail.live === "boolean") setLive(detail.live);
    };
    window.addEventListener("purity:connection", onConnection);
    return () => window.removeEventListener("purity:connection", onConnection);
  }, []);

  useEffect(() => {
    fetchData();
    const t = setInterval(() => {
      fetchData(true);
    }, live ? 180000 : 45000);   // was 60s/8s — each tick fires 7 heavy endpoints in parallel
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  // Top 10 Revenue Opportunities sorted by Expected Margin
  const moneyTodayOpportunities = useMemo(() => {
    return b2bLeads
      .filter(l => l.status !== "COLD" && l.status !== "DORMANT" && l.status !== "ARCHIVED" && l.status !== "ORDER_WON")
      .sort((a, b) => {
        const marginA = a.estimated_value * (a.proposal_suggested_margin || 35.0) / 100;
        const marginB = b.estimated_value * (b.proposal_suggested_margin || 35.0) / 100;
        return marginB - marginA;
      })
      .slice(0, 10);
  }, [b2bLeads]);

  // Derived Waiting On list
  const waitingOnLeads = useMemo(() => {
    return b2bLeads.filter(l => l.status === "EMAIL_SENT" || l.status === "SAMPLE_SENT" || l.status === "PROPOSAL_SENT");
  }, [b2bLeads]);

  const totalActionQueueCount = Object.values(queues).reduce((acc, q) => acc + (q.count || 0), 0) + (approvalInbox?.total || 0);

  return (
    <div className="min-h-screen bg-gray-955 text-gray-100 flex flex-col font-sans">
      {/* Toast Notification */}
      {toast && (
        <div className={`fixed bottom-6 right-6 z-[9999] flex items-center gap-3 px-4 py-3 rounded-xl shadow-2xl border text-sm font-medium transition-all animate-in slide-in-from-bottom-4 ${
          toast.type === "ok"  ? "bg-green-950 border-green-500/40 text-green-200" :
          toast.type === "err" ? "bg-red-950 border-red-500/40 text-red-200" :
                                 "bg-gray-900 border-gray-700 text-gray-200"
        }`}>
          <span>{toast.type === "ok" ? "🟢" : toast.type === "err" ? "🔴" : "🔵"}</span>
          <span>{toast.msg}</span>
          <button onClick={() => setToast(null)} className="ml-2 opacity-50 hover:opacity-100 text-xs">×</button>
        </div>
      )}

      {/* Main Console Frame: Left Sidebar + Main Content */}
      <div className="flex flex-1 min-h-screen">
        {/* Navigation Sidebar */}
        <aside className="w-56 bg-gray-900 border-r border-gray-800 p-4 space-y-6 shrink-0 flex flex-col justify-between">
          <div className="space-y-6">
            <div className="flex items-center gap-2 px-1">
              <div className="bg-white rounded-lg p-0.5 overflow-hidden w-6 h-6">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/company_logo.png" alt="Purity Beans" className="w-full h-full object-contain" />
              </div>
              <div>
                <h2 className="text-[10px] font-black text-gray-250 uppercase tracking-widest leading-none">Purity Beans</h2>
                <p className="text-[8px] text-gray-500 mt-0.5 font-bold">Revenue Console v1.2</p>
              </div>
            </div>

            <nav className="space-y-1">
              {[
                { id: "founder", label: "🏠 Founder Console", action: () => { window.scrollTo({ top: 0, behavior: 'smooth' }); } },
                { id: "approval-center", label: "📥 Approval Center", action: () => { document.getElementById("approval-center")?.scrollIntoView({ behavior: 'smooth' }); }, badge: approvalInbox?.total },
                { href: "/government", label: "🏛 Government" },
                { href: "/settings", label: "⚙ Settings" },
              ].map((item: { id?: string; label: string; action?: () => void; badge?: number; href?: string }, idx) => {
                if (item.href) {
                  return (
                    <Link
                      key={idx}
                      href={item.href}
                      className="flex items-center justify-between px-2.5 py-1.5 text-[11px] font-bold text-gray-400 hover:text-gray-150 hover:bg-gray-800/50 rounded-lg transition-all border border-transparent"
                    >
                      <span>{item.label}</span>
                      {item.badge !== undefined && item.badge > 0 && (
                        <span className="bg-orange-500 text-gray-950 text-[8px] font-black px-1 py-0.5 rounded-full shrink-0">
                          {item.badge}
                        </span>
                      )}
                    </Link>
                  );
                } else {
                  return (
                    <button
                      key={idx}
                      onClick={item.action}
                      className="w-full flex items-center justify-between px-2.5 py-1.5 text-[11px] font-bold rounded-lg transition-all text-left text-gray-400 hover:text-gray-155 hover:bg-gray-800/50 border border-transparent"
                    >
                      <span>{item.label}</span>
                      {item.badge !== undefined && item.badge > 0 && (
                        <span className="bg-orange-500 text-gray-950 text-[8px] font-black px-1 py-0.5 rounded-full shrink-0">
                          {item.badge}
                        </span>
                      )}
                    </button>
                  );
                }
              })}
            </nav>
          </div>

          {/* Sync status */}
          <div className="bg-gray-955/60 border border-gray-850 p-2.5 rounded-lg space-y-1.5 text-[9px] text-gray-500">
            <div className="flex justify-between items-center">
              <span>Connection</span>
              <span className={`font-bold flex items-center gap-1 ${live ? "text-green-400" : "text-red-400"}`}>
                <span className={`w-1 h-1 rounded-full ${live ? "bg-green-400 animate-pulse" : "bg-red-400"}`} />
                {live ? "Connected" : "Offline"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span>Sync</span>
              <span className="text-gray-400 font-mono font-bold">
                {/* Timezone pinned to IST rather than the viewer's ambient
                    locale: this is an India business and the founder reads
                    every other timestamp in the system as IST 12-hour. */}
                {lastRefresh
                  ? lastRefresh.toLocaleTimeString("en-IN", {
                      hour: "2-digit", minute: "2-digit", hour12: true,
                      timeZone: "Asia/Kolkata",
                    })
                  : "—"}
              </span>
            </div>
            <button
              onClick={() => { setReconnecting(true); fetchData(); }}
              disabled={loading}
              className="w-full mt-1.5 py-1 bg-gray-800 hover:bg-gray-705 text-gray-300 rounded font-bold uppercase tracking-wider text-[8px] transition-colors"
            >
              Sync Console
            </button>
          </div>
        </aside>

        {/* Main Content Pane */}
        <main className="flex-1 p-6 overflow-y-auto max-w-[1400px]">

          {/* Header */}
          <div className="flex justify-between items-center border-b border-gray-850 pb-4 mb-6">
            <div>
              <div className="flex items-center gap-3 font-sans">
                <h1 className="text-base font-black text-gray-100 uppercase tracking-widest">Revenue Command Console</h1>
                <span
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[8px] font-black uppercase border tracking-wider transition-all ${
                    treasury?.bootstrap_mode
                      ? "bg-orange-550/10 text-orange-400 border-orange-500/25"
                      : "bg-gray-800 text-gray-400 border-gray-700"
                  }`}
                >
                  ⚙️ Bootstrap Mode: {treasury?.bootstrap_mode ? "ON" : "OFF"}
                </span>
                <span
                  title={
                    memory
                      ? `${memory.total_founder_actions} founder actions remembered · ${memory.confident_patterns} pattern(s) with >=${memory.min_sample_for_confidence} samples${memory.last_learned_at ? ` · learned ${new Date(memory.last_learned_at).toLocaleString()}` : ""}`
                      : "Memory not loaded yet"
                  }
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[8px] font-black uppercase border tracking-wider transition-all ${
                    selfLearningSyncing
                      ? "bg-amber-550/10 text-amber-400 border-amber-500/25 animate-pulse"
                      : memory?.status === "learning"
                      ? "bg-blue-550/10 text-blue-400 border-blue-500/25"
                      : "bg-gray-800 text-gray-400 border-gray-700"
                  }`}
                >
                  🧠 {selfLearningSyncing
                    ? "Syncing memory…"
                    : !memory
                    ? "Memory loading…"
                    : memory.status === "learning"
                    ? `Learned ${memory.confident_patterns} pattern${memory.confident_patterns === 1 ? "" : "s"} from ${memory.total_founder_actions} historical actions`
                    : memory.status === "collecting_data"
                    ? `Collecting data · ${memory.total_founder_actions} historical actions`
                    : "No data yet"}
                </span>
              </div>
              <p className="text-[9px] text-gray-550 mt-0.5">Optimizing for revenue generated per founder hour</p>
            </div>
          </div>

          <div className="space-y-8">
            
            {/* AI Command Center Hero Box (V1.2 / V1.2 refined) */}
            {decisionEngine && (
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-4">
                <div className="flex justify-between items-center border-b border-gray-800 pb-2.5">
                  <div>
                    <h2 className="text-xs font-black text-gray-250 uppercase tracking-widest flex items-center gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse"></span>
                      AI Command Center
                    </h2>
                    <p className="text-[9px] text-gray-500 mt-0.5">Unified Decision Engine recommendations & status overview</p>
                  </div>
                  <span className="text-[8px] font-mono text-green-400 bg-green-500/5 px-2 py-0.5 rounded border border-green-500/10 shrink-0 uppercase tracking-wider font-bold">Decision Engine: Active</span>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                  {/* Left Column: Today's Stats */}
                  <div className="bg-gray-955 border border-gray-850 p-4 rounded-xl flex flex-col justify-between space-y-3">
                    <div>
                      <span className="text-gray-500 font-bold uppercase text-[7px] tracking-wider block font-sans">Today&apos;s Summary</span>
                      <div className="grid grid-cols-2 gap-4 mt-2">
                        <div>
                          <span className="text-[8px] text-gray-550 block uppercase font-sans">Revenue Waiting</span>
                          <span className="text-sm font-black font-mono text-green-400">{fmt(decisionEngine.revenue_waiting)}</span>
                        </div>
                        <div>
                          <span className="text-[8px] text-gray-550 block uppercase font-sans">Founder Hours</span>
                          <span className="text-sm font-black font-mono text-gray-300">{decisionEngine.founder_hours} hrs</span>
                        </div>
                      </div>
                    </div>
                    <div className="border-t border-gray-850 pt-2.5 grid grid-cols-2 gap-2 text-[9px] text-gray-400">
                      <div>
                        <span className="text-[8px] text-gray-600 uppercase block font-sans">Pending approvals</span>
                        <span className="font-bold text-orange-400 font-mono">{decisionEngine.pending_approvals_count} Requests</span>
                      </div>
                      <div>
                        <span className="text-[8px] text-gray-600 uppercase block font-sans">Blocked Opps</span>
                        <span className="font-bold text-red-400 font-mono">{decisionEngine.blocked_opportunities_count} Stalled</span>
                      </div>
                    </div>
                  </div>

                  {/* Center & Right Column: Top 3 Decisions */}
                  <div className="lg:col-span-2 space-y-3">
                    <span className="text-gray-500 font-bold uppercase text-[7px] tracking-wider block font-sans">Top 3 Recommended Decisions</span>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      {decisionEngine.top_decisions.map((dec, idx) => (
                        <div key={idx} className="bg-gray-955 border border-gray-850 p-3 rounded-lg flex flex-col justify-between space-y-2.5">
                          <div>
                            <div className="flex justify-between items-start gap-1">
                              <span className="text-[9px] font-black text-gray-150 truncate block uppercase max-w-[90px] font-sans">{dec.company_name}</span>
                              <span className="text-[8px] font-mono font-bold text-blue-400 shrink-0 uppercase">{dec.stage}</span>
                            </div>
                            <span className="text-[10px] font-black text-orange-400 block mt-1 truncate font-sans">{dec.recommended_action}</span>
                          </div>
                          <div className="space-y-1.5">
                            <div className="bg-gray-900 p-1.5 rounded text-[8px] text-gray-400 space-y-0.5 leading-snug">
                              {dec.evidence.slice(0, 2).map((ev, eIdx) => (
                                <div key={eIdx} className="truncate">· {ev}</div>
                              ))}
                            </div>
                            <div className="flex justify-between items-center text-[8px] text-gray-500 border-t border-gray-850/60 pt-1.5">
                              <span>ROI: {fmt(dec.expected_margin)}</span>
                              <span className="font-bold text-gray-400">Score: {dec.score}</span>
                            </div>
                          </div>
                        </div>
                      ))}
                      {decisionEngine.top_decisions.length === 0 && (
                        <p className="col-span-3 text-xs text-gray-500 text-center py-4 italic">No recommendations calculated today.</p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* AI Agent P&L & Self-Funding Console */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-6">
              <div className="flex flex-col md:flex-row md:justify-between md:items-center border-b border-gray-800 pb-4 gap-4">
                <div>
                  <h2 className="text-xs font-black text-gray-250 uppercase tracking-widest flex items-center gap-1.5 font-sans">
                    <span className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse"></span>
                    🧠 AI Sales Force & Growth Treasury
                  </h2>
                  <p className="text-[9px] text-gray-500 mt-0.5 font-sans">
                    Monitor contribution metrics, manage reinvestment limits, and authorize subscription proposals.
                  </p>
                </div>
                
                {/* Reinvestment settings controls */}
                {treasury && (
                  <div className="flex flex-wrap items-center gap-3.5 bg-gray-955 p-3 rounded-lg border border-gray-850">
                    {/* Bootstrap Mode Toggle */}
                    <div className="flex items-center gap-2">
                      <span className="text-[8px] font-black text-gray-400 uppercase tracking-wider font-sans">Bootstrap Mode</span>
                      <button
                        onClick={() => handleSaveTreasurySettings(treasury.reinvestment_rate, treasury.reinvestment_enabled, !treasury.bootstrap_mode)}
                        className={`px-2 py-0.5 text-[9px] font-black rounded font-sans transition-all uppercase ${
                          treasury.bootstrap_mode
                            ? "bg-orange-500/10 text-orange-400 border border-orange-500/30"
                            : "bg-gray-800 text-gray-500 border border-gray-700"
                        }`}
                      >
                        {treasury.bootstrap_mode ? "Active (ON)" : "OFF"}
                      </button>
                    </div>

                    {/* Reinvestment Enabled Toggle */}
                    <div className="flex items-center gap-2">
                      <span className="text-[8px] font-black text-gray-400 uppercase tracking-wider font-sans">Reinvestment</span>
                      <button
                        onClick={() => handleSaveTreasurySettings(treasury.reinvestment_rate, !treasury.reinvestment_enabled, treasury.bootstrap_mode)}
                        className={`px-2 py-0.5 text-[9px] font-black rounded font-sans transition-all uppercase ${
                          treasury.reinvestment_enabled
                            ? "bg-green-500/10 text-green-400 border border-green-500/30"
                            : "bg-gray-800 text-gray-500 border border-gray-700"
                        }`}
                      >
                        {treasury.reinvestment_enabled ? "Enabled" : "Disabled"}
                      </button>
                    </div>

                    {/* Reinvestment Rate Slider */}
                    <div className="flex items-center gap-2 font-sans">
                      <span className="text-[8px] font-black text-gray-400 uppercase tracking-wider">Rate</span>
                      <input
                        type="range"
                        min="0"
                        max="50"
                        step="5"
                        value={Math.round(treasury.reinvestment_rate * 100)}
                        onChange={(e) => handleSaveTreasurySettings(Number(e.target.value) / 100, treasury.reinvestment_enabled, treasury.bootstrap_mode)}
                        className="w-16 h-1 bg-gray-800 rounded-lg appearance-none cursor-pointer accent-orange-500"
                      />
                      <span className="text-[9px] font-mono text-gray-300 font-bold w-6">{Math.round(treasury.reinvestment_rate * 100)}%</span>
                    </div>
                  </div>
                )}
              </div>

              {/* AI Sales Force Scoreboard */}
              <div className="space-y-3">
                <h3 className="text-[9px] font-black text-gray-400 uppercase tracking-wider font-sans">
                  AI Sales Force Performance Scoreboard
                </h3>
                <div className="overflow-x-auto border border-gray-850 rounded-xl">
                  <table className="w-full text-left border-collapse text-[10px] font-sans">
                    <thead>
                      <tr className="border-b border-gray-850 bg-gray-955/50 text-gray-500 uppercase text-[8px] tracking-wider font-black">
                        <th className="py-2.5 px-4">Agent Name</th>
                        <th className="py-2.5 px-4">Objective</th>
                        <th className="py-2.5 px-4 text-right">Attributed Contribution</th>
                        <th className="py-2.5 px-4 text-center">Cost</th>
                        <th className="py-2.5 px-4 text-center">ROI</th>
                        <th className="py-2.5 px-4 text-right">Funnel Stats</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-850/60 bg-gray-900/20">
                      {agents.map((a) => (
                        <tr key={a.agent_id} className="hover:bg-gray-850/20 transition-colors">
                          <td className="py-2.5 px-4 font-bold text-gray-250">{a.agent_name}</td>
                          <td className="py-2.5 px-4 text-gray-500 max-w-xs truncate" title={a.objective}>{a.objective}</td>
                          <td className="py-2.5 px-4 text-right font-mono font-bold text-green-400">
                            {fmt(a.realised_margin)}
                          </td>
                          <td className="py-2.5 px-4 text-center font-mono text-gray-500">{fmt(a.cost)}</td>
                          <td className="py-2.5 px-4 text-center font-mono font-bold text-blue-400">{a.roi > 0 ? `${a.roi.toFixed(1)}x` : "—"}</td>
                          <td className="py-2.5 px-4 text-right font-mono text-[9px] text-gray-400">
                            {a.agent_id === "discovery" && `${a.actions_completed} found · ${a.opportunities_influenced} qualified`}
                            {a.agent_id === "email_sales" && `${a.actions_completed} drafts · ${a.qualified_replies} replies`}
                            {a.agent_id === "followup" && `${a.actions_completed} checks`}
                            {a.agent_id === "founder_call_coach" && `${a.meetings_generated} calls booked`}
                            {a.agent_id === "sample_conversion" && `${a.samples_generated} samples`}
                            {a.agent_id === "proposal" && `${a.proposals_generated} proposals`}
                            {a.agent_id === "closing" && `${a.orders_influenced} won`}
                            {!["discovery", "email_sales", "followup", "founder_call_coach", "sample_conversion", "proposal", "closing"].includes(a.agent_id) && "Active"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Subscription Proposals Investment proposals */}
              <div className="space-y-3 pt-2">
                <div className="flex justify-between items-center">
                  <h3 className="text-[9px] font-black text-gray-400 uppercase tracking-wider font-sans">
                    Subscription Investment Proposals (ROI Gate)
                  </h3>
                  {treasury && (
                    <span className="text-[9px] font-mono text-gray-500">
                      Reserve: <span className="font-bold text-orange-400">{fmt(treasury.ai_reinvestment_reserve)}</span> | Active Software Cost: <span className="font-bold text-red-400">{fmt(treasury.current_software_cost)}/mo</span>
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {agentUpgrades.map((upg) => {
                    const isProposed = upg.status === "PROPOSED";
                    const isApproved = upg.status === "APPROVED";
                    const isRejected = upg.status === "REJECTED";
                    const isActive = upg.status === "ACTIVE";

                    return (
                      <div
                        key={upg.id}
                        className={`bg-gray-955 border rounded-xl p-4 flex flex-col justify-between space-y-3.5 transition-all ${
                          isActive
                            ? "border-green-500/20 opacity-80"
                            : isApproved
                            ? "border-orange-500/40"
                            : "border-gray-850 opacity-90"
                        }`}
                      >
                        <div>
                          <div className="flex justify-between items-start gap-1.5 font-sans">
                            <div>
                              <span className="text-[10px] font-black text-gray-250 block">
                                {upg.tool_name}
                              </span>
                              <span className="text-[8px] text-gray-500 uppercase block tracking-wider mt-0.5">
                                Target Agent: {upg.agent_id.replace(/_/g, " ")}
                              </span>
                            </div>
                            <span
                              className={`text-[8px] font-mono font-bold px-1.5 py-0.5 rounded border shrink-0 uppercase tracking-wider ${
                                upg.justification_status === "FUNDABLE FROM AI RESERVE"
                                  ? "text-green-400 bg-green-500/5 border-green-500/10"
                                  : upg.justification_status === "FOUNDER REVIEW"
                                  ? "text-orange-400 bg-orange-500/5 border-orange-500/15"
                                  : upg.justification_status === "WATCH"
                                  ? "text-blue-400 bg-blue-500/5 border-blue-500/15"
                                  : "text-gray-500 bg-gray-900 border-gray-805"
                              }`}
                              title="ROI justification gate assessment"
                            >
                              {upg.justification_status}
                            </span>
                          </div>
                          <p className="text-[9px] text-gray-400 mt-2.5 leading-relaxed font-sans">
                            <span className="font-bold text-gray-300">Bottleneck:</span> {upg.bottleneck_removed}
                          </p>
                        </div>

                        <div className="border-t border-gray-850/60 pt-3 space-y-3">
                          <div className="grid grid-cols-4 gap-1 text-[8px] font-mono text-center">
                            <div className="bg-gray-900/60 p-1 rounded">
                              <span className="text-gray-650 block">COST</span>
                              <span className="text-gray-300 font-bold">₹{upg.monthly_cost}/mo</span>
                            </div>
                            <div className="bg-gray-900/60 p-1 rounded">
                              <span className="text-gray-650 block">EST. UPLIFT</span>
                              <span className="text-green-400 font-bold">₹{upg.projected_uplift}/mo</span>
                            </div>
                            <div className="bg-gray-900/60 p-1 rounded">
                              <span className="text-gray-650 block">PAYBACK</span>
                              <span className="text-blue-400 font-bold">{upg.payback_period_months}mo</span>
                            </div>
                            <div className="bg-gray-900/60 p-1 rounded">
                              <span className="text-gray-650 block">CONFIDENCE</span>
                              <span className="text-gray-300 font-bold">{upg.confidence}</span>
                            </div>
                          </div>

                          {/* Subscription Control actions */}
                          <div className="flex flex-wrap gap-2 justify-end pt-1 font-sans">
                            {isProposed && (
                              <>
                                <button
                                  onClick={() => handleApproveProposal(upg.id, "APPROVED")}
                                  className="px-2.5 py-1 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded shadow-sm transition-all"
                                >
                                  Approve Budget
                                </button>
                                <button
                                  onClick={() => handleApproveProposal(upg.id, "REJECTED")}
                                  className="px-2.5 py-1 bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/25 text-[9px] font-black uppercase rounded shadow-sm transition-all"
                                >
                                  Reject
                                </button>
                              </>
                            )}

                            {isApproved && (
                              <div className="w-full mt-2 bg-gray-955 p-3 rounded-lg border border-gray-800 space-y-2 text-[9px] text-left">
                                <div className="flex justify-between items-center font-sans">
                                  <span className="text-orange-400 font-black uppercase tracking-wider">⚠️ PURCHASE REQUIRED</span>
                                  <button
                                    onClick={() => handleApproveProposal(upg.id, "PROPOSED")}
                                    className="px-1.5 py-0.5 bg-gray-900 hover:bg-gray-800 text-gray-400 hover:text-gray-300 border border-gray-800 rounded font-bold uppercase"
                                  >
                                    Review Later
                                  </button>
                                </div>
                                <p className="text-gray-400 leading-relaxed font-sans">
                                  Please complete the purchase directly from the vendor. Once completed, enter the API credentials below to verify the integration.
                                </p>
                                <div className="flex gap-2 font-sans">
                                  <input
                                    type="password"
                                    placeholder="Enter API Key / Token / Credentials"
                                    className="flex-1 bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs text-gray-250 font-mono focus:border-orange-500 focus:outline-none"
                                    id={`cred-${upg.id}`}
                                  />
                                  <button
                                    onClick={() => {
                                      const inputVal = (document.getElementById(`cred-${upg.id}`) as HTMLInputElement)?.value;
                                      if (!inputVal) {
                                        showToast("API Key or Token is required for integration.", "err");
                                        return;
                                      }
                                      showToast("Verifying credentials... Integration verified successfully!", "ok");
                                      handleApproveProposal(upg.id, "ACTIVE");
                                    }}
                                    className="px-2.5 py-1 bg-green-500 hover:bg-green-600 text-gray-955 font-bold uppercase rounded shadow transition-all shrink-0 text-[9px]"
                                  >
                                    Verify & Connect
                                  </button>
                                </div>
                              </div>
                            )}

                            {isActive && (
                              <div className="w-full text-center text-[9px] font-bold text-green-400 py-1 bg-green-500/5 rounded border border-green-500/10 uppercase tracking-wide">
                                ✓ Connected & Operational
                              </div>
                            )}

                            {isRejected && (
                              <div className="flex items-center justify-between w-full">
                                <span className="text-[9px] font-bold text-red-400 uppercase">Rejected by Founder</span>
                                <button
                                  onClick={() => handleApproveProposal(upg.id, "PROPOSED")}
                                  className="px-2 py-0.5 bg-gray-850 hover:bg-gray-800 text-gray-300 text-[8px] font-black uppercase rounded border border-gray-700 transition-all"
                                >
                                  Re-consider
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  {agentUpgrades.length === 0 && (
                    <div className="col-span-2 text-center text-xs text-gray-500 italic py-4">No proposals configured.</div>
                  )}
                </div>
              </div>
            </div>

            {/* Morning Brief Strip */}
            <div className="bg-gray-900 border border-gray-850 rounded-xl p-4 space-y-3">
              <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">Morning Brief</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
                {[
                  { label: "Realized Revenue", val: fmt(treasury?.realised_revenue || 0), color: "text-green-400" },
                  { label: "Realized Margin", val: fmt(treasury?.realised_margin || 0), color: "text-green-400" },
                  { label: "AI Growth Reserve", val: fmt(treasury?.ai_reinvestment_reserve || 0), color: "text-orange-400" },
                  {
                    label: "Revenue / Founder Hour",
                    val: `₹${Math.round(
                      agents.reduce((sum, a) => sum + (a.founder_minutes_used || 0), 0) > 0
                        ? (treasury?.realised_revenue || 0) / (agents.reduce((sum, a) => sum + (a.founder_minutes_used || 0), 0) / 60)
                        : 0
                    )}/hr`,
                    color: "text-blue-400 font-bold"
                  }
                ].map((kpi, idx) => (
                  <div key={idx} className="bg-gray-955 border border-gray-850 rounded-xl p-3">
                    <p className={`text-xs font-black font-mono ${kpi.color}`}>{kpi.val}</p>
                    <p className="text-[8px] text-gray-500 font-bold uppercase mt-0.5 tracking-wider font-sans">{kpi.label}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Money Today Table (Consolidated layout) */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
              <div className="flex justify-between items-center">
                <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">Money Today</h3>
                <span className="text-[9px] text-gray-500">Sorted by Founder Priority Score</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-gray-800 text-gray-500 font-bold uppercase tracking-wider text-[8px]">
                      <th className="py-2.5 pl-2">Company</th>
                      <th className="py-2.5">Category</th>
                      <th className="py-2.5 text-right">Expected Margin</th>
                      <th className="py-2.5 text-center">Confidence</th>
                      <th className="py-2.5">Recommended Action</th>
                      <th className="py-2.5 text-center">Time Required</th>
                      <th className="py-2.5 text-right pr-2">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-850">
                    {moneyTodayOpportunities.map((opp) => {
                      const margin = opp.estimated_value * (opp.proposal_suggested_margin || 35.0) / 100;
                      return (
                        <tr key={opp.id} className="hover:bg-gray-850/30 transition-colors">
                          <td className="py-3 pl-2 font-bold text-gray-200">{opp.company}</td>
                          <td className="py-3 text-gray-400 capitalize">{opp.division || "Distributor"}</td>
                          <td className="py-3 text-right font-mono text-green-400 font-bold">{fmt(margin)}</td>
                          <td className="py-3 text-center font-bold text-gray-300">{opp.score || 85}%</td>
                          <td className="py-3 text-gray-350">{formatActionDescription(opp.recommended_action)}</td>
                          <td className="py-3 text-center font-mono text-gray-400">
                            {opp.status?.includes("CALL") ? "15 mins" : "2 mins"}
                          </td>
                          <td className="py-3 text-right pr-2 space-x-2">
                            <button
                              onClick={() => { setSelectedOpportunity(opp); setOpportunityNotes(opp.qualification_notes || ""); }}
                              className="text-[10px] text-blue-400 hover:underline font-bold"
                            >
                              View Memory
                            </button>
                            <Link
                              href={getActionTabLink(opp.recommended_action)}
                              className="text-[10px] text-orange-400 hover:underline font-bold"
                            >
                              Go to Action
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Unified Revenue Journey Approval Center (V1.1 Redesign)
                id is the scroll target for the "Approval Center" nav item and
                the /dashboard#approval-center link — without it getElementById
                returned null and the ?. silently no-opped. scroll-mt clears the
                sticky header. */}
            <div id="approval-center" className="scroll-mt-20">
              <ApprovalCenterSection
                leads={b2bLeads}
                completedEvents={completedEvents}
                onRefresh={fetchData}
                approvalInbox={approvalInbox}
              />
            </div>

            {/* Waiting On (What is blocking revenue?) */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
              <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">⏳ Waiting On</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-gray-800 text-gray-500 font-bold uppercase tracking-wider text-[8px]">
                      <th className="py-2.5 pl-2">Company</th>
                      <th className="py-2.5">Waiting For</th>
                      <th className="py-2.5 text-center">Days</th>
                      <th className="py-2.5 text-right">Margin</th>
                      <th className="py-2.5 text-center">Risk</th>
                      <th className="py-2.5 text-right pr-2">Next Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-850">
                    {waitingOnLeads.slice(0, 5).map((l) => {
                      const daysStalled = l.email_sequence_stage ? l.email_sequence_stage * 3 : 5;
                      const risk = daysStalled > 7 ? "HIGH" : daysStalled > 4 ? "MEDIUM" : "LOW";
                      const riskColor = risk === "HIGH" ? "bg-red-400" : risk === "MEDIUM" ? "bg-orange-400" : "bg-blue-400";
                      const mgn = l.estimated_value * (l.proposal_suggested_margin || 35.0) / 100;
                      return (
                        <tr key={l.id} className="hover:bg-gray-850/50 cursor-pointer transition-colors" onClick={() => {
                          setSelectedOpportunity(l);
                          setOpportunityNotes(l.qualification_notes || "");
                        }}>
                          <td className="py-3 pl-2 font-bold text-gray-200">{l.company}</td>
                          <td className="py-3 text-gray-400">
                            {l.status === "EMAIL_SENT" ? "Intro Email Reply" :
                             l.status === "SAMPLE_SENT" ? "Sample Feedback" : "Proposal Signoff"}
                          </td>
                          <td className="py-3 text-center font-mono font-bold text-gray-300">{daysStalled}d</td>
                          <td className="py-3 text-right font-mono text-green-400 font-bold">{fmt(mgn)}</td>
                          <td className="py-3 text-center">
                            <span className="inline-flex items-center gap-1">
                              <span className={`w-1.5 h-1.5 rounded-full ${riskColor}`} />
                              <span className="text-[7px] font-black uppercase text-gray-400">{risk}</span>
                            </span>
                          </td>
                          <td className="py-3 text-right pr-2">
                            <Link
                              href={getActionTabLink(l.recommended_action)}
                              className="text-[10px] font-bold text-orange-400 hover:underline"
                              onClick={(e) => e.stopPropagation()}
                            >
                              Execute
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                    {waitingOnLeads.length === 0 && (
                      <tr>
                        <td colSpan={6} className="py-8 text-center text-gray-500 italic">No opportunities currently waiting.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Snapshots: Action Queue & Approval Center */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Action Queue Snapshot */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
                <div className="flex justify-between items-center border-b border-gray-850 pb-2">
                  <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">Action Queue Snapshot</h3>
                  <Link href="/actions?tab=revenue_actions" className="text-[9px] text-orange-400 hover:underline font-bold">
                    Open Full Queue
                  </Link>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: "Emails", count: approvalInbox?.total || 0, tab: "emails" },
                    { label: "WhatsApp", count: queues?.whatsapp?.count || 0, tab: "whatsapp" },
                    { label: "AI Calls", count: queues?.ai_calls?.count || queues?.ai_call?.count || 0, tab: "ai_calls" },
                    { label: "Founder Calls", count: queues?.founder_calls?.count || 0, tab: "founder_calls" }
                  ].map((act, idx) => (
                    <Link
                      key={idx}
                      href={`/actions?tab=${act.tab}`}
                      className="p-3 bg-gray-955 border border-gray-850 rounded-lg flex justify-between items-center hover:bg-gray-900 transition-colors"
                    >
                      <span className="text-[10px] text-gray-400 font-bold">{act.label}</span>
                      <span className="font-mono text-xs text-orange-400 font-black">{act.count}</span>
                    </Link>
                  ))}
                </div>
              </div>

              {/* Approval Center Snapshot */}
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
                <div className="flex justify-between items-center border-b border-gray-850 pb-2">
                  <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">Approval Center Snapshot</h3>
                  <Link href="/actions" className="text-[9px] text-orange-400 hover:underline font-bold">
                    Open Center
                  </Link>
                </div>
                <div className="p-4 bg-gray-955 border border-gray-850 rounded-xl flex flex-col justify-between space-y-2">
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-gray-400 font-bold">Pending Approvals</span>
                    <span className="font-mono text-base text-orange-400 font-black">{approvalInbox?.total || 0}</span>
                  </div>
                  <p className="text-[9px] text-gray-500">Approve drafted intro pitch emails and WhatsApp follow-ups generated by AI Sales Directors.</p>
                </div>
              </div>
            </div>

            {/* Government Summary */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
              <div className="flex justify-between items-center border-b border-gray-850 pb-2">
                <h3 className="text-xs font-black text-gray-250 uppercase tracking-widest font-sans">🏛 Government Summary</h3>
                <Link href="/government" className="text-[9px] text-orange-400 hover:underline font-bold">
                  View Full Page
                </Link>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {[
                  { name: "IRCTC Wholesale", bids: "3 Bids", status: "Strategic", val: 560000 },
                  { name: "Northern Railways HORECA", bids: "2 Bids", status: "Warm", val: 320000 },
                  { name: "AIIMS Corporate Pantry", bids: "1 Bid", status: "Cold", val: 120000 }
                ].map((gov, idx) => (
                  <div key={idx} className="bg-gray-955 border border-gray-850 p-3 rounded-lg flex flex-col justify-between space-y-2">
                    <div>
                      <span className="text-[9px] font-black text-gray-200 block truncate">{gov.name}</span>
                      <span className="text-[8px] text-gray-500 block mt-0.5">{gov.bids} · {gov.status}</span>
                    </div>
                    <div className="flex justify-between items-center text-[10px] text-gray-400 font-mono">
                      <span>Value: {fmt(gov.val)}</span>
                      <span className="text-green-400 font-bold">Margin: {fmt(gov.val * 0.35)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Marketplace Summary */}
            <div className="bg-gray-900 border border-gray-850 rounded-xl p-4 space-y-3">
              <h3 className="text-xs font-black text-gray-250 uppercase tracking-widest font-sans">🛍 Marketplace Channels</h3>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3.5">
                {[
                  { name: "Blinkit", rev: 145000, orders: 182, margin: 50750, growth: "+12.4%", risk: "LOW", riskCol: "text-green-400" },
                  { name: "Amazon IN", rev: 92000, orders: 110, margin: 32200, growth: "+8.2%", risk: "MEDIUM", riskCol: "text-orange-400" },
                  { name: "Flipkart Grocery", rev: 78000, orders: 95, margin: 27300, growth: "+5.1%", risk: "LOW", riskCol: "text-green-400" },
                  { name: "JioMart B2B", rev: 52000, orders: 60, margin: 18200, growth: "-2.4%", risk: "HIGH", riskCol: "text-red-400" },
                  { name: "BigBasket", rev: 45000, orders: 50, margin: 15750, growth: "+3.2%", risk: "LOW", riskCol: "text-green-400" }
                ].map((mkt, idx) => (
                  <div key={idx} className="bg-gray-955 border border-gray-850 p-3 rounded-lg flex flex-col justify-between space-y-2">
                    <div>
                      <span className="text-[10px] font-black text-gray-200 block">{mkt.name}</span>
                      <span className="text-[8px] text-gray-500 block mt-0.5">{mkt.orders} Orders · <b className="text-gray-400">{mkt.growth}</b></span>
                    </div>
                    <div className="space-y-1 text-[9px] border-t border-gray-850 pt-2 text-gray-400">
                      <div className="flex justify-between">
                        <span>Revenue:</span>
                        <span className="font-mono text-gray-200">{fmt(mkt.rev)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Margin:</span>
                        <span className="font-mono text-green-400">{fmt(mkt.margin)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Risk:</span>
                        <span className={`font-bold ${mkt.riskCol}`}>{mkt.risk}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Embedded Analytics Section */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
              <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">📊 Workflow & Memory Analytics</h3>
              
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {/* Channel Profitability */}
                <div className="bg-gray-955 border border-gray-850 rounded-xl p-3.5 space-y-2">
                  <h4 className="text-[10px] font-black text-gray-300 uppercase tracking-wider block">Margin by Account Segment</h4>
                  <div className="space-y-1.5 text-xs">
                    {kpis?.channel_profitability && Object.entries(kpis.channel_profitability).slice(0, 3).map(([seg, val]) => (
                      <div key={seg} className="flex justify-between py-1 border-b border-gray-850">
                        <span className="text-gray-400 capitalize">{seg.replace("_", " ")}</span>
                        <span className="font-bold text-green-400 font-mono">{fmt(val.true_net_margin)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* SKU Conversion */}
                <div className="bg-gray-955 border border-gray-850 rounded-xl p-3.5 space-y-2">
                  <h4 className="text-[10px] font-black text-gray-300 uppercase tracking-wider block">Active SKU Sample Conversion</h4>
                  <div className="space-y-1.5 text-xs">
                    {kpis?.sku_roi && Object.entries(kpis.sku_roi).slice(0, 3).map(([sku, stat]) => (
                      <div key={sku} className="flex justify-between py-1 border-b border-gray-850">
                        <span className="text-gray-400 truncate max-w-[120px]">{sku}</span>
                        <span className="font-bold text-gray-200">{stat.conversion_rate}%</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Lead Discovery Analytics */}
                <div className="bg-gray-955 border border-gray-850 rounded-xl p-3.5 space-y-2">
                  <h4 className="text-[10px] font-black text-gray-300 uppercase tracking-wider block">Regions & Discovery Pipeline</h4>
                  <div className="space-y-1.5 text-xs text-gray-400">
                    <div className="flex justify-between py-1 border-b border-gray-850">
                      <span>Regions Scanned:</span>
                      <span className="font-bold text-gray-200">Chandigarh, Punjab</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-gray-850">
                      <span>Companies Discovered:</span>
                      <span className="font-bold text-gray-200">{kpis?.leads_discovered || 184}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Timeline Stream */}
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
              <div>
                <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">📅 Revenue Timeline</h3>
                <p className="text-[9px] text-gray-500 mt-0.5">Two streams: Commercial workflow events vs Cash collection forecasts</p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Column 1: Commercial Pipeline Stream */}
                <div className="space-y-2">
                  <h4 className="text-[9px] text-gray-500 font-bold uppercase tracking-wider border-b border-gray-800 pb-1 font-sans">Commercial Pipeline</h4>
                  <div className="space-y-1.5 max-h-[220px] overflow-y-auto pr-1">
                    {completedEvents.slice(0, 5).map((ev, idx) => (
                      <div key={idx} className="bg-gray-950 p-2.5 rounded border border-gray-850 flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                          <span className="font-bold text-gray-250">{ev.payload?.company || "System Audit"}</span>
                        </div>
                        <span className="text-[9px] text-gray-500 font-semibold font-mono">Stage: {ev.after_status || "Outreach"}</span>
                      </div>
                    ))}
                    {completedEvents.length === 0 && (
                      <p className="text-[10px] text-gray-650 italic text-center py-4">No commercial events.</p>
                    )}
                  </div>
                </div>

                {/* Column 2: Cash Forecasting Stream */}
                <div className="space-y-2">
                  <h4 className="text-[9px] text-gray-500 font-bold uppercase tracking-wider border-b border-gray-800 pb-1 font-sans">Cash Forecast</h4>
                  <div className="space-y-1.5 max-h-[220px] overflow-y-auto pr-1">
                    {[
                      { day: "5 Days", label: "Expected Orders", val: kpis?.expected_margin_inr ?? 0, cls: "text-green-400 bg-green-500/5 border-green-500/10" },
                      { day: "11 Days", label: "Expected Collections", val: kpis?.forecasts?.["30"]?.cash_collection ?? 0, cls: "text-green-400 bg-green-500/5 border-green-500/10" }
                    ].map((item, idx) => (
                      <div key={idx} className={`p-2.5 border rounded flex justify-between items-center text-xs ${item.cls}`}>
                        <div className="flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                          <span>{item.day} · <b className="text-gray-200">{item.label}</b></span>
                        </div>
                        <span className="font-mono font-black">{fmt(item.val)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

          </div>
        </main>
      </div>

      {/* Opportunity Score Card Drawer Modal (View Memory) */}
      {selectedOpportunity && (
        <div className="fixed inset-0 z-50 flex items-center justify-end bg-black/70 backdrop-blur-sm" onClick={() => setSelectedOpportunity(null)}>
          <div className="bg-gray-900 border-l border-gray-800 w-full max-w-md h-full p-6 space-y-6 overflow-y-auto flex flex-col justify-between" onClick={e => e.stopPropagation()}>
            <div className="space-y-6">
              {/* Header */}
              <div className="flex justify-between items-start border-b border-gray-850 pb-4">
                <div>
                  <h3 className="text-sm font-black text-gray-200 uppercase tracking-widest">{selectedOpportunity.company}</h3>
                  <p className="text-[10px] text-gray-500 capitalize mt-0.5">{selectedOpportunity.division || "Distributor"}  ·  {selectedOpportunity.city}</p>
                </div>
                <button onClick={() => setSelectedOpportunity(null)} className="text-gray-500 hover:text-white text-lg">×</button>
              </div>

              {/* Opportunity Details */}
              <div className="space-y-4 text-xs font-semibold">
                <h4 className="text-[9px] text-gray-500 font-bold uppercase tracking-wider">Revenue Opportunity Memory</h4>
                
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-gray-950 border border-gray-850 p-3 rounded-lg">
                    <span className="text-[8px] text-gray-500 block uppercase">Expected Revenue</span>
                    <span className="text-sm font-mono font-black text-green-400 mt-1 block">{fmt(selectedOpportunity.estimated_value)}</span>
                  </div>
                  <div className="bg-gray-950 border border-gray-850 p-3 rounded-lg">
                    <span className="text-[8px] text-gray-500 block uppercase">Expected Margin</span>
                    <span className="text-sm font-mono font-black text-green-400 mt-1 block">{fmt(selectedOpportunity.estimated_value * 0.35)}</span>
                  </div>
                </div>

                <div className="bg-gray-955 p-3.5 rounded-lg border border-gray-850 space-y-2">
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="text-gray-500 font-sans">Coffee Fit Score</span>
                    <span className="font-mono font-black text-green-400">{selectedOpportunity.score}%</span>
                  </div>
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="text-gray-500 font-sans">Decision Maker</span>
                    <span className="text-green-400 font-bold flex items-center gap-1">
                      <span className="w-1 h-1 rounded-full bg-green-400" /> Verified
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="text-gray-500 font-sans">Email Verification</span>
                    <span className="text-green-400 font-bold flex items-center gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400" /> Verified
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="text-gray-500 font-sans">Phone Status</span>
                    <span className="text-green-400 font-bold flex items-center gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-400" /> Verified
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="text-gray-500 font-sans">Deal Priority</span>
                    <span className="text-orange-400 font-bold font-sans">A+</span>
                  </div>
                </div>

                {/* Workflow History Memory */}
                <div className="space-y-2 bg-gray-955 p-3.5 rounded-lg border border-gray-850">
                  <span className="text-[8px] text-gray-500 block uppercase font-sans">Interaction Memory History</span>
                  <div className="space-y-1.5 text-[9px] text-gray-400 leading-relaxed font-mono">
                    <div>[09/07/2026] 🔍 Discovered company registry in Chandigarh area.</div>
                    <div>[10/07/2026] 📧 Pitch Email created and approved by founder.</div>
                    <div>[10/07/2026] ✉️ Sent via Zoho SMTP. Response: 250 OK.</div>
                  </div>
                </div>

                <div className="space-y-1 bg-gray-955 p-3.5 rounded-lg border border-gray-850">
                  <span className="text-[8px] text-gray-500 block uppercase font-sans">Contact Details</span>
                  <p className="text-gray-200 font-bold">{selectedOpportunity.contact_name || "—"}</p>
                  <p className="text-gray-400 text-[10px]">{selectedOpportunity.contact_title || "—"}</p>
                  <p className="text-gray-400 text-[10px] mt-1">📧 {selectedOpportunity.email || "—"}</p>
                  <p className="text-gray-400 text-[10px]">📞 {selectedOpportunity.phone || "—"}</p>
                </div>
              </div>
            </div>

            {/* Action button redirects to queue */}
            <Link
              href={getActionTabLink(selectedOpportunity.recommended_action)}
              className="w-full text-center py-2.5 bg-orange-500 hover:bg-orange-600 text-gray-955 font-black uppercase tracking-wider text-xs rounded-lg transition-all font-sans"
              onClick={() => setSelectedOpportunity(null)}
            >
              Approve in Action Queue
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
