"use client";
import React from "react";
import { Target, Clock, Zap, TrendingUp, AlertTriangle, ChevronRight, Phone, Mail, MessageCircle, CheckCircle } from "lucide-react";

// ── Types ──────────────────────────────────────────────────────
interface MorningBrief {
  date: string;
  total_leads: number;
  pipeline_value_rs: number;
  hot_leads: number;
  money_today_count: number;
  waiting_on_count: number;
  action_queue_count: number;
  top_channel: string;
}

interface LeadDecision {
  id: number;
  company: string;
  city: string | null;
  contact_name: string | null;
  status: string | null;
  aps: number;
  rrs: number;
  fps: number;
  estimated_value: number;
  margin_rs: number;
  division: string | null;
  email: string | null;
  phone: string | null;
  whatsapp_number: string | null;
  next_followup_date: string | null;
  days_stalled: number;
}

interface ActionQueueItem {
  lead_id: number;
  company: string;
  city: string | null;
  fps: number;
  rrs: number;
  aps: number;
  action_type: string;
  cta_label: string;
  reason: string;
  current_status: string | null;
  expected_margin_rs: number;
  days_stalled: number;
  email: string | null;
  phone: string | null;
  whatsapp_number: string | null;
}

interface RevenueCoach {
  company: string;
  lead_id: number;
  recommended_action: string;
  confidence_pct: number;
  evidence: string[];
  expected_margin_rs: number;
  estimated_days_to_close: number;
  current_status: string;
  fps: number;
}

interface PipelineHealth {
  stage_counts: Record<string, number>;
  total_pipeline_rs: number;
  conversion_rate_pct: number;
  avg_days_to_reply: number;
  stalled_count: number;
  health_grade: "GREEN" | "AMBER" | "RED";
}

interface RevenueTimeline {
  week_1_rs: number;
  week_2_rs: number;
  week_3_rs: number;
  week_4_rs: number;
  month_total_rs: number;
  confidence_pct: number;
}

interface Workspace {
  morning_brief: MorningBrief;
  money_today: LeadDecision[];
  waiting_on: LeadDecision[];
  action_queue: ActionQueueItem[];
  revenue_coach: RevenueCoach | null;
  pipeline_health: PipelineHealth;
  revenue_timeline: RevenueTimeline;
  generated_at: string;
}

// ── Helper ─────────────────────────────────────────────────────
function rs(n: number) {
  if (n >= 100000) return "Rs." + (n / 100000).toFixed(1) + "L";
  if (n >= 1000) return "Rs." + (n / 1000).toFixed(0) + "k";
  return "Rs." + n;
}

const ACTION_ICON: Record<string, React.ReactNode> = {
  email:        <Mail className="w-3 h-3" />,
  whatsapp:     <MessageCircle className="w-3 h-3" />,
  ai_call:      <Zap className="w-3 h-3" />,
  founder_call: <Phone className="w-3 h-3" />,
  meeting:      <Target className="w-3 h-3" />,
  sample:       <CheckCircle className="w-3 h-3" />,
  proposal:     <TrendingUp className="w-3 h-3" />,
  close:        <CheckCircle className="w-3 h-3" />,
  reorder:      <TrendingUp className="w-3 h-3" />,
};

const ACTION_COLOR: Record<string, string> = {
  email:        "text-blue-400 border-blue-600/30 bg-blue-600/10",
  whatsapp:     "text-emerald-400 border-emerald-600/30 bg-emerald-600/10",
  ai_call:      "text-violet-400 border-violet-600/30 bg-violet-600/10",
  founder_call: "text-amber-400 border-amber-600/30 bg-amber-600/10",
  meeting:      "text-cyan-400 border-cyan-600/30 bg-cyan-600/10",
  sample:       "text-orange-400 border-orange-600/30 bg-orange-600/10",
  proposal:     "text-rose-400 border-rose-600/30 bg-rose-600/10",
  close:        "text-green-400 border-green-600/30 bg-green-600/10",
  reorder:      "text-teal-400 border-teal-600/30 bg-teal-600/10",
};

