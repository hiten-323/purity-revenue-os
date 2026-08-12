"use client";
import React, { useState } from "react";
import { TrendingUp, AlertTriangle, ChevronDown, ChevronRight, Zap, Target, DollarSign, Users } from "lucide-react";

interface B2BKpis {
  pipeline_value_inr: number;
  expected_revenue_inr: number;
  orders_won: number;
  meetings_booked: number;
  samples_sent: number;
  revenue_this_week_inr: number;
  revenue_today_inr: number;
  blind_spots?: string[];
  data_quality?: { complete_contacts: number; incomplete_contacts: number };
  leakage?: { total_revenue_leaking: number };
  forecasts?: Record<string, { revenue: number; margin: number; cash_collection: number }>;
}

interface Props {
  kpis: B2BKpis | null;
  live: boolean;
  leadsTotal: number;
}

function fmt(n: number) {
  if (n >= 10_000_000) return `₹${(n / 10_000_000).toFixed(1)}Cr`;
  if (n >= 100_000) return `₹${(n / 100_000).toFixed(1)}L`;
  if (n >= 1_000) return `₹${(n / 1_000).toFixed(0)}K`;
  return `₹${n.toLocaleString("en-IN")}`;
}

export default function DashboardHero({ kpis, live, leadsTotal }: Props) {
  const [showQA, setShowQA] = useState(false);

  const weeklyTarget = 100_000;
  const wonThisWeek = kpis?.revenue_this_week_inr ?? 0;
  const gapPct = Math.min(100, (wonThisWeek / weeklyTarget) * 100);
  const pipeline = kpis?.pipeline_value_inr ?? 0;
  const forecast30 = kpis?.forecasts?.["30"]?.revenue ?? 0;
  const leakage = kpis?.leakage?.total_revenue_leaking ?? 0;
  const completeContacts = kpis?.data_quality?.complete_contacts ?? 0;
  const incompleteContacts = kpis?.data_quality?.incomplete_contacts ?? 0;
  const totalContacts = completeContacts + incompleteContacts || 1;
  const contactHealth = Math.round((completeContacts / totalContacts) * 100);

  const qaItems = [
    { q: "What is my total B2B active pipeline?", a: `${fmt(pipeline)} pipeline across ${leadsTotal} leads.`, action: "Open CRM and call your top 3 warm leads today." },
    { q: "What revenue can close in the next 30 days?", a: `${fmt(forecast30)} expected this month.`, action: "Follow up with EMAIL_SENT and REPLIED leads." },
    { q: "How much revenue is leaking across stalled stages?", a: leakage > 0 ? `${fmt(leakage)} leaking across stalled stages.` : "No significant leakage.", action: "Move stalled leads to next stage or deprioritise." },
    { q: "How are my contacts?", a: `${completeContacts} complete, ${incompleteContacts} incomplete (${contactHealth}% health).`, action: "Enrich incomplete contacts with email/phone from LinkedIn." },
    { q: "What should I do right now?", a: "Fire the email engine above — 98 leads are ready for outreach.", action: "Click 'Fire Emails' in the Revenue Engine panel above." },
  ];

  return (
    <div className="mb-6 space-y-3">

      {/* Status bar */}
      {!live && (
        <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-2.5">
          <div className="w-2 h-2 rounded-full bg-amber-500 animate-pulse shrink-0" />
          <p className="text-xs text-amber-300 font-medium">Backend offline — showing demo data. Start the API server to go live.</p>
        </div>
      )}

      {/* Hero KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          {
            label: "Pipeline",
            value: fmt(pipeline),
            sub: `${leadsTotal} active leads`,
            icon: <Target className="w-4 h-4" />,
            color: "text-amber-400",
            bg: "bg-amber-500/5 border-amber-500/15",
          },
          {
            label: "Won This Week",
            value: fmt(wonThisWeek),
            sub: `of ${fmt(weeklyTarget)} target`,
            icon: <DollarSign className="w-4 h-4" />,
            color: wonThisWeek >= weeklyTarget ? "text-green-400" : "text-red-400",
            bg: wonThisWeek >= weeklyTarget ? "bg-green-500/5 border-green-500/15" : "bg-red-500/5 border-red-500/15",
          },
          {
            label: "30-Day Forecast",
            value: fmt(forecast30),
            sub: "expected revenue",
            icon: <TrendingUp className="w-4 h-4" />,
            color: "text-blue-400",
            bg: "bg-blue-500/5 border-blue-500/15",
          },
          {
            label: "Meetings Booked",
            value: String(kpis?.meetings_booked ?? 0),
            sub: `${kpis?.samples_sent ?? 0} samples sent · ${kpis?.orders_won ?? 0} won`,
            icon: <Users className="w-4 h-4" />,
            color: "text-purple-400",
            bg: "bg-purple-500/5 border-purple-500/15",
          },
        ].map(card => (
          <div key={card.label} className={`rounded-xl border p-4 ${card.bg}`}>
            <div className={`flex items-center gap-1.5 mb-2 ${card.color}`}>
              {card.icon}
              <span className="text-[10px] font-black uppercase tracking-wider">{card.label}</span>
            </div>
            <p className={`text-2xl font-black leading-none ${card.color}`}>{card.value}</p>
            <p className="text-[10px] text-gray-500 mt-1">{card.sub}</p>
          </div>
        ))}
      </div>

      {/* Weekly revenue progress bar */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex items-center gap-4">
        <div className="flex-1">
          <div className="flex justify-between text-[10px] mb-1.5">
            <span className="text-gray-400 font-medium">Weekly revenue progress</span>
            <span className={`font-black ${gapPct >= 100 ? "text-green-400" : "text-red-400"}`}>{gapPct.toFixed(0)}%</span>
          </div>
          <div className="h-2.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-700 ${gapPct >= 100 ? "bg-green-500" : gapPct >= 50 ? "bg-amber-500" : "bg-red-500"}`}
              style={{ width: `${gapPct}%` }}
            />
          </div>
        </div>
        {gapPct < 100 && (
          <div className="text-right shrink-0">
            <p className="text-xs font-black text-red-400">{fmt(weeklyTarget - wonThisWeek)} gap</p>
            <p className="text-[10px] text-gray-600">to weekly target</p>
          </div>
        )}
        {gapPct >= 100 && (
          <span className="text-xs font-black text-green-400 bg-green-500/10 border border-green-500/20 px-3 py-1 rounded-full shrink-0">
            Target Hit!
          </span>
        )}
      </div>

      {/* Blind spots — only if any */}
      {kpis?.blind_spots && kpis.blind_spots.length > 0 && (
        <div className="bg-gray-900 border border-red-500/15 rounded-xl px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
            <span className="text-[10px] font-black uppercase tracking-wider text-red-400">Blind Spots — Act On These</span>
          </div>
          <ul className="space-y-1">
            {kpis.blind_spots.map((spot, i) => (
              <li key={i} className="text-xs text-gray-300 flex items-start gap-2">
                <span className="text-red-400 mt-0.5 shrink-0">•</span>
                {spot}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Collapsible Q&A */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <button
          onClick={() => setShowQA(v => !v)}
          className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-800/50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-xs font-black uppercase tracking-wider text-gray-300">5 Key Business Questions</span>
          </div>
          {showQA ? <ChevronDown className="w-4 h-4 text-gray-600" /> : <ChevronRight className="w-4 h-4 text-gray-600" />}
        </button>
        {showQA && (
          <div className="border-t border-gray-800 divide-y divide-gray-800">
            {qaItems.map((item, i) => (
              <QAItem key={i} num={i + 1} q={item.q} a={item.a} action={item.action} />
            ))}
          </div>
        )}
      </div>

    </div>
  );
}

function QAItem({ num, q, a, action }: { num: number; q: string; a: string; action: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-gray-800/30 transition-colors"
      >
        <span className="w-5 h-5 rounded-full bg-amber-500/15 text-amber-400 text-[9px] font-black flex items-center justify-center shrink-0 mt-0.5">{num}</span>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-gray-200">{q}</p>
          {!open && <p className="text-[10px] text-gray-500 mt-0.5 truncate">{a}</p>}
        </div>
        {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-600 shrink-0 mt-0.5" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-600 shrink-0 mt-0.5" />}
      </button>
      {open && (
        <div className="px-4 pb-4 pt-1 bg-gray-950/60">
          <p className="text-sm text-gray-200 leading-relaxed mb-2">{a}</p>
          <div className="bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-2">
            <p className="text-[10px] text-green-400 font-black uppercase tracking-wider mb-0.5">Action</p>
            <p className="text-xs text-green-200">{action}</p>
          </div>
        </div>
      )}
    </div>
  );
}
