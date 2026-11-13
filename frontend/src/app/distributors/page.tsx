"use client";
import React, { useState, useCallback, useEffect, useRef } from "react";
import {
  Search, RefreshCw, CheckCircle, Mail, Phone, MessageCircle,
  UserPlus, Zap, MapPin, Clock, TrendingUp, Package, Filter,
  ChevronRight, X, XCircle, AlertTriangle, Star
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface DiscoveredLead {
  company: string;
  city: string;
  phone: string;
  email: string;
  contact_name: string;
  lead_source: string;
  website: string;
  selected?: boolean;
}

interface CRMLead {
  id: number;
  company: string;
  city: string | null;
  phone: string | null;
  email: string | null;
  whatsapp_number: string | null;
  contact_name: string | null;
  status: string;
  estimated_annual_value: number;
  lead_source: string | null;
  phone_verified?: boolean;
  phone_source?: string | null;
  contact_search_status?: "SEARCHING" | "VERIFIED" | "NOT_FOUND";
}

interface BuyLead {
  buyer_name: string;
  city: string;
  product_needed: string;
  quantity: string;
  posted_ago: string;
  phone: string;
  source: string;
  source_url: string;
  urgency: "HIGH" | "MEDIUM" | "LOW";
  captured?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_META: Record<string, { label: string; color: string; step: number }> = {
  DISCOVERED:       { label: "Discovered",    color: "text-gray-400 border-gray-600",       step: 0 },
  COLD:             { label: "Cold",           color: "text-blue-400 border-blue-500/40",    step: 1 },
  INTRO_EMAIL_SENT: { label: "Email Sent",     color: "text-amber-400 border-amber-500/40",  step: 2 },
  EMAIL_SENT:       { label: "Email Sent",     color: "text-amber-400 border-amber-500/40",  step: 2 },
  WHATSAPP_SENT:    { label: "WhatsApp",       color: "text-green-400 border-green-500/40",  step: 3 },
  CALL_DONE:        { label: "Called",         color: "text-purple-400 border-purple-500/40",step: 4 },
  ORDER_WON:        { label: "Won",            color: "text-emerald-400 border-emerald-500/40", step: 5 },
  QUALIFIED:        { label: "Qualified",      color: "text-sky-400 border-sky-500/40",      step: 3 },
};

const URGENCY_META = {
  HIGH:   { color: "bg-red-500/15 text-red-400 border-red-500/40",    icon: "🔥", label: "Urgent" },
  MEDIUM: { color: "bg-amber-500/15 text-amber-400 border-amber-500/40", icon: "⚡", label: "Active" },
  LOW:    { color: "bg-gray-700 text-gray-400 border-gray-600",        icon: "📋", label: "Browsing" },
};

const SOURCE_META: Record<string, { color: string; short: string }> = {
  "IndiaMart":      { color: "bg-orange-500/15 text-orange-400 border-orange-500/40", short: "IM" },
  "TradeIndia":     { color: "bg-blue-500/15 text-blue-400 border-blue-500/40",       short: "TI" },
  "ExportersIndia": { color: "bg-purple-500/15 text-purple-400 border-purple-500/40", short: "EI" },
  "GlobalLinker":   { color: "bg-teal-500/15 text-teal-400 border-teal-500/40",       short: "GL" },
  "JustDial":       { color: "bg-yellow-500/15 text-yellow-500 border-yellow-500/40", short: "JD" },
};

const PIPELINE_STEPS = ["Discovered", "Cold", "Email", "WhatsApp", "Called", "Won"];

function Toast({ msg, type, onClose }: { msg: string; type: "ok" | "err" | "info"; onClose: () => void }) {
  const colors = { ok: "border-green-500/40 bg-green-500/10 text-green-300", err: "border-red-500/40 bg-red-500/10 text-red-300", info: "border-amber-500/40 bg-amber-500/10 text-amber-300" };
  useEffect(() => { const t = setTimeout(onClose, 3500); return () => clearTimeout(t); }, [onClose]);
  return (
    <div className={`fixed bottom-4 right-4 z-50 flex items-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-semibold shadow-2xl ${colors[type]}`}>
      {msg}
      <button onClick={onClose} className="ml-1 opacity-60 hover:opacity-100"><X className="w-3 h-3" /></button>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function DistributorsPage() {
  const [activeTab, setActiveTab] = useState<"find" | "demand">("find");

  // ── Tab 1: Find Distributors ─────────────────────────────────────────────
  const [searchCity, setSearchCity] = useState("abohar");
  const [searchSegment, setSearchSegment] = useState("distributor");
  const [searchRadius, setSearchRadius] = useState("local");
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredLead[]>([]);
  const [approving, setApproving] = useState(false);
  const [sendEmail, setSendEmail] = useState(true);
  const [crmLeads, setCrmLeads] = useState<CRMLead[]>([]);
  const [crmLoading, setCrmLoading] = useState(true);
  const [findingEmails, setFindingEmails] = useState(false);
  const [quarantining, setQuarantining]   = useState(false);
  const [emailResults, setEmailResults] = useState<Record<number, { email: string; confidence: string; emailed: boolean }>>({});
  const cancelEmailRef = useRef(false);

  // ── Tab 2: Urgent Buy Orders ────────────────────────────────────────────
  const [buyProduct, setBuyProduct] = useState("instant coffee Nescafe Bru Davidoff Tata Coffee Sleepy Owl Rage");
  const [buyLeads, setBuyLeads] = useState<BuyLead[]>([]);
  const [buyLoading, setBuyLoading] = useState(false);
  const [urgencyFilter, setUrgencyFilter] = useState<"ALL" | "HIGH" | "MEDIUM" | "LOW">("ALL");
  const [capturingId, setCapturingId] = useState<number | null>(null);

  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" | "info" } | null>(null);
  const showToast = (msg: string, type: "ok" | "err" | "info" = "info") => setToast({ msg, type });

  // ── Load CRM leads ───────────────────────────────────────────────────────
  const loadCrm = useCallback(async () => {
    setCrmLoading(true);
    try {
      const res = await fetch("/api/v1/b2b/leads");
      if (res.ok) {
        const data = await res.json();
        setCrmLeads(Array.isArray(data) ? data : data.leads ?? []);
      }
      // Silent progressive verification: queue the next unsearched batch
      // for web-wide contact enrichment. Fire-and-forget.
      fetch("/api/v1/b2b/leads/verify-contacts-backfill", { method: "POST" }).catch(() => {});
    } finally {
      setCrmLoading(false);
    }
  }, []);

  useEffect(() => { loadCrm(); }, [loadCrm]);

  // ── Discover leads ───────────────────────────────────────────────────────
  const runDiscovery = async () => {
    setDiscovering(true);
    setDiscovered([]);
    try {
      const res = await fetch("/api/v1/discovery/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          segment: searchSegment,
          cities: [searchCity],
          radius: searchRadius,
          save: false,
        }),
      });
      if (!res.ok) throw new Error("Discovery failed");
      const data = await res.json();
      const leads: DiscoveredLead[] = (data.leads ?? []).map((l: DiscoveredLead) => ({ ...l, selected: true }));
      setDiscovered(leads);
      if (leads.length === 0) showToast("No leads found — try a different city or segment", "info");
      else showToast(`Found ${leads.length} leads in ${searchCity}`, "ok");
    } catch {
      showToast("Discovery error — check backend connection", "err");
    } finally {
      setDiscovering(false);
    }
  };

  const toggleSelect = (i: number) => {
    setDiscovered(prev => prev.map((l, idx) => idx === i ? { ...l, selected: !l.selected } : l));
  };

  const selectAll = (val: boolean) => setDiscovered(prev => prev.map(l => ({ ...l, selected: val })));

  // ── Approve batch ────────────────────────────────────────────────────────
  const approveSelected = async () => {
    const selected = discovered.filter(l => l.selected);
    if (selected.length === 0) { showToast("Select at least one lead", "info"); return; }
    setApproving(true);
    try {
      const res = await fetch("/api/v1/discovery/approve-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ leads: selected, send_intro_email: sendEmail }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(`Approve failed: ${data?.detail ?? res.status}`, "err");
        return;
      }
      showToast(`Saved ${data.inserted ?? selected.length} leads to CRM${data.drafted ? ` · ${data.drafted} drafts queued for approval` : ""} · contacts verifying in background`, "ok");
      // Always clear the discovery panel after successful approval
      setDiscovered([]);
      loadCrm();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "network error";
      showToast(`Approve failed: ${msg}`, "err");
    } finally {
      setApproving(false);
    }
  };

  // ── CRM pipeline actions ─────────────────────────────────────────────────
  const markAction = async (leadId: number, action: "mark-whatsapp" | "mark-called" | "mark-won") => {
    try {
      const res = await fetch(`/api/v1/b2b/leads/${leadId}/${action}`, { method: "POST" });
      if (!res.ok) throw new Error();
      const labels = { "mark-whatsapp": "WhatsApp sent", "mark-called": "Marked as called", "mark-won": "Marked as Won!" };
      showToast(labels[action], "ok");
      loadCrm();
    } catch {
      showToast("Update failed", "err");
    }
  };

  const sendIntroEmail = async (lead: CRMLead) => {
    if (!lead.email) { showToast("No email on file — use 'Find Emails' first", "info"); return; }
    try {
      const res = await fetch("/api/v1/b2b/distributor-campaign/send-one", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to_email: lead.email,
          to_name:  lead.contact_name || "",
          city:     lead.city || "your city",
          subject:  `Partnership Opportunity — Pure Pantry Provisions × ${lead.company}`,
          body:
            `Dear ${lead.contact_name || "[Contact Name]"},\n\n` +
            `I'm Hiten Jain from Pure Pantry Provisions. We are authorised distributors of premium ` +
            `instant coffee brands including Nescafe, Bru Gold, Davidoff, Tata Coffee, Sleepy Owl and Rage Coffee.\n\n` +
            `We are expanding our distribution network in ${lead.city || "your region"} and believe ` +
            `${lead.company} would be an excellent partner.\n\n` +
            `We offer:\n` +
            `- Competitive margins (28%+2% cash discount on MRP)\n` +
            `- Fast replenishment — same-day dispatch from our Abohar warehouse\n` +
            `- Full marketing support\n\n` +
            `Would you be open to a 10-minute call this week?\n\n` +
            `Warm regards,\nHiten Jain\nPure Pantry Provisions\nconnect@purepantryprovisions.com`,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err?.detail || "send failed");
      }
      showToast(`Email sent to ${lead.company}`, "ok");
      loadCrm();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "unknown error";
      showToast(`Email failed: ${msg}`, "err");
    }
  };

  // ── Find emails + send intro (batched, 5 per request) ───────────────────
  const findEmailsAndSend = async (sendAfter: boolean) => {
    // "Find Emails": only leads without an email
    // "Find + Send Intro": leads WITH email but not yet emailed (Cold/Discovered)
    const notEmailedStatuses = ["COLD", "DISCOVERED", ""];
    const ids = sendAfter
      ? crmLeads
          .filter(l => l.email && notEmailedStatuses.some(s => (l.status || "").toUpperCase().includes(s) || s === ""))
          .filter(l => !["INTRO_EMAIL_SENT","EMAIL_SENT","WHATSAPP_SENT","CALL_DONE","ORDER_WON"].includes((l.status || "").toUpperCase()))
          .map(l => l.id)
          .slice(0, 50)
      : crmLeads.filter(l => !l.email).map(l => l.id).slice(0, 50);

    if (!ids.length) {
      showToast(
        sendAfter
          ? "No leads ready to send — either all already emailed or no email on file"
          : "All visible leads already have emails on file",
        "info"
      );
      return;
    }
    setFindingEmails(true);
    const BATCH = 5;
    const map: Record<number, { email: string; confidence: string; emailed: boolean }> = {};
    let totalFound = 0, totalSent = 0, done = 0;

    cancelEmailRef.current = false;
    showToast(`Searching emails for ${ids.length} leads (${Math.ceil(ids.length / BATCH)} batches)…`, "info");

    const fetchBatch = async (body: object) => {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 25000); // 25s per batch
      try {
        const res = await fetch("/api/v1/b2b/leads/find-emails", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: ctrl.signal,
        });
        return res;
      } finally {
        clearTimeout(timer);
      }
    };

    try {
      for (let i = 0; i < ids.length; i += BATCH) {
        if (cancelEmailRef.current) { showToast("Cancelled", "info"); break; }
        const batchIds = ids.slice(i, i + BATCH);
        showToast(`Batch ${Math.floor(i / BATCH) + 1} / ${Math.ceil(ids.length / BATCH)} — searching…`, "info");

        let res: Response;
        try {
          res = await fetchBatch({ lead_ids: batchIds, send_after_find: sendAfter, limit: BATCH });
        } catch {
          showToast(`Batch ${Math.floor(i / BATCH) + 1} timed out — skipping`, "info");
          continue;
        }
        if (!res.ok) { showToast(`Batch ${Math.floor(i / BATCH) + 1} failed — skipping`, "info"); continue; }
        const data = await res.json();

        for (const r of data.results) {
          map[r.lead_id] = { email: r.email, confidence: r.confidence, emailed: r.emailed };
        }
        totalFound += data.emails_found ?? 0;
        totalSent  += data.emails_sent  ?? 0;
        done       += batchIds.length;

        // Update the UI incrementally after each batch
        setEmailResults(prev => ({ ...prev, ...map }));
      }

      showToast(
        sendAfter
          ? `Done — found ${totalFound} emails · sent intro to ${totalSent} leads`
          : `Done — found ${totalFound} emails across ${done} leads`,
        "ok"
      );
      loadCrm();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "unknown error";
      showToast(`Email search failed: ${msg}`, "err");
    } finally {
      setFindingEmails(false);
    }
  };

  // ── Clean fabricated data (V1.1: real data only) ─────────────────────────
  const quarantineFabricated = async () => {
    setQuarantining(true);
    showToast("Scanning for placeholder phones and auto-generated emails…", "info");
    try {
      const res = await fetch("/api/v1/b2b/leads/quarantine-fabricated", { method: "POST" });
      if (!res.ok) throw new Error();
      const data = await res.json();
      showToast(
        data.quarantined > 0
          ? `✓ ${data.quarantined} leads cleaned — real contacts will be found automatically after discovery`
          : "No fabricated contact data found",
        "ok"
      );
      loadCrm();
    } catch {
      showToast("Cleanup failed — check backend", "err");
    } finally {
      setQuarantining(false);
    }
  };

  // ── Buy leads (Tab 2) ────────────────────────────────────────────────────
  const loadBuyLeads = useCallback(async (product = buyProduct) => {
    setBuyLoading(true);
    try {
      const res = await fetch(`/api/v1/discovery/buy-leads?product=${encodeURIComponent(product)}`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      setBuyLeads(data.leads ?? []);
    } catch {
      showToast("Could not load buy leads", "err");
    } finally {
      setBuyLoading(false);
    }
  }, [buyProduct]);

  useEffect(() => { if (activeTab === "demand") loadBuyLeads(); }, [activeTab, loadBuyLeads]);

  const captureBuyLead = async (lead: BuyLead, idx: number) => {
    setCapturingId(idx);
    try {
      const res = await fetch("/api/v1/discovery/approve-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          leads: [{
            company: lead.buyer_name,
            city: lead.city,
            phone: lead.phone,
            email: "",
            contact_name: "",
            lead_source: lead.source,
            website: lead.source_url,
            segment: "distributor",
          }],
          send_intro_email: false,
        }),
      });
      if (!res.ok) throw new Error();
      const data = await res.json();
      if (data.inserted > 0) {
        showToast(`${lead.buyer_name} added to CRM`, "ok");
        setBuyLeads(prev => prev.map((l, i) => i === idx ? { ...l, captured: true } : l));
      } else {
        showToast("Already in CRM", "info");
        setBuyLeads(prev => prev.map((l, i) => i === idx ? { ...l, captured: true } : l));
      }
    } catch {
      showToast("Capture failed", "err");
    } finally {
      setCapturingId(null);
    }
  };

  const filteredBuyLeads = urgencyFilter === "ALL" ? buyLeads : buyLeads.filter(l => l.urgency === urgencyFilter);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-[1400px] mx-auto px-4 py-5">

        {/* Page heading */}
        <div className="mb-5 flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-amber-500/15 flex items-center justify-center">
            <TrendingUp className="w-5 h-5 text-amber-400" />
          </div>
          <div>
            <h1 className="text-lg font-black text-gray-100">Distributor Pipeline</h1>
            <p className="text-xs text-gray-500">Find leads · Approve · Email → WhatsApp → Call → Win</p>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-gray-900 p-1 rounded-xl w-fit">
          {([ ["find", "Find Distributors", "🔍"], ["demand", "Urgent Buy Orders", "🔥"] ] as const).map(([id, label, emoji]) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === id
                  ? "bg-amber-500/20 text-amber-400 border border-amber-500/40"
                  : "text-gray-400 hover:text-gray-100"
              }`}
            >
              {emoji} {label}
            </button>
          ))}
        </div>

        {/* ── TAB 1: FIND DISTRIBUTORS ── */}
        {activeTab === "find" && (
          <div className="space-y-6">

            {/* Search form */}
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
              <h2 className="text-sm font-black text-gray-200 mb-4 flex items-center gap-2">
                <Search className="w-4 h-4 text-amber-400" /> Find Distributor Leads
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div>
                  <label className="text-[10px] text-gray-500 font-bold uppercase mb-1 block">City</label>
                  <input
                    value={searchCity}
                    onChange={e => setSearchCity(e.target.value)}
                    placeholder="Mumbai, Delhi..."
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-amber-500/50"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-bold uppercase mb-1 block">Category</label>
                  <select
                    value={searchSegment}
                    onChange={e => setSearchSegment(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-amber-500/50"
                  >
                    <optgroup label="Consumes instant coffee">
                      <option value="corporate_office">Corporate Office</option>
                      <option value="corporate_pantry">Corporate Pantry</option>
                      <option value="industrial_canteen">Industrial Canteen</option>
                      <option value="catering_contractor">Catering Contractor</option>
                      <option value="facility_management">Facility Management</option>
                      <option value="hospital">Hospital</option>
                      <option value="education_mess">College / Hostel Mess</option>
                      <option value="hotel_canteen">Hotel / Resort</option>
                      <option value="guest_house">Guest House</option>
                      <option value="govt_canteen">Govt / PSU / Railway Canteen</option>
                      <option value="event_catering">Event Caterer</option>
                    </optgroup>
                    <optgroup label="Resells instant coffee">
                      <option value="distributor">Distributor</option>
                      <option value="wholesaler">Wholesaler</option>
                      <option value="kirana_store">Kirana / General Store</option>
                      <option value="retail_chain">Supermarket / Retail Chain</option>
                      <option value="corporate_gifting">Corporate Gifting</option>
                    </optgroup>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-bold uppercase mb-1 block">Radius</label>
                  <select
                    value={searchRadius}
                    onChange={e => setSearchRadius(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-amber-500/50"
                  >
                    <option value="local">Local (50 km)</option>
                    <option value="punjab">Punjab</option>
                    <option value="north">North India</option>
                    <option value="national">National</option>
                    <option value="all">Pan-India</option>
                  </select>
                </div>
                <div className="flex items-end">
                  <button
                    onClick={runDiscovery}
                    disabled={discovering}
                    className="w-full flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-400 disabled:opacity-60 text-gray-950 font-black text-sm px-4 py-2 rounded-lg transition-all"
                  >
                    {discovering ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                    {discovering ? "Searching..." : "Find Now"}
                  </button>
                </div>
              </div>

              {/* Discovery results */}
              {discovered.length > 0 && (
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-bold text-gray-300">{discovered.filter(l => l.selected).length}/{discovered.length} selected</span>
                    <div className="flex items-center gap-3">
                      <button onClick={() => selectAll(true)} className="text-[10px] text-amber-400 hover:text-amber-300 font-bold">Select All</button>
                      <button onClick={() => selectAll(false)} className="text-[10px] text-gray-500 hover:text-gray-300 font-bold">None</button>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 mb-4">
                    {discovered.map((lead, i) => (
                      <div
                        key={i}
                        onClick={() => toggleSelect(i)}
                        className={`p-3 rounded-xl border cursor-pointer transition-all ${
                          lead.selected
                            ? "border-amber-500/50 bg-amber-500/8"
                            : "border-gray-700 bg-gray-800/50"
                        }`}
                      >
                        <div className="flex items-start gap-2">
                          <div className={`w-4 h-4 mt-0.5 rounded border-2 flex items-center justify-center shrink-0 transition-all ${lead.selected ? "bg-amber-500 border-amber-500" : "border-gray-600"}`}>
                            {lead.selected && <span className="text-gray-950 text-[9px] font-black">✓</span>}
                          </div>
                          <div className="min-w-0">
                            <p className="text-xs font-bold text-gray-100 truncate">{lead.company}</p>
                            <p className="text-[10px] text-gray-500 flex items-center gap-1 mt-0.5">
                              <MapPin className="w-2.5 h-2.5" /> {lead.city}
                            </p>
                            {lead.phone && <p className="text-[10px] text-green-400 mt-0.5 font-mono">{lead.phone}</p>}
                            <p className="text-[9px] text-gray-600 mt-0.5">{lead.lead_source}</p>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="flex items-center gap-3 pt-3 border-t border-gray-800">
                    <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={sendEmail}
                        onChange={e => setSendEmail(e.target.checked)}
                        className="accent-amber-500"
                      />
                      Draft intro emails for the Approval Inbox (leads with email on file)
                    </label>
                    <div className="flex-1" />
                    <button
                      onClick={approveSelected}
                      disabled={approving || discovered.filter(l => l.selected).length === 0}
                      className="flex items-center gap-2 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white font-bold text-xs px-5 py-2 rounded-lg transition-all"
                    >
                      {approving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <UserPlus className="w-3.5 h-3.5" />}
                      {approving ? "Saving..." : `Approve ${discovered.filter(l => l.selected).length} Leads`}
                    </button>
                    <button onClick={() => setDiscovered([])} className="text-xs text-gray-500 hover:text-gray-300 font-bold">
                      Clear
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* CRM Pipeline */}
            <div>
              <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <h2 className="text-sm font-black text-gray-200 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-amber-400" /> CRM Pipeline
                  {!crmLoading && <span className="text-gray-500 font-normal">({crmLeads.length} leads)</span>}
                </h2>
                <div className="flex items-center gap-2">
                  {/* V1.1: contact enrichment + email verification run silently
                      after discovery — no founder buttons needed for data hygiene */}
                  <button
                    onClick={() => findEmailsAndSend(false)}
                    disabled={findingEmails || crmLoading}
                    className="flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-400 hover:bg-blue-500/25 disabled:opacity-50 transition-all"
                  >
                    {findingEmails ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Mail className="w-3 h-3" />}
                    {findingEmails ? "Searching…" : "Find Emails"}
                  </button>
                  <button
                    onClick={() => findEmailsAndSend(true)}
                    disabled={findingEmails || crmLoading}
                    className="flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-400 hover:bg-amber-500/25 disabled:opacity-50 transition-all"
                  >
                    {findingEmails ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
                    {findingEmails ? "Working…" : "Find + Draft Intro"}
                  </button>
                  <button
                    onClick={quarantineFabricated}
                    disabled={quarantining || crmLoading}
                    className="flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-red-500/15 border border-red-500/40 text-red-400 hover:bg-red-500/25 disabled:opacity-50 transition-all"
                    title="Remove placeholder phones (+91-88888…) and auto-generated emails — real data only"
                  >
                    {quarantining ? <RefreshCw className="w-3 h-3 animate-spin" /> : <XCircle className="w-3 h-3" />}
                    {quarantining ? "Cleaning…" : "Clean Fake Data"}
                  </button>
                  {findingEmails && (
                    <button
                      onClick={() => { cancelEmailRef.current = true; }}
                      className="flex items-center gap-1.5 text-xs font-bold px-3 py-1.5 rounded-lg bg-red-500/15 border border-red-500/40 text-red-400 hover:bg-red-500/25 transition-all"
                    >
                      Stop
                    </button>
                  )}
                  <button onClick={loadCrm} className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1">
                    <RefreshCw className="w-3 h-3" /> Refresh
                  </button>
                </div>
              </div>

              {crmLoading ? (
                <div className="text-center py-12 text-gray-600 text-sm">Loading CRM...</div>
              ) : crmLeads.length === 0 ? (
                <div className="text-center py-12 text-gray-600 text-sm">No leads yet — run discovery above to find your first distributor leads.</div>
              ) : (
                <div className="space-y-2">
                  {crmLeads.map(lead => {
                    const meta = STATUS_META[lead.status] ?? STATUS_META["COLD"];
                    const step = meta.step;
                    return (
                      <div key={lead.id} className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition-all">
                        <div className="flex items-start gap-3">

                          {/* Left: info */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                              <span className="text-sm font-black text-gray-100">{lead.company}</span>
                              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${meta.color}`}>
                                {meta.label}
                              </span>
                              {emailResults[lead.id] && (
                                <span className={`text-[9px] font-black px-1.5 py-0.5 rounded border ${
                                  emailResults[lead.id].emailed                                  ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/40" :
                                  emailResults[lead.id].confidence === "HIGH"                    ? "bg-blue-500/15 text-blue-400 border-blue-500/40" :
                                  emailResults[lead.id].confidence === "MEDIUM"                  ? "bg-blue-500/10 text-blue-300 border-blue-500/30" :
                                  emailResults[lead.id].confidence === "GUESSED"                 ? "bg-gray-700 text-gray-400 border-gray-600" :
                                  "bg-red-500/10 text-red-400 border-red-500/20"
                                }`}>
                                  {emailResults[lead.id].emailed                 ? "✉ Draft queued" :
                                   emailResults[lead.id].confidence === "HIGH"   ? `✉ ${emailResults[lead.id].email}` :
                                   emailResults[lead.id].confidence === "MEDIUM" ? `~ ${emailResults[lead.id].email}` :
                                   emailResults[lead.id].confidence === "GUESSED"? `? ${emailResults[lead.id].email}` :
                                   "✗ No email found"}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-3 text-[10px] text-gray-500 flex-wrap">
                              {lead.city && <span className="flex items-center gap-1"><MapPin className="w-2.5 h-2.5" />{lead.city}</span>}
                              {/* V1.1: only web-verified contacts are ever shown */}
                              {lead.phone && (
                                <span className="flex items-center gap-1 text-green-400 font-mono" title={lead.phone_source ? `Confirmed via ${lead.phone_source}` : undefined}>
                                  <Phone className="w-2.5 h-2.5" />{lead.phone}
                                  <span className="text-[8px] text-green-500">✓</span>
                                </span>
                              )}
                              {lead.email && <span className="flex items-center gap-1 text-blue-400"><Mail className="w-2.5 h-2.5" />{lead.email} <span className="text-[8px] text-blue-500">✓</span></span>}
                              {!lead.phone && !lead.email && lead.contact_search_status === "SEARCHING" && (
                                <span className="flex items-center gap-1 text-gray-500 italic"><RefreshCw className="w-2.5 h-2.5 animate-spin" />searching web for contacts…</span>
                              )}
                              {!lead.phone && !lead.email && lead.contact_search_status === "NOT_FOUND" && (
                                <span className="text-gray-600 italic">no verified contact found on web</span>
                              )}
                              {lead.estimated_annual_value > 0 && (
                                <span className="text-amber-400">Rs.{(lead.estimated_annual_value / 1000).toFixed(0)}k/yr</span>
                              )}
                            </div>

                            {/* Pipeline progress bar */}
                            <div className="mt-2 flex items-center gap-1">
                              {PIPELINE_STEPS.map((s, i) => (
                                <div key={s} className="flex items-center gap-1">
                                  <div className={`w-5 h-1.5 rounded-full transition-all ${i <= step ? "bg-amber-500" : "bg-gray-700"}`} />
                                  {i < PIPELINE_STEPS.length - 1 && <ChevronRight className="w-2 h-2 text-gray-700 shrink-0" />}
                                </div>
                              ))}
                              <span className="text-[9px] text-gray-600 ml-1">{PIPELINE_STEPS[step]}</span>
                            </div>
                          </div>

                          {/* Right: action buttons */}
                          <div className="flex flex-col gap-1.5 shrink-0">
                            {step < 2 && (
                              <button
                                onClick={() => sendIntroEmail(lead)}
                                className="flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-blue-500/15 text-blue-400 border border-blue-500/30 hover:bg-blue-500/25 transition-all"
                              >
                                <Mail className="w-3 h-3" /> Email
                              </button>
                            )}
                            {step >= 2 && step < 3 && (
                              <a
                                href={`https://wa.me/${(lead.whatsapp_number || lead.phone || "").replace(/[^0-9]/g, "")}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={() => markAction(lead.id, "mark-whatsapp")}
                                className="flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25 transition-all"
                              >
                                <MessageCircle className="w-3 h-3" /> WhatsApp
                              </a>
                            )}
                            {step >= 3 && step < 4 && (
                              <a
                                href={`tel:${lead.phone || ""}`}
                                onClick={() => markAction(lead.id, "mark-called")}
                                className="flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-purple-500/15 text-purple-400 border border-purple-500/30 hover:bg-purple-500/25 transition-all"
                              >
                                <Phone className="w-3 h-3" /> Call
                              </a>
                            )}
                            {step >= 4 && step < 5 && (
                              <button
                                onClick={() => markAction(lead.id, "mark-won")}
                                className="flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 transition-all"
                              >
                                <Star className="w-3 h-3" /> Mark Won
                              </button>
                            )}
                            {step === 5 && (
                              <span className="flex items-center gap-1.5 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-emerald-500/15 text-emerald-400">
                                <CheckCircle className="w-3 h-3" /> Won
                              </span>
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
        )}

        {/* ── TAB 2: URGENT BUY ORDERS ── */}
        {activeTab === "demand" && (
          <div className="space-y-5">

            {/* Controls */}
            <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex-1 min-w-[200px]">
                  <label className="text-[10px] text-gray-500 font-bold uppercase mb-1 block">Product / Category</label>
                  <input
                    value={buyProduct}
                    onChange={e => setBuyProduct(e.target.value)}
                    placeholder="Nescafe, Bru, Davidoff, instant coffee jars..."
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-amber-500/50"
                  />
                </div>
                <div className="flex items-end">
                  <button
                    onClick={() => loadBuyLeads(buyProduct)}
                    disabled={buyLoading}
                    className="flex items-center gap-2 bg-amber-500 hover:bg-amber-400 disabled:opacity-60 text-gray-950 font-black text-sm px-5 py-2 rounded-lg transition-all"
                  >
                    {buyLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    {buyLoading ? "Scanning..." : "Scan Pan-India"}
                  </button>
                </div>

                {/* Urgency filter */}
                <div className="flex items-center gap-1 ml-auto">
                  <Filter className="w-3.5 h-3.5 text-gray-500" />
                  {(["ALL", "HIGH", "MEDIUM", "LOW"] as const).map(u => (
                    <button
                      key={u}
                      onClick={() => setUrgencyFilter(u)}
                      className={`text-[10px] font-bold px-2.5 py-1 rounded-lg border transition-all ${
                        urgencyFilter === u
                          ? "bg-amber-500/20 text-amber-400 border-amber-500/40"
                          : "text-gray-500 border-gray-700 hover:text-gray-300"
                      }`}
                    >
                      {u === "ALL" ? "All" : URGENCY_META[u].icon + " " + URGENCY_META[u].label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Stats row */}
              {buyLeads.length > 0 && (
                <div className="mt-4 grid grid-cols-3 gap-3">
                  {(["HIGH", "MEDIUM", "LOW"] as const).map(u => {
                    const count = buyLeads.filter(l => l.urgency === u).length;
                    const m = URGENCY_META[u];
                    return (
                      <div key={u} className={`rounded-xl border px-3 py-2 ${m.color}`}>
                        <p className="text-lg font-black">{count}</p>
                        <p className="text-[10px] font-bold opacity-80">{m.icon} {m.label} leads</p>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Buy leads grid */}
            {buyLoading ? (
              <div className="text-center py-16 text-gray-600 text-sm">
                <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-3 text-amber-500" />
                Scanning IndiaMart for buy orders across India...
              </div>
            ) : filteredBuyLeads.length === 0 ? (
              <div className="text-center py-16 text-gray-600 text-sm">
                No buy leads found. Click &quot;Scan Pan-India&quot; to load demand signals.
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {filteredBuyLeads.map((lead, i) => {
                  const urg = URGENCY_META[lead.urgency];
                  return (
                    <div key={i} className={`bg-gray-900 border rounded-2xl p-4 flex flex-col gap-3 transition-all ${lead.captured ? "border-green-500/40" : "border-gray-800 hover:border-gray-700"}`}>

                      {/* Header */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-black text-gray-100 truncate">{lead.buyer_name}</p>
                          <p className="text-[10px] text-gray-500 flex items-center gap-1 mt-0.5">
                            <MapPin className="w-2.5 h-2.5" /> {lead.city}
                          </p>
                        </div>
                        <span className={`text-[9px] font-black px-2 py-0.5 rounded-full border shrink-0 ${urg.color}`}>
                          {urg.icon} {urg.label}
                        </span>
                      </div>

                      {/* Product / requirement */}
                      <div className="bg-gray-800 rounded-xl px-3 py-2">
                        <p className="text-[10px] text-gray-400 leading-relaxed">{lead.product_needed}</p>
                      </div>

                      {/* Meta row */}
                      <div className="flex items-center gap-2 flex-wrap text-[10px] text-gray-500">
                        <span className="flex items-center gap-1"><Package className="w-2.5 h-2.5" />{lead.quantity}</span>
                        <span className="flex items-center gap-1"><Clock className="w-2.5 h-2.5" />{lead.posted_ago}</span>
                        {(() => {
                          const srcKey = lead.source.replace(" (Demo)", "").replace(" Buy Leads", "");
                          const sm = SOURCE_META[srcKey];
                          return sm ? (
                            <span className={`px-1.5 py-0.5 rounded border text-[9px] font-black ${sm.color}`}>{srcKey}</span>
                          ) : (
                            <span className="text-gray-700">{srcKey}</span>
                          );
                        })()}
                      </div>

                      {/* Phone (if available) */}
                      {lead.phone && (
                        <a href={`tel:${lead.phone}`} className="text-[10px] text-green-400 font-mono flex items-center gap-1 hover:text-green-300">
                          <Phone className="w-3 h-3" /> {lead.phone}
                        </a>
                      )}

                      {/* Actions */}
                      <div className="flex gap-2 mt-auto">
                        {lead.captured ? (
                          <div className="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-[10px] font-bold text-green-400">
                            <CheckCircle className="w-3.5 h-3.5" /> Added to CRM
                          </div>
                        ) : (
                          <button
                            onClick={() => captureBuyLead(lead, i)}
                            disabled={capturingId === i}
                            className="flex-1 flex items-center justify-center gap-1.5 bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/40 text-amber-400 text-[10px] font-bold py-2 rounded-lg transition-all disabled:opacity-60"
                          >
                            {capturingId === i ? <RefreshCw className="w-3 h-3 animate-spin" /> : <UserPlus className="w-3 h-3" />}
                            {capturingId === i ? "Capturing..." : "Capture Lead"}
                          </button>
                        )}
                        {lead.source_url && lead.source_url !== "undefined" && (
                          <a
                            href={lead.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 text-[10px] font-bold px-3 py-2 rounded-lg border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 transition-all"
                          >
                            View <ChevronRight className="w-3 h-3" />
                          </a>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Source note */}
            {buyLeads.length > 0 && (
              <div className="flex items-start gap-2 px-4 py-3 rounded-xl border border-gray-800 bg-gray-900/50 text-[10px] text-gray-600">
                <AlertTriangle className="w-3.5 h-3.5 text-gray-600 shrink-0 mt-0.5" />
  Data sourced live from IndiaMart, TradeIndia, ExportersIndia, GlobalLinker and JustDial. Phone numbers may be masked on some portals — click View to open the original listing. Capture Lead adds the buyer to your CRM pipeline for follow-up.
              </div>
            )}
          </div>
        )}
      </div>

      {toast && <Toast msg={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