const HEALTH_COLOR = {
  GREEN: "text-green-400 border-green-500/30 bg-green-500/5",
  AMBER: "text-amber-400 border-amber-500/30 bg-amber-500/5",
  RED:   "text-red-400 border-red-500/30 bg-red-500/5",
};

// ── Sub-components ─────────────────────────────────────────────

function MorningBriefWidget({ brief }: { brief: MorningBrief }) {
  return (
    <div className="bg-gray-900/60 border border-indigo-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Morning Brief</span>
        <span className="ml-auto text-[9px] text-gray-600">{brief.date}</span>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {[
          { label: "Pipeline", val: rs(brief.pipeline_value_rs), color: "text-indigo-400" },
          { label: "Hot Leads", val: brief.hot_leads, color: "text-amber-400" },
          { label: "Money Today", val: brief.money_today_count, color: "text-green-400" },
          { label: "Action Queue", val: brief.action_queue_count, color: "text-blue-400" },
        ].map(s => (
          <div key={s.label} className="text-center bg-gray-950/50 rounded-lg p-2">
            <p className={"text-base font-black " + s.color}>{s.val}</p>
            <p className="text-[8px] text-gray-600 mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>
      {brief.waiting_on_count > 0 && (
        <p className="mt-2 text-[9px] text-amber-400/80 text-center">
          {brief.waiting_on_count} leads awaiting your response — check Waiting On
        </p>
      )}
    </div>
  );
}

function RevenueCoachWidget({ coach }: { coach: RevenueCoach }) {
  return (
    <div className="bg-gray-900/60 border border-amber-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Revenue Coach</span>
        <span className="ml-auto text-[9px] font-black text-amber-400">{coach.confidence_pct}% confident</span>
      </div>
      <div className="bg-gray-950/60 border border-amber-500/10 rounded-lg p-3 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-black text-gray-100">{coach.company}</p>
          <span className="text-[8px] text-gray-500 bg-gray-800 px-1.5 py-0.5 rounded">{coach.current_status}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[9px] font-black text-amber-400 uppercase tracking-wider">{coach.recommended_action}</span>
          <ChevronRight className="w-3 h-3 text-amber-400" />
          <span className="text-[9px] text-emerald-400 font-bold">{rs(coach.expected_margin_rs)} margin</span>
          <span className="text-[9px] text-gray-600 ml-auto">~{coach.estimated_days_to_close}d to close</span>
        </div>
        {coach.evidence.length > 0 && (
          <div className="space-y-0.5 pt-1 border-t border-gray-800">
            {coach.evidence.map((e, i) => (
              <p key={i} className="text-[8px] text-gray-500 flex items-start gap-1">
                <span className="text-amber-500 mt-0.5 shrink-0">›</span>{e}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MoneyTodayWidget({ leads }: { leads: LeadDecision[] }) {
  if (leads.length === 0) return null;
  return (
    <div className="bg-gray-900/60 border border-green-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-1.5 h-1.5 rounded-full bg-green-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Money Today</span>
        <span className="text-[9px] text-green-400 font-black ml-1">{leads.length} ready</span>
        <span className="ml-auto text-[8px] text-gray-600">FPS-ranked</span>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
        {leads.map(lead => (
          <div key={lead.id} className="shrink-0 w-40 bg-gray-950 border border-green-500/10 hover:border-green-500/30 rounded-lg p-2.5 transition-all">
            <div className="flex items-start justify-between gap-1 mb-1">
              <p className="text-[9px] font-bold text-gray-200 line-clamp-1 flex-1">{lead.company}</p>
              <span className="text-[8px] font-black text-green-400 shrink-0">{lead.fps.toFixed(0)}</span>
            </div>
            <p className="text-[8px] text-gray-600 mb-1.5">{lead.city} · {lead.status}</p>
            <p className="text-[9px] font-bold text-emerald-400">{rs(lead.margin_rs)} margin</p>
            <div className="flex gap-1 mt-1.5">
              <span className="text-[7px] bg-gray-800 px-1 py-0.5 rounded text-gray-500">RRS {lead.rrs}</span>
              <span className="text-[7px] bg-gray-800 px-1 py-0.5 rounded text-gray-500">APS {lead.aps}</span>
            </div>
            {(lead.phone || lead.whatsapp_number) && (
              <a href={"tel:" + (lead.phone || lead.whatsapp_number || "").replace(/\D/g, "")}
                className="mt-2 block text-center text-[8px] font-black py-1 bg-green-600/10 hover:bg-green-600 text-green-400 hover:text-white border border-green-600/20 rounded transition-all">
                Call Now
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function WaitingOnWidget({ leads }: { leads: LeadDecision[] }) {
  if (leads.length === 0) return null;
  return (
    <div className="bg-gray-900/60 border border-amber-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Clock className="w-3 h-3 text-amber-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Waiting On</span>
        <span className="text-[9px] text-amber-400 font-black ml-1">{leads.length} stalled</span>
        <span className="ml-auto text-[8px] text-gray-600">awaiting their response</span>
      </div>
      <div className="space-y-1.5">
        {leads.slice(0, 8).map(lead => (
          <div key={lead.id} className="flex items-center gap-3 bg-gray-950/60 border border-gray-800 hover:border-amber-500/20 rounded-lg px-3 py-2 transition-all">
            <div className="flex-1 min-w-0">
              <p className="text-[9px] font-bold text-gray-200 line-clamp-1">{lead.company}</p>
              <p className="text-[8px] text-gray-600">{lead.city} · {lead.status}</p>
            </div>
            <div className="text-right shrink-0">
              <p className="text-[8px] font-black text-amber-400">{lead.days_stalled}d stalled</p>
              <p className="text-[8px] text-emerald-400">{rs(lead.margin_rs)}</p>
            </div>
            <div className="flex gap-1 shrink-0">
              {lead.phone && (
                <a href={"tel:" + lead.phone.replace(/\D/g, "")}
                  className="w-6 h-6 flex items-center justify-center bg-amber-600/10 hover:bg-amber-600 border border-amber-600/20 rounded text-amber-400 hover:text-white transition-all">
                  <Phone className="w-3 h-3" />
                </a>
              )}
              {lead.whatsapp_number && (
                <a href={"https://api.whatsapp.com/send?phone=" + lead.whatsapp_number.replace(/\D/g, "")}
                  target="_blank" rel="noreferrer"
                  className="w-6 h-6 flex items-center justify-center bg-emerald-600/10 hover:bg-emerald-600 border border-emerald-600/20 rounded text-emerald-400 hover:text-white transition-all">
                  <MessageCircle className="w-3 h-3" />
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ActionQueueWidget({ queue, onAction }: { queue: ActionQueueItem[]; onAction: (item: ActionQueueItem) => void }) {
  return (
    <div className="bg-gray-900/60 border border-blue-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Zap className="w-3 h-3 text-blue-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Action Queue</span>
        <span className="text-[9px] text-blue-400 font-black ml-1">{queue.length} items</span>
        <span className="ml-auto text-[8px] text-gray-600">FPS-ranked</span>
      </div>
      <div className="space-y-1.5">
        {queue.slice(0, 12).map((item, i) => {
          const colorCls = ACTION_COLOR[item.action_type] || "text-gray-400 border-gray-600/30 bg-gray-600/10";
          return (
            <div key={item.lead_id + item.action_type}
              className="flex items-center gap-2 bg-gray-950/60 border border-gray-800 hover:border-blue-500/20 rounded-lg px-3 py-2 transition-all group">
              <span className="text-[8px] text-gray-700 w-4 text-center font-black">{i + 1}</span>
              <div className="flex-1 min-w-0">
                <p className="text-[9px] font-bold text-gray-200 line-clamp-1">{item.company}</p>
                <p className="text-[8px] text-gray-600">{item.city} · {item.reason}</p>
              </div>
              <div className="text-right shrink-0 hidden sm:block">
                <p className="text-[8px] text-emerald-400 font-bold">{rs(item.expected_margin_rs)}</p>
                <p className="text-[8px] text-gray-600">{item.days_stalled > 0 ? item.days_stalled + "d stalled" : "fresh"}</p>
              </div>
              <button onClick={() => onAction(item)}
                className={"shrink-0 flex items-center gap-1 text-[8px] font-black px-2 py-1 rounded border transition-all hover:opacity-90 " + colorCls}>
                {ACTION_ICON[item.action_type]}
                <span className="hidden sm:inline">{item.cta_label}</span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PipelineHealthWidget({ health }: { health: PipelineHealth }) {
  const stageOrder = ["cold","email","whatsapp","ai_call","founder","meeting","sample","proposal","won","reorder"];
  const stageLabels: Record<string, string> = {
    cold:"Discovery", email:"Email", whatsapp:"WhatsApp", ai_call:"AI Call",
    founder:"Founder", meeting:"Meeting", sample:"Sample", proposal:"Proposal",
    won:"Won", reorder:"Reorder",
  };
  const total = Object.values(health.stage_counts).reduce((s, n) => s + n, 0) || 1;
  const gradeCls = HEALTH_COLOR[health.health_grade] || HEALTH_COLOR.AMBER;

  return (
    <div className="bg-gray-900/60 border border-gray-700/40 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp className="w-3 h-3 text-gray-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Pipeline Health</span>
        <span className={"ml-auto text-[8px] font-black px-1.5 py-0.5 rounded border " + gradeCls}>
          {health.health_grade}
        </span>
      </div>
      <div className="grid grid-cols-5 gap-1 mb-3">
        {stageOrder.map(stage => {
          const count = health.stage_counts[stage] || 0;
          const pct = Math.round((count / total) * 100);
          return (
            <div key={stage} className="flex flex-col items-center gap-0.5">
              <span className="text-[10px] font-black text-gray-200">{count}</span>
              <div className="w-full bg-gray-800 rounded-full h-1 overflow-hidden">
                <div className="h-1 bg-indigo-500 rounded-full" style={{ width: pct + "%" }} />
              </div>
              <span className="text-[7px] text-gray-600 text-center leading-tight">{stageLabels[stage]}</span>
            </div>
          );
        })}
      </div>
      <div className="flex gap-3 text-center">
        <div className="flex-1 bg-gray-950/40 rounded p-1.5">
          <p className="text-[9px] font-black text-indigo-400">{health.conversion_rate_pct}%</p>
          <p className="text-[7px] text-gray-600">Reply rate</p>
        </div>
        <div className="flex-1 bg-gray-950/40 rounded p-1.5">
          <p className="text-[9px] font-black text-amber-400">{health.stalled_count}</p>
          <p className="text-[7px] text-gray-600">Stalled 14d+</p>
        </div>
        <div className="flex-1 bg-gray-950/40 rounded p-1.5">
          <p className="text-[9px] font-black text-emerald-400">{rs(health.total_pipeline_rs)}</p>
          <p className="text-[7px] text-gray-600">Total pipeline</p>
        </div>
      </div>
    </div>
  );
}

function RevenueTimelineWidget({ timeline }: { timeline: RevenueTimeline }) {
  const weeks = [
    { label: "Wk 1", value: timeline.week_1_rs, note: "Proposals closing" },
    { label: "Wk 2", value: timeline.week_2_rs, note: "Samples converting" },
    { label: "Wk 3", value: timeline.week_3_rs, note: "Meetings advancing" },
    { label: "Wk 4", value: timeline.week_4_rs, note: "Warm leads moving" },
  ];
  const maxVal = Math.max(...weeks.map(w => w.value), 1);

  return (
    <div className="bg-gray-900/60 border border-gray-700/40 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp className="w-3 h-3 text-emerald-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Revenue Timeline</span>
        <span className="ml-auto text-[8px] text-gray-600">{timeline.confidence_pct}% confidence</span>
      </div>
      <div className="flex items-end gap-2 h-20 mb-2">
        {weeks.map((w, i) => {
          const h = Math.max(6, (w.value / maxVal) * 80);
          const hue = 145 + i * 15;
          return (
            <div key={w.label} className="flex-1 flex flex-col items-center gap-1">
              <span className="text-[8px] font-black text-emerald-400">{rs(w.value)}</span>
              <div className="w-full rounded-t" style={{ height: h + "px", background: `hsl(${hue},60%,40%)` }} />
              <span className="text-[7px] text-gray-500">{w.label}</span>
            </div>
          );
        })}
      </div>
      <div className="flex items-center justify-between pt-2 border-t border-gray-800">
        <span className="text-[8px] text-gray-600">Monthly forecast</span>
        <span className="text-[10px] font-black text-emerald-400">{rs(timeline.month_total_rs)} margin</span>
      </div>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────

interface Props {
  showToast: (m: string, t: "ok" | "err" | "info") => void;
  fetchData: () => void;
}

export default function FounderWorkspace({ showToast, fetchData: _fetchData }: Props) {
  const [workspace, setWorkspace] = React.useState<Workspace | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [lastRefresh, setLastRefresh] = React.useState<Date | null>(null);
  const [followUpItem, setFollowUpItem] = React.useState<ActionQueueItem | null>(null);

  const loadWorkspace = React.useCallback(async (retryCount = 0) => {
    try {
      const res = await fetch("/api/v1/dashboard/workspace", { signal: AbortSignal.timeout(10000) });
      if (!res.ok) throw new Error("workspace fetch failed");
      const data = await res.json();
      setWorkspace(data);
      setLastRefresh(new Date());
      setLoading(false);
    } catch {
      if (retryCount < 4) {
        // Silent retry: 3s, 6s, 12s, 20s — backend waking from sleep
        const delay = [3000, 6000, 12000, 20000][retryCount];
        setTimeout(() => loadWorkspace(retryCount + 1), delay);
      } else {
        setLoading(false);
        // Only show toast after all retries exhausted
        showToast("Backend offline — click Reconnect in header", "err");
      }
    }
  }, [showToast]);

  React.useEffect(() => {
    loadWorkspace();
    const interval = setInterval(() => loadWorkspace(), 120_000); // refresh every 2 min
    return () => clearInterval(interval);
  }, [loadWorkspace]);

  const logEvent = async (eventType: string, leadId: number, beforeStatus: string | null, afterStatus?: string) => {
    try {
      await fetch("/api/v1/workflow/event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: eventType, lead_id: leadId, before_status: beforeStatus, after_status: afterStatus, actor: "FOUNDER" }),
      });
    } catch { /* non-critical */ }
  };

  const handleAction = async (item: ActionQueueItem) => {
    setFollowUpItem(item);
    if (item.action_type === "founder_call") {
      await logEvent("FOUNDER_CALL_INITIATED", item.lead_id, item.current_status);
    }
  };

  if (loading) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 mb-6 flex items-center justify-center">
        <div className="text-[10px] text-gray-600 animate-pulse font-black uppercase tracking-widest">
          Building Founder Workspace...
        </div>
      </div>
    );
  }

  if (!workspace) return null;

  const { morning_brief, money_today, waiting_on, action_queue, revenue_coach, pipeline_health, revenue_timeline } = workspace;

  return (
    <div className="mb-6 space-y-3">
      {/* Section header */}
      <div className="flex items-center gap-2 px-1">
        <AlertTriangle className="w-3.5 h-3.5 text-indigo-400" />
        <span className="text-[9px] font-black uppercase tracking-widest text-gray-500">Founder Mode</span>
        {lastRefresh && (
          <span className="ml-auto text-[8px] text-gray-700">
            Updated {lastRefresh.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
          </span>
        )}
        <button onClick={() => loadWorkspace()} className="text-[8px] text-gray-600 hover:text-gray-400 transition-all ml-1">
          Refresh
        </button>
      </div>

      {/* Row 1: Morning Brief + Revenue Coach */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <MorningBriefWidget brief={morning_brief} />
        {revenue_coach && <RevenueCoachWidget coach={revenue_coach} />}
      </div>

      {/* Row 2: Money Today */}
      {money_today.length > 0 && <MoneyTodayWidget leads={money_today} />}

      {/* Row 3: Waiting On + Pipeline Health */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {waiting_on.length > 0 && <WaitingOnWidget leads={waiting_on} />}
        <PipelineHealthWidget health={pipeline_health} />
      </div>

      {/* Row 4: Action Queue + Revenue Timeline */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <ActionQueueWidget queue={action_queue} onAction={handleAction} />
        <RevenueTimelineWidget timeline={revenue_timeline} />
      </div>

      {/* Follow-Up Modal */}
      {followUpItem && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm px-4 pb-6 sm:pb-0" onClick={() => setFollowUpItem(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-[9px] font-black uppercase tracking-widest text-amber-400 mb-0.5">{followUpItem.cta_label}</p>
                <h3 className="text-base font-black text-gray-100">{followUpItem.company}</h3>
                <p className="text-[10px] text-gray-500">{followUpItem.city || "—"} · Rs.{(followUpItem.expected_margin_rs / 100000).toFixed(1)}L margin · {followUpItem.days_stalled}d stalled</p>
              </div>
              <button onClick={() => setFollowUpItem(null)} className="text-gray-600 hover:text-gray-300 text-xl leading-none">×</button>
            </div>

            {/* Reason */}
            {followUpItem.reason && (
              <div className="mb-4 px-3 py-2 rounded-lg bg-amber-500/8 border border-amber-500/20">
                <p className="text-[10px] text-amber-300">{followUpItem.reason}</p>
              </div>
            )}

            {/* Contact actions */}
            <div className="flex flex-col gap-2">
              {followUpItem.phone ? (
                <a href={`tel:${followUpItem.phone}`}
                  className="flex items-center gap-3 px-4 py-3 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400 font-bold text-sm hover:bg-green-500/20 transition-all">
                  <Phone className="w-4 h-4 shrink-0" />
                  <div>
                    <p className="text-xs font-black">Call Now</p>
                    <p className="text-[10px] text-green-300/70">{followUpItem.phone}</p>
                  </div>
                </a>
              ) : (
                <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-gray-800/50 border border-gray-700 text-gray-600 text-sm">
                  <Phone className="w-4 h-4 shrink-0" />
                  <p className="text-[10px]">No phone — add in CRM</p>
                </div>
              )}

              {(followUpItem.whatsapp_number || followUpItem.phone) ? (
                <a href={`https://wa.me/${(followUpItem.whatsapp_number || followUpItem.phone || "").replace(/\D/g,"")}`}
                  target="_blank" rel="noopener noreferrer"
                  className="flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-bold text-sm hover:bg-emerald-500/20 transition-all">
                  <MessageCircle className="w-4 h-4 shrink-0" />
                  <div>
                    <p className="text-xs font-black">WhatsApp</p>
                    <p className="text-[10px] text-emerald-300/70">{followUpItem.whatsapp_number || followUpItem.phone}</p>
                  </div>
                </a>
              ) : (
                <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-gray-800/50 border border-gray-700 text-gray-600 text-sm">
                  <MessageCircle className="w-4 h-4 shrink-0" />
                  <p className="text-[10px]">No WhatsApp — add in CRM</p>
                </div>
              )}

              {followUpItem.email ? (
                <a href={`mailto:${followUpItem.email}?subject=Following up — Purity Beans Partnership&body=Dear ${followUpItem.company},%0A%0AJust following up on our previous conversation about Purity Beans.%0A%0AWarm regards,%0AHiten Jain%0APure Pantry Provisions`}
                  className="flex items-center gap-3 px-4 py-3 rounded-xl bg-blue-500/10 border border-blue-500/30 text-blue-400 font-bold text-sm hover:bg-blue-500/20 transition-all">
                  <Mail className="w-4 h-4 shrink-0" />
                  <div>
                    <p className="text-xs font-black">Send Email</p>
                    <p className="text-[10px] text-blue-300/70">{followUpItem.email}</p>
                  </div>
                </a>
              ) : (
                <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-gray-800/50 border border-gray-700 text-gray-600 text-sm">
                  <Mail className="w-4 h-4 shrink-0" />
                  <p className="text-[10px]">No email — add in CRM</p>
                </div>
              )}

              <button onClick={async () => {
                await logEvent("FOLLOW_UP_DONE", followUpItem.lead_id, followUpItem.current_status);
                showToast("Marked done — " + followUpItem.company, "ok");
                setFollowUpItem(null);
              }} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-gray-800 border border-gray-700 text-gray-400 font-bold text-sm hover:bg-gray-700 transition-all mt-1">
                <CheckCircle className="w-4 h-4 shrink-0" />
                <p className="text-xs font-black">Mark Done & Close</p>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
