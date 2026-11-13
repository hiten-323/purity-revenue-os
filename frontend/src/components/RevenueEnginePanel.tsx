"use client";
import React, { useEffect, useState, useCallback } from "react";
import { Zap, User, TrendingUp, MapPin, RefreshCw, Target } from "lucide-react";

// V1.2 Revenue Engine — answers the founder's real questions:
// how much revenue has automation created, how much can founder actions
// unlock, which opportunities rank highest, and where to expand next.

interface Bucket {
  pipeline_value_rs: number;
  expected_margin_rs: number;
  stages: string[];
  [k: string]: number | string[] | undefined;
}
interface Opportunity {
  lead_id: number;
  company: string;
  city: string | null;
  division: string | null;
  status: string | null;
  revenue_potential_rs: number;
  expected_margin_rs: number;
  probability_pct: number;
  confidence_pct: number;
  data_completeness_pct: number;
  opportunity_score: number;
}
interface Stage { key: string; label: string; coverage_pct: number; complete: boolean; }
interface Summary {
  split: {
    automation: Bucket & { companies_discovered: number; verified_emails: number; verified_phones: number; ai_qualified: number };
    founder: Bucket & { founder_calls: number; meetings: number; samples: number; proposals: number };
    won: { value_rs: number; gross_margin_rs: number; orders_won: number };
    combined: { total_pipeline_rs: number; total_expected_margin_rs: number };
  };
  expansion: { stages: Stage[]; next_stage: string; next_cities: string[] };
  top_opportunities: Opportunity[];
}

function rs(n: number) {
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(2) + "Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + "L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + "K";
  return "₹" + Math.round(n);
}

export default function RevenueEnginePanel() {
  const [data, setData] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/revenue/summary").catch(() => null);
      setData(r?.ok ? await r.json().catch(() => null) : null);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  if (loading && !data) {
    return (
      <div className="mb-6 bg-gray-900 border border-gray-800 rounded-xl p-5 text-center text-[10px] text-gray-600 font-black uppercase tracking-widest animate-pulse">
        Computing revenue engine…
      </div>
    );
  }
  if (!data) return null;

  const { automation, founder, won, combined } = data.split;

  return (
    <div className="mb-6 space-y-3">

      {/* ── Automation vs Founder revenue split ── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="bg-gray-900 border border-blue-500/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-[9px] font-black uppercase tracking-widest text-blue-400">Automation Created</span>
          </div>
          <p className="text-2xl font-black text-white">{rs(automation.pipeline_value_rs)}</p>
          <p className="text-[10px] text-gray-500 mt-0.5">pipeline · {rs(automation.expected_margin_rs)} expected margin · zero founder time</p>
          <div className="grid grid-cols-4 gap-1 mt-3 text-center">
            {[
              ["Found", automation.companies_discovered],
              ["✓Email", automation.verified_emails],
              ["✓Phone", automation.verified_phones],
              ["AI Qual", automation.ai_qualified],
            ].map(([l, v]) => (
              <div key={l as string} className="bg-gray-950/60 rounded-lg py-1.5">
                <p className="text-[11px] font-black text-blue-300">{v as number}</p>
                <p className="text-[7px] text-gray-600 font-bold">{l as string}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-gray-900 border border-amber-500/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <User className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-[9px] font-black uppercase tracking-widest text-amber-400">Founder Can Convert</span>
          </div>
          <p className="text-2xl font-black text-white">{rs(founder.pipeline_value_rs)}</p>
          <p className="text-[10px] text-gray-500 mt-0.5">pipeline · {rs(founder.expected_margin_rs)} margin — your calls, meetings, proposals</p>
          <div className="grid grid-cols-4 gap-1 mt-3 text-center">
            {[
              ["Calls", founder.founder_calls],
              ["Meets", founder.meetings],
              ["Samples", founder.samples],
              ["Proposals", founder.proposals],
            ].map(([l, v]) => (
              <div key={l as string} className="bg-gray-950/60 rounded-lg py-1.5">
                <p className="text-[11px] font-black text-amber-300">{v as number}</p>
                <p className="text-[7px] text-gray-600 font-bold">{l as string}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-gray-900 border border-green-500/25 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-3.5 h-3.5 text-green-400" />
            <span className="text-[9px] font-black uppercase tracking-widest text-green-400">Won + Combined</span>
          </div>
          <p className="text-2xl font-black text-white">{rs(won.gross_margin_rs)}</p>
          <p className="text-[10px] text-gray-500 mt-0.5">gross margin won · {won.orders_won} orders</p>
          <div className="mt-3 space-y-1.5">
            <div className="flex justify-between text-[10px]">
              <span className="text-gray-500 font-bold">Total pipeline</span>
              <span className="text-gray-200 font-black">{rs(combined.total_pipeline_rs)}</span>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-gray-500 font-bold">Total expected margin</span>
              <span className="text-emerald-400 font-black">{rs(combined.total_expected_margin_rs)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* ── Expansion coverage + top opportunities ── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <MapPin className="w-3.5 h-3.5 text-cyan-400" />
              <span className="text-[9px] font-black uppercase tracking-widest text-cyan-400">Market Coverage</span>
            </div>
            <button onClick={load} className="text-gray-600 hover:text-gray-300"><RefreshCw className="w-3 h-3" /></button>
          </div>
          <div className="space-y-2">
            {data.expansion.stages.map(s => (
              <div key={s.key}>
                <div className="flex justify-between text-[9px] mb-0.5">
                  <span className="font-bold text-gray-400">{s.label}</span>
                  <span className={"font-black " + (s.complete ? "text-green-400" : "text-gray-500")}>
                    {s.complete ? "✓ Complete" : `${s.coverage_pct}%`}
                  </span>
                </div>
                <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                  <div className={"h-full rounded-full " + (s.complete ? "bg-green-500" : "bg-cyan-500")}
                       style={{ width: `${s.coverage_pct}%` }} />
                </div>
              </div>
            ))}
          </div>
          {data.expansion.next_cities.length > 0 && (
            <p className="text-[8px] text-gray-600 mt-3">
              Next: <span className="text-cyan-400 font-bold">{data.expansion.next_cities.slice(0, 4).join(" · ")}</span>
            </p>
          )}
        </div>

        <div className="md:col-span-2 bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Target className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-[9px] font-black uppercase tracking-widest text-emerald-400">Top Revenue Opportunities</span>
            <span className="text-[8px] text-gray-600">margin × confidence × probability</span>
          </div>
          {data.top_opportunities.length === 0 ? (
            <p className="text-[10px] text-gray-600 text-center py-4">No real data available.</p>
          ) : (
            <div className="space-y-1.5">
              {data.top_opportunities.slice(0, 6).map(o => (
                <div key={o.lead_id} className="flex items-center gap-3 bg-gray-950/50 rounded-lg px-3 py-2">
                  <div className="flex-1 min-w-0">
                    <span className="text-[10px] font-black text-gray-200 truncate">{o.company}</span>
                    <span className="text-[8px] text-gray-600 ml-2">{o.city || ""} · {o.status}</span>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[10px] font-black text-emerald-400">{rs(o.expected_margin_rs)} margin</p>
                    <p className="text-[8px] text-gray-600">
                      {rs(o.revenue_potential_rs)} potential · {o.confidence_pct}% conf · {o.data_completeness_pct}% data
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
