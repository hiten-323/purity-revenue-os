"use client";
import React, { useEffect, useState, useCallback } from "react";
import { TrendingUp, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";

interface FunnelStage {
  stage: string;
  count: number;
  pct: number;
  color: string;
}

interface FunnelData {
  funnel: FunnelStage[];
  kpis: Record<string, number>;
}

const COLOR_MAP: Record<string, string> = {
  gray:    "bg-gray-600",
  blue:    "bg-blue-500",
  cyan:    "bg-cyan-500",
  amber:   "bg-amber-500",
  yellow:  "bg-yellow-400",
  purple:  "bg-purple-500",
  indigo:  "bg-indigo-500",
  teal:    "bg-teal-500",
  green:   "bg-green-500",
  emerald: "bg-emerald-500",
};

const TEXT_COLOR: Record<string, string> = {
  gray:    "text-gray-400",
  blue:    "text-blue-400",
  cyan:    "text-cyan-400",
  amber:   "text-amber-400",
  yellow:  "text-yellow-400",
  purple:  "text-purple-400",
  indigo:  "text-indigo-400",
  teal:    "text-teal-400",
  green:   "text-green-400",
  emerald: "text-emerald-400",
};

const KPI_LABELS: Record<string, string> = {
  email_found_pct:       "Email Found %",
  email_verified_pct:    "Email Verified %",
  email_sent_pct:        "Email Sent %",
  email_open_rate:       "Open Rate",
  reply_rate:            "Reply Rate",
  meeting_rate:          "Meeting Rate",
  sample_conversion:     "Sample → Meet",
  proposal_conversion:   "Proposal %",
  order_conversion:      "Order Win %",
  end_to_end_conversion: "End-to-End",
};

const KPI_TARGETS: Record<string, number> = {
  email_found_pct:       60,
  email_verified_pct:    80,
  email_sent_pct:        90,
  email_open_rate:       25,
  reply_rate:            8,
  meeting_rate:          30,
  sample_conversion:     60,
  proposal_conversion:   70,
  order_conversion:      40,
  end_to_end_conversion: 2,
};

export default function PipelineFunnel() {
  const [data, setData] = useState<FunnelData | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/b2b/pipeline/funnel");
      if (r.ok) setData(await r.json());
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const maxCount = Math.max(1, ...(data?.funnel.map(s => s.count) || [1]));

  return (
    <div className="mb-6 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <button
        onClick={() => { setOpen(v => !v); if (!open) load(); }}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-800/40 transition-colors"
      >
        <div className="flex items-center gap-3">
          <TrendingUp className="w-4 h-4 text-green-400" />
          <div className="text-left">
            <span className="text-sm font-black text-gray-100">Sales Pipeline Funnel</span>
            <p className="text-[10px] text-gray-500 mt-0.5">
              Discovery → Enrichment → Verification → Email → Meeting → Order
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {data && (
            <div className="hidden md:flex items-center gap-3 text-[10px] font-bold">
              <span className="text-gray-400">{data.funnel[0]?.count ?? 0} Leads</span>
              <span className="text-amber-400">→ {data.kpis.reply_rate ?? 0}% Reply</span>
              <span className="text-emerald-400">→ {data.funnel[data.funnel.length - 1]?.count ?? 0} Won</span>
            </div>
          )}
          {open ? <ChevronDown className="w-4 h-4 text-gray-600" /> : <ChevronRight className="w-4 h-4 text-gray-600" />}
        </div>
      </button>

      {open && (
        <div className="border-t border-gray-800 p-5 space-y-5">

          {/* Refresh */}
          <div className="flex justify-end">
            <button onClick={load} className="flex items-center gap-1.5 text-[10px] text-gray-500 hover:text-gray-300 transition-colors">
              <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} /> Refresh
            </button>
          </div>

          {/* Visual funnel */}
          {data && (
            <div className="space-y-2">
              {data.funnel.map((stage, i) => {
                const barWidth = maxCount > 0 ? (stage.count / maxCount) * 100 : 0;
                const prevCount = i > 0 ? data.funnel[i - 1].count : stage.count;
                const dropPct = prevCount > 0 ? ((prevCount - stage.count) / prevCount) * 100 : 0;

                return (
                  <div key={stage.stage} className="flex items-center gap-3">
                    <div className="w-32 text-right shrink-0">
                      <span className="text-[10px] text-gray-400 font-medium">{stage.stage}</span>
                    </div>
                    <div className="flex-1 relative h-7 bg-gray-800/60 rounded-lg overflow-hidden">
                      <div
                        className={`h-full rounded-lg transition-all duration-500 ${COLOR_MAP[stage.color] || "bg-gray-600"} opacity-80`}
                        style={{ width: `${barWidth}%` }}
                      />
                      <div className="absolute inset-0 flex items-center px-3">
                        <span className={`text-xs font-black ${TEXT_COLOR[stage.color] || "text-gray-400"}`}>
                          {stage.count.toLocaleString()}
                        </span>
                      </div>
                    </div>
                    <div className="w-16 shrink-0 text-right">
                      <span className={`text-[10px] font-black ${stage.pct >= 50 ? "text-gray-300" : stage.pct >= 20 ? "text-amber-400" : "text-red-400"}`}>
                        {stage.pct.toFixed(1)}%
                      </span>
                      {i > 0 && dropPct > 30 && (
                        <p className="text-[9px] text-red-400 leading-none mt-0.5">↓{dropPct.toFixed(0)}%</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* KPI grid */}
          {data?.kpis && (
            <div>
              <p className="text-[10px] font-black uppercase tracking-wider text-gray-500 mb-3">Conversion Rate KPIs</p>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {Object.entries(KPI_LABELS).map(([key, label]) => {
                  const val = data.kpis[key] ?? 0;
                  const target = KPI_TARGETS[key] ?? 50;
                  const ok = val >= target;
                  const warn = val >= target * 0.5;
                  return (
                    <div key={key} className={`rounded-lg p-3 border ${ok ? "bg-green-500/5 border-green-500/15" : warn ? "bg-amber-500/5 border-amber-500/15" : "bg-red-500/5 border-red-500/15"}`}>
                      <p className={`text-lg font-black leading-none ${ok ? "text-green-400" : warn ? "text-amber-400" : "text-red-400"}`}>
                        {val.toFixed(1)}%
                      </p>
                      <p className="text-[9px] text-gray-500 mt-1 leading-tight">{label}</p>
                      <p className={`text-[9px] mt-0.5 font-bold ${ok ? "text-green-600" : "text-gray-600"}`}>
                        target: {target}%
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Priority order — static guidance */}
          <div className="bg-gray-950/60 border border-gray-800 rounded-xl p-4">
            <p className="text-[10px] font-black uppercase tracking-wider text-gray-500 mb-3">Revenue Priority Order</p>
            <div className="grid grid-cols-2 md:grid-cols-7 gap-2">
              {[
                { rank: 1, label: "Corporate Distributors", color: "text-amber-400 border-amber-500/30 bg-amber-500/5" },
                { rank: 2, label: "FMCG Wholesalers",       color: "text-orange-400 border-orange-500/30 bg-orange-500/5" },
                { rank: 3, label: "Modern Trade",           color: "text-yellow-400 border-yellow-500/30 bg-yellow-500/5" },
                { rank: 4, label: "HoReCa",                 color: "text-green-400 border-green-500/30 bg-green-500/5" },
                { rank: 5, label: "Corporate Pantry",       color: "text-blue-400 border-blue-500/30 bg-blue-500/5" },
                { rank: 6, label: "Private Label",          color: "text-purple-400 border-purple-500/30 bg-purple-500/5" },
                { rank: 7, label: "Gov Tenders",            color: "text-gray-400 border-gray-600 bg-gray-800/40" },
              ].map(p => (
                <div key={p.rank} className={`rounded-lg border px-3 py-2 ${p.color}`}>
                  <span className="text-[9px] font-black opacity-60">#{p.rank}</span>
                  <p className="text-[10px] font-bold mt-0.5 leading-tight">{p.label}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Workflow event legend */}
          <div className="bg-gray-950/60 border border-gray-800 rounded-xl p-4">
            <p className="text-[10px] font-black uppercase tracking-wider text-gray-500 mb-2">Tracked Touchpoints</p>
            <div className="flex flex-wrap gap-1.5">
              {[
                "LEAD_DISCOVERED", "LEAD_ENRICHED", "EMAIL_VERIFIED", "EMAIL_APPROVED",
                "EMAIL_SENT", "EMAIL_OPENED", "EMAIL_REPLIED", "PHONE_ENRICHED",
                "WHATSAPP_SENT", "WHATSAPP_REPLIED", "AI_CALL_COMPLETED",
                "FOUNDER_CALL_COMPLETED", "MEETING_BOOKED", "SAMPLE_SENT",
                "PROPOSAL_SENT", "ORDER_WON", "REORDER_TRIGGERED",
              ].map(ev => (
                <span key={ev} className="text-[9px] font-mono px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-500">
                  {ev}
                </span>
              ))}
            </div>
          </div>

        </div>
      )}
    </div>
  );
}
