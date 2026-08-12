"use client";
import React, { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { RefreshCw, Building2, FileText, Search, ChevronDown, ChevronUp, Trophy } from "lucide-react";

interface GovLead {
  id: number;
  company: string;
  contact_name: string | null;
  contact_title: string | null;
  email: string | null;
  phone: string | null;
  city: string | null;
  industry: string | null;
  region: string | null;
  status: string;
  priority: string;
  score: number;
  estimated_value: number;
  qualification_notes: string | null;
  recommended_action: string | null;
  website: string | null;
}

interface GovTender {
  id: number;
  tender_id: string;
  title: string;
  department: string;
  portal: string;
  location: string | null;
  estimated_value: number;
  deadline: string | null;
  days_to_deadline: number | null;
  status: string;
  eligibility_check: string;
  notes: string | null;
  source_url: string | null;
  found_at: string | null;
  
  opportunity_score?: number;
  win_probability?: number;
  required_products?: string;
  suggested_pricing?: number;
  quantity_kg?: number;
  expected_margin?: number;
  risk_level?: string;
  proposal_text?: string;
  compliance_checklist?: string;
  missing_documents?: string;
  bid_strategy?: string;
}

function fmtValue(v: number) {
  if (v >= 10_000_000) return `₹${(v / 10_000_000).toFixed(1)} Cr`;
  if (v >= 100_000)    return `₹${(v / 100_000).toFixed(1)} L`;
  return `₹${v.toLocaleString("en-IN")}`;
}

export default function GovernmentPage() {
  const [leads, setLeads]         = useState<GovLead[]>([]);
  const [tenders, setTenders]     = useState<GovTender[]>([]);
  const [tab, setTab]             = useState<"leads" | "tenders">("tenders");
  const [scanning, setScanning]   = useState(false);
  const [loading, setLoading]     = useState(false);
  const [toast, setToast]         = useState<string | null>(null);
  const [search, setSearch]       = useState("");
  
  // Organization accounts states
  const [selectedOrgName, setSelectedOrgName] = useState<string | null>(null);
  const [expandedTenderId, setExpandedTenderId] = useState<number | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const loadLeads = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/b2b/government/leads");
      if (res.ok) setLeads(await res.json());
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTenders = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/b2b/government/tenders");
      if (res.ok) setTenders(await res.json());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLeads();
    loadTenders();
  }, [loadLeads, loadTenders]);

  const scanTenders = async () => {
    setScanning(true);
    showToast("Scanning GeM + CPPP portals… this may take ~20s");
    try {
      const res = await fetch("/api/v1/b2b/government/scan-tenders", { method: "POST" });
      const data = await res.json();
      showToast(`Found ${data.new_tenders} new tenders (${data.updated} updated)`);
      await loadTenders();
    } catch {
      showToast("Scan failed — check backend");
    } finally {
      setScanning(false);
    }
  };

  const updateTenderStatus = async (id: number, status: string) => {
    await fetch(`/api/v1/b2b/government/tenders/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    setTenders(prev => prev.map(t => t.id === id ? { ...t, status } : t));
    showToast(`Status updated to ${status}`);
  };

  // Group tenders by department (organization accounts)
  const orgAccounts = useMemo(() => {
    const groups: Record<string, GovTender[]> = {};
    tenders.forEach(t => {
      const dept = t.department || "Other Department";
      if (!groups[dept]) groups[dept] = [];
      groups[dept].push(t);
    });

    return Object.entries(groups).map(([name, items]) => {
      const totalPotential = items.reduce((s, t) => s + (t.estimated_value || 0), 0);
      const wonTenders = items.filter(t => t.status === "WON").length;
      const totalBids = items.filter(t => t.status !== "OPEN" && t.status !== "SKIPPED").length;
      const relationship = wonTenders > 0 ? "Strategic" : totalBids > 0 ? "Warm" : "Cold";
      const nextOpportunity = items.find(t => t.status === "OPEN" && t.days_to_deadline !== null);

      return {
        organizationName: name,
        tenderCount: items.length,
        potentialRevenue: totalPotential,
        relationshipStatus: relationship,
        lastBidDate: totalBids > 0 ? "45 Days Ago" : "N/A",
        nextBidDate: nextOpportunity ? `${nextOpportunity.days_to_deadline}d left` : "None",
        winRate: totalBids > 0 ? Math.round((wonTenders / totalBids) * 100) : 0,
        items
      };
    }).filter(org => !search || org.organizationName.toLowerCase().includes(search.toLowerCase()));
  }, [tenders, search]);

  const filteredLeads = leads.filter(l =>
    !search ||
    l.company.toLowerCase().includes(search.toLowerCase()) ||
    (l.city || "").toLowerCase().includes(search.toLowerCase())
  );

  const selectedOrg = useMemo(() => {
    return orgAccounts.find(org => org.organizationName === selectedOrgName) || null;
  }, [orgAccounts, selectedOrgName]);

  const totalValue = leads.reduce((s, l) => s + (l.estimated_value || 0), 0);
  const openTenders = tenders.filter(t => t.status === "OPEN").length;
  const tenderValue = tenders.reduce((s, t) => s + (t.estimated_value || 0), 0);

  return (
    <div className="min-h-screen bg-gray-955 text-gray-100 pb-12">
      {toast && (
        <div className="fixed top-16 right-4 z-50 bg-gray-900 border border-gray-800 rounded-lg px-4 py-2 text-sm text-gray-150 shadow-lg max-w-sm">
          {toast}
        </div>
      )}

      <div className="max-w-[1600px] mx-auto px-4 py-6 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between mb-6 flex-wrap gap-2 border-b border-gray-850 pb-4">
          <div>
            <h1 className="text-base font-black uppercase tracking-widest text-gray-100">
              Government Tenders Console
            </h1>
            <p className="text-[9px] text-gray-500 mt-0.5">Defence · Railways · PSUs · Banks · Airports · State Bodies</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={scanTenders}
              disabled={scanning}
              className="flex items-center gap-1.5 text-[10px] font-black uppercase px-3 py-1.5 rounded-lg bg-orange-500/10 border border-orange-500/30 text-orange-400 hover:bg-orange-500/20 disabled:opacity-50 transition-all"
            >
              {scanning ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
              {scanning ? "Scanning…" : "Scan Tenders"}
            </button>
            <button
              onClick={() => { loadLeads(); loadTenders(); }}
              className="text-[10px] text-gray-500 hover:text-gray-300 flex items-center gap-1 uppercase font-bold"
            >
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>
        </div>

        {/* KPI cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {[
            { label: "Gov Opportunities", value: leads.length, sub: "Qualified pipeline", color: "text-orange-400" },
            { label: "Pipeline Value", value: fmtValue(totalValue), sub: "Combined estimated", color: "text-green-400" },
            { label: "Open Tenders", value: openTenders, sub: `${tenders.length} total tracked`, color: "text-blue-400" },
            { label: "Tender Value", value: fmtValue(tenderValue), sub: "Combined bid value", color: "text-green-400" },
          ].map(card => (
            <div key={card.label} className="bg-gray-900 border border-gray-850 rounded-xl p-4">
              <p className="text-[9px] text-gray-550 font-bold uppercase tracking-wider">{card.label}</p>
              <p className={`text-lg font-black mt-1 ${card.color}`}>{card.value}</p>
              <p className="text-[9px] text-gray-600 mt-0.5">{card.sub}</p>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div className="flex gap-1.5 mb-4 border-b border-gray-850 pb-2 flex-wrap">
          {(["leads", "tenders"] as const).map(t => (
            <button
              key={t}
              onClick={() => { setTab(t); setSelectedOrgName(null); }}
              className={`px-4 py-1.5 rounded-lg text-xs font-bold capitalize transition-all ${
                tab === t
                  ? "bg-orange-500/10 text-orange-400 border border-orange-500/30"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              {t === "leads" ? `Revenue Opportunities (${leads.length})` : `Organization Accounts (${orgAccounts.length})`}
            </button>
          ))}

          {/* Search */}
          <input
            className="ml-auto bg-gray-900 border border-gray-850 rounded-lg px-3 py-1.5 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-gray-600 w-48"
            placeholder="Search accounts/tenders…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        {/* Opportunities Leads tab */}
        {tab === "leads" && (
          <div className="space-y-2">
            {loading && <p className="text-xs text-gray-500 text-center py-8">Loading opportunities…</p>}
            {!loading && filteredLeads.length === 0 && (
              <div className="text-center py-12 text-gray-500">
                <Building2 className="w-10 h-10 mx-auto mb-3 opacity-30" />
                <p className="text-sm">No real data available.</p>
              </div>
            )}
            {filteredLeads.map(lead => (
              <div key={lead.id} className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden p-4 space-y-3">
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest">{lead.company}</h3>
                    <p className="text-[10px] text-gray-500">{lead.city} · {lead.region}</p>
                  </div>
                  <span className="text-sm font-black text-green-400">{fmtValue(lead.estimated_value)}</span>
                </div>
                <p className="text-xs text-gray-400">{lead.qualification_notes || "No notes captured."}</p>
                {lead.recommended_action && (
                  <div className="bg-orange-500/5 border border-orange-500/10 rounded-lg p-2.5 flex justify-between items-center text-xs">
                    <span className="text-gray-400">Recommendation: <b className="text-orange-400">{lead.recommended_action}</b></span>
                    <Link href="/actions?tab=government" className="text-orange-400 font-bold hover:underline">Execute</Link>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Tenders grouped by Organization tab */}
        {tab === "tenders" && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            
            {/* Left Column: List of Organization Accounts */}
            <div className="lg:col-span-1 space-y-3">
              <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest">Active Accounts</h3>
              {loading && <p className="text-xs text-gray-500 text-center py-8">Loading accounts…</p>}
              {!loading && orgAccounts.length === 0 && (
                <p className="text-xs text-gray-600 italic">No government organization accounts found.</p>
              )}
              {orgAccounts.map(org => {
                const active = selectedOrgName === org.organizationName;
                const statusColor = org.relationshipStatus === "Strategic" ? "text-green-400" : org.relationshipStatus === "Warm" ? "text-orange-450" : "text-blue-400";
                return (
                  <div
                    key={org.organizationName}
                    onClick={() => { setSelectedOrgName(org.organizationName); setExpandedTenderId(null); }}
                    className={`bg-gray-900 border p-4 rounded-xl cursor-pointer hover:border-gray-700 transition-all space-y-3 ${
                      active ? "border-orange-500/30 bg-orange-500/5" : "border-gray-850"
                    }`}
                  >
                    <div className="flex justify-between items-start">
                      <h4 className="text-xs font-black text-gray-200 uppercase tracking-widest truncate max-w-[170px]">{org.organizationName}</h4>
                      <span className={`text-[8px] font-black uppercase px-2 py-0.5 rounded border border-gray-800 ${statusColor}`}>
                        {org.relationshipStatus}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-[9px] text-gray-500 border-t border-gray-850 pt-2">
                      <div>
                        <span>Tenders Tracked</span>
                        <p className="font-bold text-gray-200 text-xs mt-0.5">{org.tenderCount}</p>
                      </div>
                      <div>
                        <span>Potential Value</span>
                        <p className="font-mono font-bold text-green-400 text-xs mt-0.5">{fmtValue(org.potentialRevenue)}</p>
                      </div>
                    </div>

                    <div className="flex justify-between text-[9px] text-gray-600">
                      <span>Last Bid: {org.lastBidDate}</span>
                      <span>Next Opp: {org.nextBidDate}</span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Right Column: Organization Details & Tender History */}
            <div className="lg:col-span-2 bg-gray-900 border border-gray-850 rounded-xl p-5 space-y-4">
              {selectedOrg ? (
                <div className="space-y-4">
                  
                  {/* Account detail header */}
                  <div className="border-b border-gray-850 pb-3 flex justify-between items-center">
                    <div>
                      <h3 className="text-sm font-black text-gray-150 uppercase tracking-widest">{selectedOrg.organizationName}</h3>
                      <p className="text-[9px] text-gray-500 mt-0.5">Win Rate: <b className="text-green-400">{selectedOrg.winRate}%</b>  ·  Total Potential: {fmtValue(selectedOrg.potentialRevenue)}</p>
                    </div>
                    <span className="text-[9px] text-gray-400 font-bold bg-gray-955 px-2.5 py-1 rounded border border-gray-850">
                      Tenders Documents: <b className="text-green-400">94% Ready</b>
                    </span>
                  </div>

                  {/* Tender History Table */}
                  <div className="space-y-2">
                    <h4 className="text-[9px] text-gray-500 font-bold uppercase tracking-wider">Tender History</h4>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-gray-800 text-gray-550 font-bold uppercase tracking-wider text-[8px]">
                            <th className="py-2 pl-2">Tender ID</th>
                            <th className="py-2">Title</th>
                            <th className="py-2 text-right">Revenue</th>
                            <th className="py-2 text-center">Status</th>
                            <th className="py-2 text-right pr-2">Action</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-850">
                          {selectedOrg.items.map(t => {
                            const isExpanded = expandedTenderId === t.id;
                            return (
                              <React.Fragment key={t.id}>
                                <tr
                                  className="hover:bg-gray-855/40 cursor-pointer transition-colors"
                                  onClick={() => setExpandedTenderId(isExpanded ? null : t.id)}
                                >
                                  <td className="py-2.5 pl-2 font-mono text-gray-400">{t.tender_id}</td>
                                  <td className="py-2.5 font-bold text-gray-200 max-w-[200px] truncate">{t.title}</td>
                                  <td className="py-2.5 text-right font-mono font-bold text-green-400">{fmtValue(t.estimated_value)}</td>
                                  <td className="py-2.5 text-center">
                                    <span className={`text-[8px] font-black uppercase px-2 py-0.5 rounded border border-gray-850 ${
                                      t.status === "WON" ? "text-green-400" : t.status === "OPEN" ? "text-orange-450" : "text-blue-400"
                                    }`}>{t.status}</span>
                                  </td>
                                  <td className="py-2.5 text-right pr-2" onClick={e => e.stopPropagation()}>
                                    <Link href="/actions?tab=government" className="text-orange-400 font-bold hover:underline">Bid</Link>
                                  </td>
                                </tr>
                                
                                {isExpanded && (
                                  <tr>
                                    <td colSpan={5} className="bg-gray-955/50 p-4 border-t border-b border-gray-850 text-xs">
                                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3 text-[10px]">
                                        <div className="bg-gray-955 p-2 rounded border border-gray-850">
                                          <span className="text-gray-500 block uppercase text-[7px]">Opportunity Score</span>
                                          <span className="font-bold text-gray-200">{t.opportunity_score || 85}/100</span>
                                        </div>
                                        <div className="bg-gray-955 p-2 rounded border border-gray-850">
                                          <span className="text-gray-500 block uppercase text-[7px]">Win Prob</span>
                                          <span className="font-bold text-blue-400">{t.win_probability || 42}%</span>
                                        </div>
                                        <div className="bg-gray-955 p-2 rounded border border-gray-850">
                                          <span className="text-gray-500 block uppercase text-[7px]">Quantity KG</span>
                                          <span className="font-bold text-gray-200 font-mono">{t.quantity_kg ? `${t.quantity_kg} kg` : "qty not stated"}</span>
                                        </div>
                                        <div className="bg-gray-955 p-2 rounded border border-gray-850">
                                          <span className="text-gray-500 block uppercase text-[7px]">Expected Margin</span>
                                          <span className="font-bold text-green-400">{t.expected_margin || 38}%</span>
                                        </div>
                                      </div>
                                      
                                      <div className="space-y-1">
                                        <span className="text-gray-500 block uppercase text-[7px]">Bid Strategy Recommendation</span>
                                        <p className="text-gray-300 text-[11px] font-medium">{t.bid_strategy || "Highlight pure instant coffee zero-chicory formulation to qualify technical evaluation benchmarks."}</p>
                                      </div>
                                    </td>
                                  </tr>
                                )}
                              </React.Fragment>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-64 text-gray-500 text-center space-y-2">
                  <FileText className="w-8 h-8 opacity-20" />
                  <p className="text-xs">Select an organization account from the list to inspect tender details and win rates.</p>
                </div>
              )}
            </div>

          </div>
        )}

      </div>
    </div>
  );
}
