"use client";
import React, { useState, useMemo, useEffect, useCallback } from "react";
import {
  Check, X, RefreshCw, Flame, TrendingUp, Clock, AlertTriangle, Send, MessageCircle, Phone, Zap,
  Eye, ArrowRight, Building2, Globe, Link2, ShieldCheck, FileText, HelpCircle, ChevronDown, ChevronUp,
  Search, Filter, MapPin, Sparkles, History, User, DollarSign, Layers, Calendar, Landmark
} from "lucide-react";

interface DiscoveryResults {
  buckets?: Record<string, B2BLead[]>;
  stats?: Record<string, number>;
  progress?: string[];
}

interface HubBucket {
  name: string;
  leads: B2BLead[];
  margin: number;
  count: number;
  isActiveSearch: boolean;
  cityPattern: string;
}

interface B2BLead {
  id: number;
  company: string;
  division: string;
  industry?: string;
  email_opens?: number;
  email_verified?: boolean;
  city: string;
  pincode?: string;
  state?: string;
  status: string;
  score: number;
  estimated_value: number;
  contact_name: string;
  contact_title: string;
  email: string;
  phone: string;
  // Stored values, independent of the verification gate that governs `email`
  // and `phone`. The gate decides what may be acted on, not what is shown.
  email_on_file?: string;
  phone_on_file?: string;
  // True when the money figure is the category average rather than a number
  // derived from anything known about this specific business.
  revenue_is_category_default?: boolean;
  revenue_basis?: string;
  whatsapp_number?: string;
  website?: string;
  linkedin?: string;
  lead_source: string;
  recommended_action?: string;
  contact_persona?: string;
  probability?: number;
  expected_monthly_consumption_kg?: number;
  email_approved_by_founder?: boolean;
  email_rejected_by_founder?: boolean;
  phone_verified?: boolean;
  qualification_notes?: string;
  email_verification_status?: string;
  email_confidence?: number;
  lead_temperature_tier?: string;
  lead_temperature_score?: number;
  decision_maker?: string;
  call_summary?: string;
  call_transcript?: string;
  sample_taste?: number;
  sample_aroma?: number;
  address?: string;
  // Real Intelligence Layer assessment, served by GET /b2b/leads.
  buying_score?: number;
  buying_confidence?: number;
  commercial_fit?: number;
  opportunity_score?: number;
  classification?: "HOT" | "WARM" | "COLD" | "REJECT";
  intelligence_reasoning?: string;
  buying_evidence?: string[];
  // False until Truth Layer evidence collection has actually run for this
  // lead. Distinct from classification — a freshly discovered lead is
  // "not yet assessed", never "rejected".
  evidence_collected?: boolean;
  // Explicit evaluation lifecycle. Only COMPLETE means classification can be
  // trusted; NOT_STARTED/COLLECTING/SCORING mean "still assessing", FAILED
  // means the pipeline errored and the lead needs a retry.
  intelligence_status?: "NOT_STARTED" | "COLLECTING" | "SCORING" | "COMPLETE" | "FAILED";
  provenance?: Record<string, string>;
  contactability?: Record<string, string>;
  expected_margin_rs?: number;
  coffee_buying_score?: number;
  coffee_buying_evidence?: string;
  distance_from_origin?: number;
  origin_city?: string;
  searched_category?: string;
  division_confidence?: number;
  division_verified?: boolean;
  engagement_level?: string;
  switching_signal?: { signal: string; evidence: string; source: string; date: string; };
  segment_performance?: { label: string; rate: string; observations: number; };
  priority_score?: number;
  value_type?: string;
  next_action?: string;
  value?: number;
}

interface CompletedEvent {
  event_type: string;
  actor: string;
  channel: string;
  before_status: string;
  after_status: string;
  payload: { company?: string; [key: string]: unknown };
  created_at: string;
}

// One step of a lead's journey as proven by the server's event log.
// state: "done" = an event records it happening | "failed" = a failure event was
// recorded (detail carries the real error) | "pending" = no evidence yet.
interface StageStep {
  label: string;
  state: "done" | "failed" | "pending";
  detail?: string;
}

// ── Business Memory ──────────────────────────────────────────────────────────
// What the founder learned on a call, kept permanently against the business.
// Mirrors InteractionRequest / _build_summary in backend/app/api/endpoints.py.

interface BusinessSummary {
  decision_maker?: string | null;
  designation?: string | null;
  current_supplier?: string | null;
  current_brand?: string | null;
  monthly_consumption_kg?: number | null;
  budget_range?: string | null;
  preferred_contact_time?: string | null;
  preferred_contact_method?: string | null;
  last_interaction?: { at?: string | null; method?: string; outcome?: string | null; remark?: string } | null;
  open_tasks?: string[];
  risks?: string[];
  next_best_action?: string | null;
  interactions_recorded?: number;
}

interface MemoryTimelineEntry {
  type: string;
  at?: string | null;
  channel?: string | null;
  detail?: string | null;
}

interface BusinessMemory {
  summary: BusinessSummary;
  timeline: MemoryTimelineEntry[];
}

interface InteractionForm {
  method: string;
  outcome: string;
  decision_maker: string;
  designation: string;
  current_supplier: string;
  monthly_consumption_kg: string;
  budget_range: string;
  preferred_contact_time: string;
  preferred_contact_method: string;
  next_followup_date: string;
  remark: string;
}

// Everything blank on purpose. An unanswered field stays unknown — it is never
// pre-filled with a guess, because a guess written here becomes a "fact" the
// drafts and call briefs will repeat back as though the founder confirmed it.
const BLANK_INTERACTION: InteractionForm = {
  method: "founder_call", outcome: "", decision_maker: "", designation: "",
  current_supplier: "", monthly_consumption_kg: "", budget_range: "",
  preferred_contact_time: "", preferred_contact_method: "",
  next_followup_date: "", remark: "",
};

const INTERACTION_METHODS = [
  ["founder_call", "Founder call"], ["whatsapp", "WhatsApp"], ["email", "Email"],
  ["meeting", "Meeting"], ["visit", "Visit"], ["sample", "Sample"],
  ["proposal", "Proposal"], ["order", "Order"], ["other", "Other"],
];

const INTERACTION_OUTCOMES = [
  ["interested", "Interested"], ["not_interested", "Not interested"],
  ["callback", "Asked to call back"], ["no_answer", "No answer"],
  ["wrong_number", "Wrong number"], ["gatekeeper", "Blocked by gatekeeper"],
  ["sample_requested", "Sample requested"], ["quote_requested", "Quote requested"],
];

interface CityCategory {
  category: string;
  label: string;
  leads: number;
  phone_reachable: number;
  email_sendable: number;
  email_on_file_unverified: number;
  no_contact: number;
  drafts_awaiting_approval: number;
  already_contacted: number;
  examples: string[];
}
interface CityBreakdown {
  city: string;
  leads: number;
  categories: CityCategory[];
  totals?: { phone_reachable: number; email_sendable: number;
             drafts_awaiting_approval: number; no_contact: number };
  note?: string;
}

interface ApprovalCenterSectionProps {
  leads: B2BLead[];
  completedEvents: CompletedEvent[];
  onRefresh: () => void;
  approvalInbox?: {
    leads?: Array<{
      lead_id: number;
      draft_id: number;
      preview?: {
        subject?: string;
        body?: string;
      };
    }>;
    total?: number;
  } | null;
}

function rs(n: number) {
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(2) + " Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(1) + " L";
  if (n >= 1e3) return "₹" + Math.round(n / 1e3) + " K";
  return "₹" + Math.round(n);
}

// Maps divisions to clean display categories
const CATEGORIES = [
  { id: "distributor", label: "Distributor", pattern: ["distributor"] },
  { id: "wholesaler", label: "Wholesaler", pattern: ["wholesaler"] },
  { id: "modern_trade", label: "Modern Trade", pattern: ["modern_trade"] },
  { id: "supermarket", label: "Supermarket", pattern: ["supermarket"] },
  { id: "grocery_chain", label: "Grocery Chain", pattern: ["grocery_chain"] },
  { id: "retail_kirana", label: "Retail / Kirana", pattern: ["retail_kirana"] },
  { id: "corporate_office", label: "Corporate Office", pattern: ["corporate_office"] },
  { id: "office_pantry", label: "Office Pantry", pattern: ["office_pantry"] },
  { id: "manufacturing", label: "Manufacturing", pattern: ["manufacturing"] },
  { id: "facility_management", label: "Facility Management", pattern: ["facility_management"] },
  { id: "hotel", label: "Hotel", pattern: ["hotel"] },
  { id: "restaurant", label: "Restaurant", pattern: ["restaurant"] },
  { id: "cafe", label: "Cafe", pattern: ["cafe"] },
  { id: "hospital", label: "Hospital", pattern: ["hospital"] },
  { id: "school", label: "School", pattern: ["school"] },
  { id: "college", label: "College", pattern: ["college"] },
  { id: "government", label: "Government", pattern: ["government"] },
  { id: "corporate_gifting", label: "Corporate Gifting", pattern: ["corporate_gifting"] },
  { id: "private_label", label: "Private Label", pattern: ["private_label"] },
  { id: "exporter", label: "Exporter", pattern: ["exporter"] },
  { id: "institutional_buyer", label: "Institutional Buyer", pattern: ["institutional_buyer"] },
  { id: "needs_reclassification", label: "Needs Reclassification", pattern: ["needs_reclassification"] },
  { id: "unknown", label: "Unknown Category", pattern: ["unknown"] }
];

const DEMAND_CATEGORIES = [
  { id: "distributor", label: "Distributors" },
  { id: "wholesaler", label: "Wholesalers" },
  { id: "modern_trade", label: "Modern Trade" },
  { id: "supermarket", label: "Supermarkets" },
  { id: "grocery_chain", label: "Grocery Chains" },
  { id: "retail_kirana", label: "Retail / Kirana Stores" },
  { id: "corporate_office", label: "Corporate Offices" },
  { id: "office_pantry", label: "Office Pantry Providers" },
  { id: "manufacturing", label: "Manufacturing Facilities" },
  { id: "facility_management", label: "Facility Management Services" },
  { id: "hotel", label: "Hotels & Lodges" },
  { id: "restaurant", label: "Restaurants" },
  { id: "cafe", label: "Cafés" },
  { id: "hospital", label: "Hospitals & Clinics" },
  { id: "school", label: "Schools" },
  { id: "college", label: "Colleges" },
  { id: "government", label: "Government & PSUs" },
  { id: "corporate_gifting", label: "Corporate Gifting Partners" },
  { id: "private_label", label: "Private Label Clients" },
  { id: "exporter", label: "Exporters" },
  { id: "institutional_buyer", label: "Institutional Buyers" },
];

export default function ApprovalCenterSection({ leads, completedEvents, onRefresh, approvalInbox }: ApprovalCenterSectionProps) {
  // Integrated Discovery inputs
  const [searchCity, setSearchCity] = useState("");
  const [searchPincode, setSearchPincode] = useState("");
  const [searchState, setSearchState] = useState("");
  const [searchRadius, setSearchRadius] = useState("50");
  const [searchCoverage, setSearchCoverage] = useState("regional");
  const [searchCategory, setSearchCategory] = useState("");
  const [filterMethod, setFilterMethod] = useState("ALL");
  const [sortBy, setSortBy] = useState("best_conversion");
  const [showBatchReview, setShowBatchReview] = useState(false);

  const [searchQuery, setSearchQuery] = useState("");
  const [editedDrafts, setEditedDrafts] = useState<Record<number, { subject: string; body: string }>>({});
  const [editedLeads, setEditedLeads] = useState<Record<number, { contact_name: string; email: string; phone: string }>>({});
  const [savingLeadId, setSavingLeadId] = useState<number | null>(null);
  const [savingDraftIds, setSavingDraftIds] = useState<Set<number>>(new Set());
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedRegion, setSelectedRegion] = useState<string | null>(null);
  const [expandedOpportunityId, setExpandedOpportunityId] = useState<number | null>(null);
  const [selectedOpportunityForMemory, setSelectedOpportunityForMemory] = useState<B2BLead | null>(null);
  const [memory, setMemory] = useState<BusinessMemory | null>(null);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [interaction, setInteraction] = useState<InteractionForm>(BLANK_INTERACTION);
  const [savingInteraction, setSavingInteraction] = useState(false);
  const [busyLeads, setBusyLeads] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  // Custom Mark Won splits modal
  const [showMarkWonModal, setShowMarkWonModal] = useState(false);
  const [markWonLeadId, setMarkWonLeadId] = useState<number | null>(null);
  const [markWonCompanyName, setMarkWonCompanyName] = useState("");
  const [markWonOrderValue, setMarkWonOrderValue] = useState<number>(0);
  const [splitDiscovery, setSplitDiscovery] = useState(25);
  const [splitOutreach, setSplitOutreach] = useState(35);
  const [splitProposal, setSplitProposal] = useState(20);
  const [splitClosing, setSplitClosing] = useState(20);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryResults, setDiscoveryResults] = useState<DiscoveryResults | null>(null);
  const [currentProgressIndex, setCurrentProgressIndex] = useState<number>(-1);
  const [progressLogs, setProgressLogs] = useState<string[]>([]);
  const [activeBucket, setActiveBucket] = useState<string>("READY_NOW");
  const [activeCategoryFilter, setActiveCategoryFilter] = useState<string>("ALL");
  // Category-wise breakdown for the searched city — persistent, not a toast.
  const [cityBreakdown, setCityBreakdown] = useState<CityBreakdown | null>(null);
  const loadCityBreakdown = useCallback(async (city: string) => {
    const c = (city || "").trim();
    if (!c) { setCityBreakdown(null); return; }
    try {
      const r = await fetch(`/api/v1/b2b/city/${encodeURIComponent(c)}/categories`);
      setCityBreakdown(r.ok ? await r.json() : null);
    } catch { setCityBreakdown(null); }
  }, []);
  // Show the breakdown for whatever city is in the box, not only after a fresh
  // prospecting run — the founder searches a city to SEE what is already there.
  useEffect(() => {
    const t = setTimeout(() => loadCityBreakdown(searchCity), 400);
    return () => clearTimeout(t);
  }, [searchCity, loadCityBreakdown]);
  // Find contact details for a category's unreachable businesses (no phone,
  // no email). Scoped to that category so scraping is not wasted on businesses
  // we can already reach.
  const [enriching, setEnriching] = useState<string | null>(null);
  const enrichNoContact = async (city: string, category: string) => {
    setEnriching(category);
    try {
      const r = await fetch(
        `/api/v1/b2b/city/${encodeURIComponent(city)}/enrich-no-contact?category=${encodeURIComponent(category)}`,
        { method: "POST" });
      const d = await r.json();
      if (r.ok) {
        showToast(d.contact_found
          ? `${city} · ${category}: found contact for ${d.contact_found} of ${d.no_contact}`
          : `${city} · ${category}: no contact found for the ${d.no_contact} unreachable — left as-is`);
        loadCityBreakdown(city);
        onRefresh();
      } else {
        showToast(d.detail || "Enrichment failed.");
      }
    } catch {
      showToast("Network error running enrichment.");
    } finally {
      setEnriching(null);
    }
  };

  // Real per-lead execution status, proven by the event log (see getStageChecklist).
  const [journeys, setJourneys] = useState<Record<number, StageStep[]>>({});
  const loadJourneys = useCallback(() => {
    fetch("/api/v1/b2b/journeys")
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d?.journeys) setJourneys(d.journeys); })
      .catch(() => {});
  }, []);
  useEffect(() => {
    loadJourneys();
    // Keep the checkmarks honest as workflows progress, and refresh right after
    // the founder acts (approve / send / mark-won all fire dashboard:refresh).
    const t = setInterval(loadJourneys, 120_000);
    window.addEventListener("dashboard:refresh", loadJourneys);
    return () => {
      clearInterval(t);
      window.removeEventListener("dashboard:refresh", loadJourneys);
    };
  }, [loadJourneys]);

  // Advanced filters state
  const [filterVerifiedEmail, setFilterVerifiedEmail] = useState(false);
  const [filterDMFound, setFilterDMFound] = useState(false);
  const [filterHighRevenue, setFilterHighRevenue] = useState(false);
  const [filterHighMargin, setFilterHighMargin] = useState(false);
  const [filterPendingApproval, setFilterPendingApproval] = useState(false);
  const [filterNeedsFounderCall, setFilterNeedsFounderCall] = useState(false);
  const [filterGovernmentOnly, setFilterGovernmentOnly] = useState(false);

  const showToast = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(null), 4000);
  };

  // SINGLE source of truth for "can this lead actually be approved for email?".
  // Used by every count, total and bulk-approve selection so numbers shown are
  // always numbers that will really send. A lead with no address is skipped by
  // the backend guard, so counting it would promise sends that never happen.
  const isEmailable = (l: B2BLead) =>
    l.status === "DISCOVERED" && !l.email_approved_by_founder && !!(l.email || "").trim();

  // Discovered but not yet emailable — still waiting on auto-warm enrichment.
  const isAwaitingEnrichment = (l: B2BLead) =>
    l.status === "DISCOVERED" && !(l.email || "").trim();

  const hasPhone = (l: B2BLead) => !!((l.phone || l.whatsapp_number || "")).trim();
  const hasEmail = (l: B2BLead) => !!(l.email || "").trim();

  // Evaluation lifecycle helpers. isEvaluated gates everything that reads the
  // classification: a score is only meaningful once intelligence_status is
  // COMPLETE. Anything else (or a legacy row where the field is absent but
  // evidence_collected is true) is treated conservatively.
  const isEvaluated = (l: B2BLead) =>
    l.intelligence_status === "COMPLETE" ||
    (l.intelligence_status === undefined && l.evidence_collected === true);
  const isAssessing = (l: B2BLead) =>
    !isEvaluated(l) && l.intelligence_status !== "FAILED";

  // SINGLE source of truth for the phone channels, mirroring the backend's
  // _sequence_for(): a lead with no email is on the phone-first ladder, where
  // WhatsApp is the OPENING written touch rather than a day-3 follow-up.
  //
  // These previously required status === "EMAIL_SENT" for WhatsApp and
  // "WHATSAPP_SENT" for calls, which hardcoded the email-first chain into the
  // UI. A lead with no address can never reach EMAIL_SENT, so every
  // phone-only lead was permanently unreachable: WhatsApp showed 0 pending
  // forever and AI calls sat behind it. In Abohar that hid 92 leads that have
  // real, verified phone numbers.
  const isWhatsAppReady = (l: B2BLead) =>
    hasPhone(l) && (
      l.status === "EMAIL_SENT" ||                                  // email-first: day-3 follow-up
      (!hasEmail(l) && ["DISCOVERED", "QUALIFIED", "COLD"].includes(l.status || ""))  // phone-first: opening touch
    );

  const isCallReady = (l: B2BLead) =>
    hasPhone(l) && l.status === "WHATSAPP_SENT";

  // Helper to map UI category ID to backend segments
  const mapCategoryToSegment = (cat: string) => {
    const c = cat.toLowerCase();
    if (c.includes("distributor") || c.includes("wholesale")) return "distributor";
    if (c.includes("hotel") || c.includes("restaurant") || c.includes("cafe") || c.includes("horeca")) return "horeca";
    if (c.includes("corporate") || c.includes("office") || c.includes("pantry")) return "corporate_pantry";
    if (c.includes("grocery") || c.includes("supermarket") || c.includes("retail")) return "grocery";
    if (c.includes("government") || c.includes("psu")) return "government";
    if (c.includes("gifting") || c.includes("event")) return "gifting";
    if (c.includes("industrial") || c.includes("manufacturing")) return "private_label";
    // No silent default. An unset category used to fall through to "gifting",
    // so every area search from the dashboard prospected gifting companies
    // whatever the founder meant — and nothing on screen said so.
    return "";
  };

  // Run prospecting search on backend
  const handleRunDiscovery = async (mode: "existing" | "discover" | "intelligent" = "intelligent") => {
    if (!searchCity && !searchState && !searchPincode) {
      showToast("Please specify a city, state, or pincode to prospect leads.");
      return;
    }

    const segment = mapCategoryToSegment(searchCategory);
    setDiscovering(true);
    setDiscoveryResults(null);
    setCurrentProgressIndex(0);
    setProgressLogs([
      mode === "existing" ? "Searching state leads..." : (mode === "intelligent" ? "Intelligently scanning CRM..." : "Searching businesses..."),
      "Enforcing Result Category...",
      "Checking switching signals...",
      "Resolving contactability...",
      "Ranking conversion priority...",
      "Outreach Queues Ready."
    ]);

    // Animate search steps for transparency
    const progressInterval = setInterval(() => {
      setCurrentProgressIndex((prev) => {
        if (prev < 4) return prev + 1;
        return prev;
      });
    }, 450);

    let citiesToSend = undefined;
    if (searchCity) {
      citiesToSend = [searchCity];
    } else if (searchPincode) {
      citiesToSend = [searchPincode];
    }

    let radiusToSend = "punjab";
    if (searchCity && searchRadius === "5") {
      radiusToSend = "local";
    } else if (searchState && searchState.toLowerCase() !== "punjab") {
      radiusToSend = "national";
    }

    try {
      const res = await fetch("/api/v1/discovery/run", {
        method: "POST",
        headers: { "Type": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          segment,
          cities: citiesToSend,
          radius: radiusToSend,
          save: true,
          state: searchState || undefined,
          method: filterMethod === "ALL" ? "EMAIL" : filterMethod,
          search_mode: mode,
          sort_by: sortBy
        })
      });
      const data = await res.json();
      clearInterval(progressInterval);
      
      if (res.ok) {
        // Complete the animation
        setProgressLogs(data.progress || []);
        setCurrentProgressIndex(5);
        setDiscoveryResults(data);
        
        const label = searchCity || searchPincode || searchState || "Selected region";
        showToast(`${label}: Found ${data.stats?.email_ready ?? 0} email-ready opportunities.`);
        onRefresh();
        if (searchCity) {
          loadCityBreakdown(searchCity);
        }
      } else {
        showToast(data.detail || "Lead discovery failed.");
        setCurrentProgressIndex(-1);
      }
    } catch {
      clearInterval(progressInterval);
      showToast("Network error running discovery search.");
      setCurrentProgressIndex(-1);
    } finally {
      setDiscovering(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleRunDiscovery("intelligent");
    }
  };

  // ── Business Memory ──
  const loadMemory = useCallback(async (leadId: number) => {
    setMemoryLoading(true);
    try {
      const [sum, mem] = await Promise.all([
        fetch(`/api/v1/b2b/business/${leadId}/summary`).then(r => (r.ok ? r.json() : null)),
        fetch(`/api/v1/b2b/business/${leadId}/memory`).then(r => (r.ok ? r.json() : null)),
      ]);
      setMemory({ summary: sum?.summary ?? {}, timeline: mem?.timeline ?? [] });
    } catch {
      setMemory(null);
    } finally {
      setMemoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedOpportunityForMemory) {
      setInteraction(BLANK_INTERACTION);
      loadMemory(selectedOpportunityForMemory.id);
    } else {
      setMemory(null);
    }
  }, [selectedOpportunityForMemory, loadMemory]);

  const handleRecordInteraction = async () => {
    const lead = selectedOpportunityForMemory;
    if (!lead) return;
    // A remark or an outcome is the minimum — an empty row records nothing and
    // would only inflate the interaction count.
    if (!interaction.remark.trim() && !interaction.outcome) {
      showToast("Add what happened, or pick an outcome, before saving.");
      return;
    }
    setSavingInteraction(true);
    try {
      // Only send fields the founder actually filled in. Empty strings are
      // dropped rather than written as blanks over what is already known.
      const body: Record<string, unknown> = { method: interaction.method, created_by: "FOUNDER" };
      (Object.keys(interaction) as Array<keyof InteractionForm>).forEach(k => {
        if (k === "method") return;
        const v = interaction[k].trim();
        if (!v) return;
        body[k] = k === "monthly_consumption_kg" ? parseFloat(v) : v;
      });
      if (interaction.outcome) body.interested = interaction.outcome === "interested" ? true
        : interaction.outcome === "not_interested" ? false : null;

      const res = await fetch(`/api/v1/b2b/business/${lead.id}/interaction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const d = await res.json();
        const n = (d.promoted_to_lead || []).length;
        showToast(n ? `Interaction saved — ${n} fact${n > 1 ? "s" : ""} now on the record.`
                    : "Interaction saved.");
        setInteraction(BLANK_INTERACTION);
        await loadMemory(lead.id);
        onRefresh();
      } else {
        const d = await res.json().catch(() => ({}));
        showToast(d.detail || "Could not save the interaction.");
      }
    } catch {
      showToast("Network error saving the interaction.");
    } finally {
      setSavingInteraction(false);
    }
  };

  const handleSaveLeadDetails = async (leadId: number) => {
    const edit = editedLeads[leadId];
    if (!edit) return;
    setSavingLeadId(leadId);
    try {
      const res = await fetch(`/api/v1/b2b/leads/${leadId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contact_name: edit.contact_name,
          email: edit.email,
          phone: edit.phone
        })
      });
      if (res.ok) {
        showToast("Lead details updated successfully.");
        onRefresh();
      } else {
        const data = await res.json();
        showToast(data.detail || "Failed to update lead details.");
      }
    } catch {
      showToast("Network error saving lead details.");
    } finally {
      setSavingLeadId(null);
    }
  };

  // ── Unified Journey Approval Action ──
  const handleApproveJourney = async (leadIds: number[]) => {
    if (!leadIds.length) return;
    const newBusy = new Set(busyLeads);
    leadIds.forEach(id => newBusy.add(id));
    setBusyLeads(newBusy);

    try {
      const res = await fetch("/api/v1/b2b/workflow/approve-journey", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_ids: leadIds })
      });
      const data = await res.json();
      if (res.ok) {
        showToast(`Journey Approved: Activated autonomous outreach for ${leadIds.length} opportunities.`);
        onRefresh();
      } else {
        showToast(data.detail || "Journey approval failed.");
      }
    } catch {
      showToast("Network error executing journey approval.");
    } finally {
      const resetBusy = new Set(busyLeads);
      leadIds.forEach(id => resetBusy.delete(id));
      setBusyLeads(resetBusy);
    }
  };

  // Mark lead as won (opens split override dialog)
  const handleMarkWon = (l: B2BLead) => {
    setMarkWonLeadId(l.id);
    setMarkWonCompanyName(l.company);
    setMarkWonOrderValue(l.estimated_value || 0);
    setSplitDiscovery(25);
    setSplitOutreach(35);
    setSplitProposal(20);
    setSplitClosing(20);
    setShowMarkWonModal(true);
  };

  const submitMarkWon = async () => {
    if (!markWonLeadId) return;
    try {
      const res = await fetch(`/api/v1/b2b/leads/${markWonLeadId}/mark-won-with-split`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          order_value_inr: Number(markWonOrderValue),
          overrides: {
            discovery_scoring: Number(splitDiscovery) / 100,
            outreach_channels: Number(splitOutreach) / 100,
            proposal_negotiation: Number(splitProposal) / 100,
            followup_closing: Number(splitClosing) / 100
          }
        })
      });
      if (res.ok) {
        showToast("Deal Won! Commission generated and allocated successfully.");
        setShowMarkWonModal(false);
        onRefresh();
      } else {
        showToast("Failed to mark lead as won.");
      }
    } catch {
      showToast("Network error marking lead as won.");
    }
  };

  const handleVerifyPaymentClick = async (l: B2BLead) => {
    try {
      const res = await fetch(`/api/v1/b2b/leads/${l.id}/payment-verified`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actual_cash: Number(l.estimated_value || 0)
        })
      });
      if (res.ok) {
        showToast("Payment verified! Reinvestment reserves and AI sales force metrics updated.");
        onRefresh();
      } else {
        const err = await res.json();
        showToast(err.detail || "Failed to verify payment.");
      }
    } catch {
      showToast("Network error verifying payment.");
    }
  };

  // Outreach method-wise approvals
  const handleApproveMethod = async (method: "email" | "whatsapp" | "call", targetLeads: B2BLead[]) => {
    const pendingLeads = targetLeads.filter(l =>
      (method === "email" && isEmailable(l)) ||
      (method === "whatsapp" && isWhatsAppReady(l)) ||
      (method === "call" && isCallReady(l))
    );
    if (pendingLeads.length === 0) {
      showToast(`No leads are currently pending/eligible for ${method} action.`);
      return;
    }
    
    try {
      if (method === "email") {
        // Email genuinely sends over SMTP, so bulk approval is real.
        await handleApproveJourney(pendingLeads.map(l => l.id));
      } else if (method === "whatsapp") {
        // wa.me cannot send on our behalf — the founder must press send in
        // WhatsApp per lead. Bulk-marking here used to flag every lead
        // WHATSAPP_SENT while sending nothing, writing founder actions for
        // messages that never went out and corrupting the learned patterns and
        // the margin/founder-hour metrics. Prepare the links; the lead is only
        // marked sent when the founder confirms (Send button on each row).
        showToast(
          `WhatsApp can't be bulk-sent — wa.me needs one manual send per lead. ` +
          `${pendingLeads.length} are ready: use Send on each row, then confirm.`
        );
      } else {
        // Same problem: nothing actually dials. Marking CALL_DONE in bulk would
        // record calls that never happened.
        showToast(
          `AI calls aren't dialled in bulk from here — approve the AI-call script ` +
          `and run calls per lead so only real calls are recorded.`
        );
      }
    } catch {
      showToast("Error executing method-wise approval.");
    }
  };

  const handleSaveDraft = async (draftId: number, leadId: number, subject: string, body: string) => {
    const newSaving = new Set(savingDraftIds);
    newSaving.add(draftId);
    setSavingDraftIds(newSaving);

    try {
      const res = await fetch(`/api/v1/b2b/email/draft/${draftId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, body })
      });
      if (res.ok) {
        showToast("Outreach email draft saved successfully.");
        onRefresh();
      } else {
        const data = await res.json();
        showToast(data.detail || "Failed to save draft.");
      }
    } catch {
      showToast("Network error saving draft changes.");
    } finally {
      const resetSaving = new Set(savingDraftIds);
      resetSaving.delete(draftId);
      setSavingDraftIds(resetSaving);
    }
  };

  // Helper to place leads into their category bucket
  const getLeadCategory = (lead: B2BLead) => {
    const div = (lead.division || "").toLowerCase();
    const ind = (lead.industry || "").toLowerCase();
    for (const cat of CATEGORIES) {
      if (cat.id === "other") continue;
      if (cat.pattern.some(p => div.includes(p) || ind.includes(p))) {
        return cat.id;
      }
    }
    return "other";
  };

  // ── Integrated Filtering logic ──
  const isLeadMatchingMethod = (l: B2BLead, method: string) => {
    const status = (l.status || "").toUpperCase();
    switch (method) {
      case "EMAIL":
        return isEmailable(l) || ["EMAIL_SENT", "REPLIED"].includes(status);
      case "WHATSAPP":
        return isWhatsAppReady(l) || ["WHATSAPP_SENT", "REPLIED"].includes(status);
      case "CALL":
      case "AI_CALL":
      case "FOUNDER_CALL":
        return isCallReady(l) || ["AI_CALL_COMPLETED", "FOUNDER_CALL_COMPLETED"].includes(status);
      case "FOLLOW_UP":
        return ["EMAIL_SENT", "WHATSAPP_SENT"].includes(status);
      case "SAMPLE":
        return ["SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED"].includes(status);
      case "PROPOSAL":
        return status === "PROPOSAL_SENT";
      default:
        return true;
    }
  };

  const filteredLeads = useMemo(() => {
    return leads.filter(l => {
      if (isEvaluated(l) && l.classification === "REJECT") return false;

      // Method filter
      if (filterMethod !== "ALL" && !isLeadMatchingMethod(l, filterMethod)) return false;

      // Category filter
      if (searchCategory) {
        const leadCat = (l.division || "unknown").toLowerCase();
        if (leadCat !== searchCategory.toLowerCase()) return false;
      }

      // Geography: City Match
      if (searchCity) {
        const cMatch = (l.city || "").toLowerCase().includes(searchCity.toLowerCase());
        if (!cMatch) return false;
      }

      // Geography: Pincode Match
      if (searchPincode) {
        const pMatch = (l.pincode || "").includes(searchPincode) || (l.address || "").includes(searchPincode);
        if (!pMatch) return false;
      }

      // Geography: State Match
      if (searchState) {
        const q = searchState.toLowerCase();
        const addressMatch = (l.state || "").toLowerCase().includes(q) ||
                             (l.address || "").toLowerCase().includes(q);
        let sMatch = addressMatch;
        if (!sMatch && q === "punjab") {
          const punjabCities = ["chandigarh", "abohar", "bathinda", "ludhiana", "amritsar", "jalandhar", "patiala", "mohali", "zirakpur", "kharar"];
          if (l.city && punjabCities.includes(l.city.toLowerCase())) {
            sMatch = true;
          }
        }
        if (!sMatch) return false;
      }

      // Text query search
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const matchesSearch =
          (l.company || "").toLowerCase().includes(q) ||
          (l.city || "").toLowerCase().includes(q) ||
          (l.contact_name || "").toLowerCase().includes(q) ||
          (l.email || "").toLowerCase().includes(q) ||
          (l.phone || "").toLowerCase().includes(q) ||
          (l.division || "").toLowerCase().includes(q);
        if (!matchesSearch) return false;
      }

      // Advanced checkboxes
      if (filterVerifiedEmail && !l.email_verified) return false;
      if (filterDMFound && !l.decision_maker) return false;
      if (filterHighRevenue && (l.estimated_value || 0) < 500000) return false;
      if (filterHighMargin && ((l.expected_margin_rs || l.estimated_value * 0.35) < 150000)) return false;
      if (filterPendingApproval && l.status !== "DISCOVERED") return false;
      if (filterNeedsFounderCall && !l.recommended_action?.toLowerCase().includes("call")) return false;
      if (filterGovernmentOnly && (l.division || "").toLowerCase() !== "government") return false;

      return true;
    });
  }, [leads, filterMethod, searchCategory, searchCity, searchPincode, searchState, searchQuery, filterVerifiedEmail, filterDMFound, filterHighRevenue, filterHighMargin, filterPendingApproval, filterNeedsFounderCall, filterGovernmentOnly]);

  // Group filtered leads into their region containers
  const regionGroups = useMemo(() => {
    const groups: Record<string, B2BLead[]> = {};
    filteredLeads.forEach(l => {
      const reg = l.city || "Other Region";
      if (!groups[reg]) groups[reg] = [];
      groups[reg].push(l);
    });
    return groups;
  }, [filteredLeads]);

  const sortedRegions = useMemo(() => {
    const regions = Object.keys(regionGroups);
    const hubsOrder = ["abohar", "ganganagar", "ludhiana"];
    return regions.sort((a, b) => {
      const aLower = a.toLowerCase();
      const bLower = b.toLowerCase();
      const idxA = hubsOrder.findIndex(h => aLower.includes(h));
      const idxB = hubsOrder.findIndex(h => bLower.includes(h));
      if (idxA !== -1 && idxB !== -1) return idxA - idxB;
      if (idxA !== -1) return -1;
      if (idxB !== -1) return 1;
      return regionGroups[b].length - regionGroups[a].length;
    });
  }, [regionGroups]);

  // Leads pending approval matching current filters
  const matchingPendingLeads = useMemo(() => {
    return filteredLeads.filter(isEmailable);
  }, [filteredLeads]);

  const matchingPendingMargin = useMemo(() => {
    return matchingPendingLeads.reduce((s, l) => s + (l.estimated_value * 0.35), 0);
  }, [matchingPendingLeads]);

  // ── Geo-Batch Buckets (Tied Directly to Active search query) ──
  const geoBuckets = useMemo(() => {
    const buckets: HubBucket[] = [];
    const isSearchActive = searchCity || searchState || searchPincode || searchCategory;

    if (isSearchActive) {
      buckets.push({
        name: `Active Search: ${searchCity || searchState || searchPincode || "Query Segment"}`,
        leads: matchingPendingLeads,
        margin: matchingPendingMargin,
        count: matchingPendingLeads.length,
        isActiveSearch: true,
        cityPattern: ""
      });
    }

    const staticHubs = [
      { name: "Abohar Hub", pattern: "abohar" },
      { name: "Sri Ganganagar Hub", pattern: "ganganagar" },
      { name: "Ludhiana Hub", pattern: "ludhiana" }
    ];

    staticHubs.forEach(h => {
      const matches = leads.filter(l =>
        (l.city || "").toLowerCase().includes(h.pattern) &&
        !(isEvaluated(l) && l.classification === "REJECT") && isEmailable(l)
      );
      const margin = matches.reduce((s, l) => s + (l.estimated_value * 0.35), 0);
      buckets.push({
        name: h.name,
        leads: matches,
        margin,
        count: matches.length,
        isActiveSearch: false,
        cityPattern: h.pattern
      });
    });

    return buckets;
  }, [filteredLeads, leads, searchCity, searchState, searchPincode, searchCategory, matchingPendingLeads, matchingPendingMargin]);

  // ── V3 Revenue Discovery Engine Buckets & Priority Queue ──
  const bucketLabels = {
    HIGH_INTENT_FOUNDER_ACTION: "High Intent",
    FOLLOW_UP_DUE: "Follow-Up Due",
    WARM_LEADS: "Warm",
    READY_NOW: "Intro Ready",
    NEEDS_ENRICHMENT: "Need Verification",
    UNREACHABLE: "Unreachable",
    REJECTED: "Do Not Contact",
  };

  const buckets = useMemo(() => {
    const sourceLeads = discoveryResults ? [
      ...(discoveryResults.buckets?.READY_NOW || []),
      ...(discoveryResults.buckets?.NEEDS_ENRICHMENT || []),
      ...(discoveryResults.buckets?.FOLLOW_UP_DUE || []),
      ...(discoveryResults.buckets?.HIGH_INTENT_FOUNDER_ACTION || []),
      ...(discoveryResults.buckets?.WARM_LEADS || []),
      ...(discoveryResults.buckets?.UNREACHABLE || []),
      ...(discoveryResults.buckets?.REJECTED || [])
    ] : filteredLeads;

    const list_founder: B2BLead[] = [];
    const list_followup: B2BLead[] = [];
    const list_warm: B2BLead[] = [];
    const list_ready: B2BLead[] = [];
    const list_enrich: B2BLead[] = [];
    const list_unreachable: B2BLead[] = [];
    const list_rejected: B2BLead[] = [];

    const seenIds = new Set<number>();

    sourceLeads.forEach(l => {
      if (seenIds.has(l.id)) return;
      seenIds.add(l.id);

      const score = l.coffee_buying_score || l.score || 0;
      const div = (l.division || "unknown").toLowerCase();
      
      let fit = "LOW";
      if (score >= 75 || ["distributor", "wholesaler", "supermarket", "modern_trade", "grocery_chain", "cafe"].includes(div)) {
        fit = "HIGH";
      } else if (score >= 45) {
        fit = "MEDIUM";
      } else if (score >= 30) {
        fit = "LOW";
      } else {
        fit = "REJECT";
      }

      const hasEmail = l.email && l.email.includes("@");
      const hasPhone = l.phone && l.phone.trim().length >= 10;
      let cont = "UNREACHABLE";
      if (hasEmail && hasPhone) {
        cont = "EMAIL_PHONE";
      } else if (hasEmail) {
        cont = "EMAIL";
      } else if (hasPhone) {
        cont = "PHONE";
      }

      const stat = (l.status || "DISCOVERED").toUpperCase();
      
      if (fit === "REJECT" || stat === "COLD" || l.classification === "REJECT" || l.email_rejected_by_founder) {
        list_rejected.push(l);
      } else if (["REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED"].includes(stat)) {
        list_founder.push(l);
      } else if (["EMAIL_SENT", "SAMPLE_SENT", "PROPOSAL_SENT"].includes(stat)) {
        list_followup.push(l);
      } else if (l.email_opens && l.email_opens > 1) {
        list_warm.push(l);
      } else if (["EMAIL_PHONE", "EMAIL"].includes(cont) && l.email_verified && ["DISCOVERED", "QUALIFIED"].includes(stat)) {
        list_ready.push(l);
      } else if (["EMAIL_PHONE", "EMAIL"].includes(cont) && !l.email_verified && ["DISCOVERED", "QUALIFIED"].includes(stat)) {
        list_enrich.push(l);
      } else {
        list_unreachable.push(l);
      }
    });

    return {
      HIGH_INTENT_FOUNDER_ACTION: discoveryResults?.buckets?.HIGH_INTENT_FOUNDER_ACTION || list_founder,
      FOLLOW_UP_DUE: discoveryResults?.buckets?.FOLLOW_UP_DUE || list_followup,
      WARM_LEADS: discoveryResults?.buckets?.WARM_LEADS || list_warm,
      READY_NOW: discoveryResults?.buckets?.READY_NOW || list_ready,
      NEEDS_ENRICHMENT: discoveryResults?.buckets?.NEEDS_ENRICHMENT || list_enrich,
      UNREACHABLE: discoveryResults?.buckets?.UNREACHABLE || list_unreachable,
      REJECTED: discoveryResults?.buckets?.REJECTED || list_rejected,
    };
  }, [filteredLeads, discoveryResults]);

  const categoryGroups = useMemo(() => {
    return {
      ALL: filteredLeads,
      DISTRIBUTORS: filteredLeads.filter((l: B2BLead) => ["distributor", "wholesaler"].includes((l.division || "").toLowerCase())),
      KIRANA: filteredLeads.filter((l: B2BLead) => ["retail_kirana", "kirana_store"].includes((l.division || "").toLowerCase())),
      SUPERMARKETS: filteredLeads.filter((l: B2BLead) => ["supermarket", "retail_chain", "modern_trade", "grocery_chain"].includes((l.division || "").toLowerCase())),
      CORPORATES: filteredLeads.filter((l: B2BLead) => ["corporate_office", "office_pantry", "manufacturing", "facility_management"].includes((l.division || "").toLowerCase())),
      HORECA: filteredLeads.filter((l: B2BLead) => ["hotel", "restaurant", "cafe", "horeca"].includes((l.division || "").toLowerCase())),
      INSTITUTIONS: filteredLeads.filter((l: B2BLead) => ["hospital", "school", "college", "govt_canteen"].includes((l.division || "").toLowerCase())),
    };
  }, [filteredLeads]);

  const activeLeadsToDisplay = useMemo(() => {
    const list = buckets[activeBucket as keyof typeof buckets] || [];
    if (activeCategoryFilter === "ALL") return list;
    
    return list.filter(l => {
      const div = (l.division || "").toLowerCase();
      if (activeCategoryFilter === "DISTRIBUTORS") return ["distributor", "wholesaler"].includes(div);
      if (activeCategoryFilter === "KIRANA") return ["retail_kirana", "kirana_store"].includes(div);
      if (activeCategoryFilter === "SUPERMARKETS") return ["supermarket", "retail_chain", "modern_trade", "grocery_chain"].includes(div);
      if (activeCategoryFilter === "CORPORATES") return ["corporate_office", "office_pantry", "manufacturing", "facility_management"].includes(div);
      if (activeCategoryFilter === "HORECA") return ["hotel", "restaurant", "cafe", "horeca"].includes(div);
      if (activeCategoryFilter === "INSTITUTIONS") return ["hospital", "school", "college", "govt_canteen"].includes(div);
      return true;
    });
  }, [buckets, activeBucket, activeCategoryFilter]);

  const priorityQueue = useMemo(() => {
    const activeOpps = [...buckets.READY_NOW, ...buckets.NEEDS_ENRICHMENT, ...buckets.HIGH_INTENT_FOUNDER_ACTION];
    
    const getRankScore = (l: B2BLead) => {
      const score = l.coffee_buying_score || l.score || 0;
      const div = (l.division || "unknown").toLowerCase();
      let fit_mult = 0.4;
      if (score >= 75 || ["distributor", "wholesaler", "supermarket", "modern_trade", "grocery_chain", "cafe"].includes(div)) {
        fit_mult = 1.0;
      } else if (score >= 45) {
        fit_mult = 0.7;
      }
      
      let cont_mult = 0.1;
      const hasEmail = l.email && l.email.includes("@");
      const hasPhone = l.phone && l.phone.trim().length >= 10;
      if (hasEmail && hasPhone) cont_mult = 1.0;
      else if (hasEmail) cont_mult = 0.8;
      else if (hasPhone) cont_mult = 0.6;
      
      return (l.estimated_value || 60000) * fit_mult * cont_mult;
    };

    return activeOpps.sort((a, b) => getRankScore(b) - getRankScore(a)).slice(0, 3);
  }, [buckets]);

  // ── Stage checklist ──
  // Served by GET /b2b/journeys, which derives every step from the immutable
  // event log. It is deliberately NOT computed here: the old client-side version
  // inferred steps from lead.status, so a lead that replied to an EMAIL rendered
  // "WhatsApp Sent ✓" and "AI Call ✓" for things that never happened, and the 180
  // real EMAIL_FAILED events were invisible. Render what the server proves.
  const getStageChecklist = (l: B2BLead) =>
    journeys[l.id] ?? [{ label: "Loading…", state: "pending", detail: "" }];

  return (
    <div id="approval-center" className="space-y-6">
      
      {/* Toast Alert */}
      {toast && (
        <div className="fixed bottom-5 right-5 z-50 bg-gray-900 border border-amber-500/35 text-amber-300 font-bold px-4 py-3 rounded-xl shadow-2xl flex items-center gap-3 animate-in fade-in slide-in-from-bottom-5">
          <Sparkles className="w-4 h-4 text-amber-400 animate-spin" />
          <span className="text-xs">{toast}</span>
        </div>
      )}

      {/* Header Panel */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <span className="text-[8px] text-orange-400 block uppercase font-bold tracking-widest">Revenue Operating Center</span>
          <h2 className="text-base font-black text-white uppercase tracking-widest flex items-center gap-2 mt-1">
            <ShieldCheck className="w-5 h-5 text-orange-400" /> Revenue Workflow Approvals
          </h2>
          <p className="text-[10px] text-gray-500 mt-1">
            Configure, batch-approve, and monitor complete autonomous multi-channel journeys.
          </p>
        </div>

        {/* Dynamic Global KPIs */}
        <div className="flex gap-4">
          <div className="bg-gray-955 border border-green-500/10 px-4 py-2.5 rounded-xl text-center">
            <span className="text-[7px] text-gray-500 uppercase block font-bold">Unapproved Revenue</span>
            <span className="text-sm font-black text-green-400 mt-0.5">
              {rs(filteredLeads.filter(isEmailable).reduce((s, l) => s + l.estimated_value, 0))}
            </span>
          </div>
          <div className="bg-gray-955 border border-amber-500/10 px-4 py-2.5 rounded-xl text-center">
            <span className="text-[7px] text-gray-500 uppercase block font-bold">Unapproved Margin</span>
            <span className="text-sm font-black text-orange-400 mt-0.5">
              {rs(filteredLeads.filter(isEmailable).reduce((s, l) => s + (l.estimated_value * 0.35), 0))}
            </span>
          </div>
        </div>
      </div>

      {/* Dynamic Geo-Batch Buckets (Tied to Search inputs) */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-3.5">
        <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest flex items-center gap-2">
          <MapPin className="w-4 h-4 text-orange-400" /> Geo-Batch Control Center
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {geoBuckets.map((geo, idx) => (
            <div key={idx} className={`border p-4 rounded-xl flex flex-col justify-between space-y-3 transition-all ${geo.isActiveSearch ? "bg-orange-500/5 border-orange-500/25" : "bg-gray-955 border-gray-850"}`}>
              <div>
                <div className="flex justify-between items-center">
                  <h4 className="text-xs font-black text-gray-200 uppercase truncate max-w-[130px]">{geo.name}</h4>
                  {/* "Ready" alone implied the whole hub was ready. The Approve
                      button below sends EMAIL, so the count is email-ready by
                      design — say so, rather than letting a 0 read as "nothing
                      to do here" when the hub is full of WhatsApp-reachable
                      leads. */}
                  <span className="text-[9px] px-2 py-0.5 font-bold rounded-full bg-orange-500/10 text-orange-400 border border-orange-500/20">
                    {geo.count} email-ready
                  </span>
                </div>
                <div className="mt-2 text-xs text-gray-400">
                  Expected Margin: <span className="font-mono text-green-400 font-bold">{rs(geo.margin)}</span>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    if (geo.isActiveSearch) {
                      handleApproveJourney(geo.leads.map(l => l.id));
                    } else if (geo.cityPattern) {
                      setSearchCity(geo.cityPattern);
                    }
                  }}
                  className={`flex-1 py-1.5 text-[9px] font-black uppercase rounded-lg transition-all text-center ${
                    geo.isActiveSearch 
                      ? "bg-orange-500 hover:bg-orange-600 text-gray-955 font-black" 
                      : "bg-gray-900 border border-gray-800 text-gray-400 hover:text-gray-200"
                  }`}
                >
                  {geo.isActiveSearch ? "Approve Active Batch" : "Filter Console"}
                </button>
                {!geo.isActiveSearch && (
                  <button
                    disabled={geo.count === 0}
                    onClick={() => handleApproveJourney(geo.leads.map(l => l.id))}
                    className="px-2 py-1.5 bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border border-orange-500/25 rounded-lg text-[9px] font-bold uppercase transition-all"
                  >
                    Approve
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Integrated Discovery & Search Console */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
        <div>
          <h3 className="text-xs font-black text-gray-200 uppercase tracking-widest font-sans">🔍 Discovery Search Engine & Filters</h3>
          <p className="text-[9px] text-gray-500 mt-0.5">Filter by pincode, city, radius, or state to prospect real leads matching specific segments</p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-3.5 items-end">
          <div>
            <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">City</label>
            <input
              type="text" value={searchCity} onChange={(e) => setSearchCity(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="e.g. Abohar"
              className="w-full bg-gray-955 text-gray-200 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
            />
          </div>
          <div>
            <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">Pincode</label>
            <input
              type="text" value={searchPincode} onChange={(e) => setSearchPincode(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="e.g. 152116"
              className="w-full bg-gray-955 text-gray-200 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
            />
          </div>
          <div>
            <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">Radius</label>
            <select
              value={searchRadius} onChange={(e) => setSearchRadius(e.target.value)}
              className="w-full bg-gray-955 text-gray-300 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none"
            >
              <option value="5">5 km</option>
              <option value="50">50 km</option>
              <option value="100">100 km</option>
            </select>
          </div>
          <div>
            <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">State</label>
            <input
              type="text" value={searchState} onChange={(e) => setSearchState(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="e.g. Punjab"
              className="w-full bg-gray-955 text-gray-200 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
            />
          </div>
          <div>
            <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">Sort Priority</label>
            <select
              value={sortBy} onChange={(e) => setSortBy(e.target.value)}
              className="w-full bg-gray-955 text-gray-300 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
            >
              <option value="best_conversion">Best Next Revenue Action</option>
              <option value="intent">Highest Intent</option>
              <option value="willing_to_switch">Willing to Switch</option>
              <option value="recent">Recently Engaged</option>
              <option value="segment_conversion">Highest Segment Conversion</option>
              <option value="value">Highest Potential Value</option>
              <option value="newest">Newest Opportunity</option>
            </select>
          </div>
        </div>

        {/* Row 2: Category Selector */}
        <div className="grid grid-cols-1 gap-2.5 pt-1">
          <div>
            <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">Category (Default: All Categories Sweep)</label>
            <select
              value={searchCategory}
              onChange={(e) => setSearchCategory(e.target.value)}
              className="w-full bg-gray-955 text-gray-200 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
            >
              <option value="" className="bg-[#0c0d12] text-gray-200">All categories (sweep the city)</option>
              {CATEGORIES.filter(c => c.id !== "other").map(c => (
                <option key={c.id} value={c.id} className="bg-[#0c0d12] text-gray-200">{c.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Row 3: Outreach Method Selector */}
        <div className="space-y-1.5 pt-1">
          <label className="text-[8px] text-gray-400 uppercase font-black block mb-1">Outreach Method</label>
          <div className="flex flex-wrap gap-2">
            {["ALL", "INTRO EMAIL", "WHATSAPP OUTREACH", "FOUNDER CALL", "AI CALL"].map((m) => {
              const active = filterMethod === m || (m === "INTRO EMAIL" && filterMethod === "EMAIL") || (m === "WHATSAPP OUTREACH" && filterMethod === "WHATSAPP");
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    if (m === "INTRO EMAIL") setFilterMethod("EMAIL");
                    else if (m === "WHATSAPP OUTREACH") setFilterMethod("WHATSAPP");
                    else setFilterMethod(m);
                  }}
                  className={`px-3.5 py-1.5 rounded-lg text-[9px] font-bold border transition-all ${
                    active
                      ? "bg-orange-500/10 text-orange-400 border-orange-500/30 font-black"
                      : "border-gray-800 bg-gray-955 text-gray-400 hover:text-gray-205"
                  }`}
                >
                  {m}
                </button>
              );
            })}
          </div>
        </div>

        {/* V3 animated progress search overlay */}
        {discovering && (
          <div className="bg-gray-955 border border-gray-855 rounded-xl p-4 space-y-3.5 mt-4">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-orange-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Revenue Discovery Engine running...
              </span>
              <span className="text-[9px] text-gray-500 font-mono">Sweeping {searchRadius}km area...</span>
            </div>
            <div className="space-y-2">
              {progressLogs.map((step, idx) => {
                const active = idx === currentProgressIndex;
                const done = idx < currentProgressIndex;
                return (
                  <div key={idx} className="flex items-center justify-between text-xs border-b border-gray-900 pb-1.5 last:border-0 last:pb-0">
                    <div className="flex items-center gap-2">
                      <div className={`w-1.5 h-1.5 rounded-full ${
                        active ? "bg-orange-500 animate-ping" : 
                        done ? "bg-green-500" : "bg-gray-800"
                      }`} />
                      <span className={`${active ? "text-gray-200 font-bold" : done ? "text-gray-400" : "text-gray-600"}`}>
                        {step.split("...")[0]}
                      </span>
                    </div>
                    <span className="font-mono text-[10px] text-orange-400 font-bold">
                      {step.split("...")[1] || (active ? "In Progress" : done ? "Done" : "Queued")}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {!discovering && discoveryResults && (
          <div className="bg-gray-955 border border-gray-855 rounded-xl p-4 space-y-3 mt-4">
            <div className="flex items-center justify-between border-b border-gray-800 pb-2">
              <span className="text-[10px] text-green-400 font-bold uppercase tracking-wider">Search Sweep Completed Successfully</span>
              <span className="text-[9px] text-gray-500 font-mono">Found {discoveryResults.stats?.qualified ?? 0} Qualified Leads</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-1.5">
              <div className="bg-gray-900/50 p-2.5 rounded-lg border border-gray-855 text-center">
                <div className="text-[8px] text-gray-500 uppercase font-black">Discovered</div>
                <div className="text-sm font-mono font-bold text-gray-300 mt-1">{discoveryResults.stats?.discovered ?? 0}</div>
              </div>
              <div className="bg-gray-900/50 p-2.5 rounded-lg border border-gray-855 text-center">
                <div className="text-[8px] text-gray-500 uppercase font-black">Email Ready</div>
                <div className="text-sm font-mono font-bold text-orange-400 mt-1">{discoveryResults.stats?.email_ready ?? 0}</div>
              </div>
              <div className="bg-gray-900/50 p-2.5 rounded-lg border border-gray-855 text-center">
                <div className="text-[8px] text-gray-500 uppercase font-black">Phone / WA Ready</div>
                <div className="text-sm font-mono font-bold text-green-400 mt-1">{discoveryResults.stats?.phone_ready ?? 0}</div>
              </div>
              <div className="bg-gray-900/50 p-2.5 rounded-lg border border-gray-855 text-center">
                <div className="text-[8px] text-gray-500 uppercase font-black">High Value (Founder Call)</div>
                <div className="text-sm font-mono font-bold text-blue-400 mt-1">{discoveryResults.stats?.founder_call_priority ?? 0}</div>
              </div>
            </div>
          </div>
        )}
        {/* Text Filter Search & Advanced Toggle */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 pt-3 border-t border-gray-850">
          <div className="relative flex-1 w-full max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search leads by name, email, phone, or title query..."
              className="w-full bg-gray-955 text-xs text-gray-250 pl-9 pr-4 py-1.5 border border-gray-800 rounded-lg focus:outline-none focus:border-orange-500"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto">
            <button
              onClick={() => handleRunDiscovery("intelligent")}
              disabled={discovering}
              className={`px-6 py-2 bg-gradient-to-r from-orange-500 to-red-600 hover:from-orange-600 hover:to-red-700 text-white text-xs font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow-lg border border-orange-400/20 ${discovering ? "opacity-60 cursor-wait animate-pulse" : ""}`}
            >
              {discovering ? (
                <RefreshCw className="w-4 h-4 animate-spin" />
              ) : (
                <Sparkles className="w-4 h-4 fill-current text-yellow-300" />
              )}
              [🔍 FIND BEST OPPORTUNITIES]
            </button>

            {matchingPendingLeads.length > 0 && (
              <button
                onClick={() => handleApproveJourney(matchingPendingLeads.map(l => l.id))}
                className="px-4 py-1.5 bg-orange-500 hover:bg-orange-600 text-gray-955 text-xs font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow-md"
              >
                <Zap className="w-3.5 h-3.5 fill-current" />
                Approve All {matchingPendingLeads.length} Matches (Margin: {rs(matchingPendingMargin)})
              </button>
            )}
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className={`px-3 py-1.5 border rounded-lg text-xs font-bold transition-all flex items-center gap-1.5 ${showAdvanced ? "bg-orange-500/15 border-orange-500/30 text-orange-400" : "bg-gray-955 border-gray-850 text-gray-400 hover:text-gray-200"}`}
            >
              <Filter className="w-3.5 h-3.5" /> Advanced Filters
            </button>
          </div>
        </div>

        {/* Advanced Filters Toggle Drawer */}
        {showAdvanced && (
          <div className="bg-gray-955 border border-gray-850 p-4 rounded-xl grid grid-cols-2 md:grid-cols-4 gap-4 text-[10px] text-gray-400 font-bold uppercase">
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterVerifiedEmail} onChange={(e) => setFilterVerifiedEmail(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              Verified Email Only
            </label>
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterDMFound} onChange={(e) => setFilterDMFound(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              Decision Maker Found
            </label>
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterHighRevenue} onChange={(e) => setFilterHighRevenue(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              High Revenue (&gt;5L)
            </label>
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterHighMargin} onChange={(e) => setFilterHighMargin(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              High Margin (&gt;1.5L)
            </label>
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterPendingApproval} onChange={(e) => setFilterPendingApproval(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              Pending Approvals
            </label>
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterNeedsFounderCall} onChange={(e) => setFilterNeedsFounderCall(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              Needs Founder Call
            </label>
            <label className="flex items-center gap-2 cursor-pointer hover:text-gray-200">
              <input type="checkbox" checked={filterGovernmentOnly} onChange={(e) => setFilterGovernmentOnly(e.target.checked)} className="rounded bg-gray-900 border-gray-800 text-orange-500" />
              Government Only
            </label>
          </div>
        )}
      </div>

      {/* Category-wise breakdown for the searched city — persistent, not a toast.
          Reachability is split by channel because a category can look healthy on
          lead count yet be unworkable: in Abohar 68 leads, 64 phone, 0 email. */}
      {cityBreakdown && cityBreakdown.leads > 0 && (
        <div className="bg-gray-955 border border-gray-850 rounded-2xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-black text-white uppercase tracking-widest">
              {cityBreakdown.city} — {cityBreakdown.leads} businesses by category
            </h3>
            {cityBreakdown.totals && (
              <span className="text-[10px] text-gray-500">
                {cityBreakdown.totals.phone_reachable} phone-reachable ·{" "}
                {cityBreakdown.totals.email_sendable} email-sendable ·{" "}
                {cityBreakdown.totals.drafts_awaiting_approval} awaiting approval
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-gray-500 text-[9px] uppercase tracking-wider text-left">
                  <th className="py-1.5 font-black">Category</th>
                  <th className="py-1.5 font-black text-right">Leads</th>
                  <th className="py-1.5 font-black text-right">Phone</th>
                  <th className="py-1.5 font-black text-right">Email</th>
                  <th className="py-1.5 font-black text-right">Awaiting approval</th>
                  <th className="py-1.5 font-black text-right">No contact</th>
                </tr>
              </thead>
              <tbody>
                {cityBreakdown.categories.map((c) => (
                  <tr key={c.category} className="border-t border-gray-850">
                    <td className="py-2 text-gray-200 font-bold">{c.label}</td>
                    <td className="py-2 text-right font-mono text-gray-200">{c.leads}</td>
                    <td className="py-2 text-right font-mono text-gray-300">{c.phone_reachable}</td>
                    {/* 0 sendable emails is the norm here — dim it so it reads as
                        a real constraint rather than a missing value. */}
                    <td className={`py-2 text-right font-mono ${c.email_sendable ? "text-green-400" : "text-gray-600"}`}>
                      {c.email_sendable}
                    </td>
                    <td className={`py-2 text-right font-mono ${c.drafts_awaiting_approval ? "text-orange-400 font-bold" : "text-gray-600"}`}>
                      {c.drafts_awaiting_approval}
                    </td>
                    <td className="py-2 text-right font-mono">
                      {c.no_contact ? (
                        <button
                          onClick={() => enrichNoContact(cityBreakdown.city, c.category)}
                          disabled={enriching !== null}
                          title={`Find contact details for ${c.no_contact} unreachable ${c.label} business(es)`}
                          className="text-amber-500/90 hover:text-amber-300 underline decoration-dotted underline-offset-2 disabled:opacity-40"
                        >
                          {enriching === c.category ? "finding…" : `${c.no_contact} · enrich`}
                        </button>
                      ) : (
                        <span className="text-gray-600">0</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cityBreakdown.totals?.email_sendable === 0 && (
            <p className="text-[10px] text-amber-500/90 leading-relaxed">
              No sendable email address in {cityBreakdown.city} — phone and WhatsApp are the
              reachable channels here. The drafts awaiting approval are usable as call briefs.
            </p>
          )}
        </div>
      )}

      {/* Region-Grouped Panels */}\n      {/* V3 Revenue Discovery Engine Unified View */}
      <div className="space-y-6">
        
        {/* Founder Priority Queue Card */}
        {priorityQueue.length > 0 && (
          <div className="bg-gradient-to-r from-orange-500/10 via-amber-500/5 to-transparent border border-orange-500/20 rounded-2xl p-5 space-y-4 shadow-lg animate-in fade-in duration-300">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-xs font-black text-orange-400 uppercase tracking-wider flex items-center gap-1.5 font-sans">
                  <Sparkles className="w-4 h-4 animate-pulse text-orange-400" /> {"Today's Best Opportunities"} (Founder Priority Queue)
                </h3>
                <p className="text-[9px] text-gray-400 mt-0.5">Top high-value, highly contactable targets ready for action</p>
              </div>
              <span className="text-[8px] bg-orange-500 text-gray-955 px-2.5 py-0.5 rounded-full font-black uppercase tracking-wider animate-pulse">Priority</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {priorityQueue.map((l) => {
                const value = l.estimated_value || 60000;
                const score = l.coffee_buying_score || l.score || 0;
                const div = l.division || "unknown";
                
                let buying_fit = "MEDIUM";
                if (score >= 75 || ["distributor", "wholesaler", "supermarket", "modern_trade", "grocery_chain", "cafe"].includes(div)) {
                  buying_fit = "HIGH";
                } else if (score >= 45) {
                  buying_fit = "MEDIUM";
                } else {
                  buying_fit = "LOW";
                }
                
                const hasEmail = l.email && l.email.includes("@");
                const hasPhone = l.phone && l.phone.trim().length >= 10;
                const contactability = hasEmail && hasPhone ? "EMAIL + PHONE" : hasEmail ? "EMAIL ONLY" : hasPhone ? "PHONE ONLY" : "UNREACHABLE";
                
                return (
                  <div key={l.id} className="bg-gray-955 border border-gray-855 p-4 rounded-xl space-y-3 hover:border-orange-500/40 transition-all flex flex-col justify-between shadow-inner">
                    <div className="space-y-1">
                      <div className="flex justify-between items-start gap-2">
                        <span className="text-[8px] text-gray-500 font-bold uppercase tracking-wider">{l.city} {l.distance_from_origin ? `· ${l.distance_from_origin} km` : ""}</span>
                        <span className="text-[8px] bg-green-500/10 text-green-400 border border-green-500/20 px-1.5 py-0.5 rounded-full font-bold">Est: {rs(value)}</span>
                      </div>
                      <h4 className="font-bold text-xs text-gray-250 truncate">{l.company}</h4>
                      <p className="text-[9px] text-gray-400 truncate">{l.contact_name || "decision maker not recorded"}</p>
                    </div>
                    
                    <div className="grid grid-cols-3 gap-1 text-[8px] uppercase font-bold text-center border-t border-b border-gray-900 py-1.5 my-1.5">
                      <div>
                        <div className="text-gray-500 text-[7px]">Fit</div>
                        <div className="text-orange-400 font-black">{buying_fit}</div>
                      </div>
                      <div>
                        <div className="text-gray-500 text-[7px]">Contact</div>
                        <div className="text-blue-400 font-black truncate">{contactability.split(" ")[0]}</div>
                      </div>
                      <div>
                        <div className="text-gray-500 text-[7px]">Confidence</div>
                        <div className="text-green-400 font-black">{Math.round((l.division_confidence || 0.72) * 100)}%</div>
                      </div>
                    </div>
                    
                    <button
                      onClick={() => {
                        setExpandedOpportunityId(expandedOpportunityId === l.id ? null : l.id);
                      }}
                      className="w-full py-1.5 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded-lg transition-all shadow-sm"
                    >
                      {expandedOpportunityId === l.id ? "Hide Journey Details" : "Take Action Now"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Category Group Metrics Quick-Filter Pills */}
        <div className="flex flex-wrap gap-1.5 bg-gray-955 p-3 rounded-xl border border-gray-855 items-center font-sans">
          <span className="text-[8px] text-gray-500 uppercase font-black tracking-wider flex items-center mr-2">Category Filter:</span>
          {Object.entries(categoryGroups).map(([key, list]) => {
            const active = activeCategoryFilter === key;
            const label = key === "ALL" ? "All Groups" : key.replace(/_/g, " ");
            return (
              <button
                key={key}
                onClick={() => setActiveCategoryFilter(key)}
                className={`px-3 py-1.5 rounded-full text-[9px] font-bold border transition-all flex items-center gap-1.5 uppercase ${
                  active
                    ? "bg-orange-500/10 text-orange-400 border-orange-500/30 font-black"
                    : "border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-205"
                }`}
              >
                {label}
                <span className={`text-[8px] px-1.5 py-0.5 rounded-full ${active ? "bg-orange-500 text-gray-955 font-bold" : "bg-gray-800 text-gray-400"}`}>
                  {list.length}
                </span>
              </button>
            );
          })}
        </div>

        {/* Bucket Tabs Selection */}
        <div className="flex flex-wrap gap-1.5 border-b border-gray-850 pb-3">
          {(Object.keys(buckets) as Array<keyof typeof buckets>).map((b) => {
            const active = activeBucket === b;
            const count = buckets[b].length;
            const label = bucketLabels[b as keyof typeof bucketLabels] || b.replace(/_/g, " ");
            return (
              <button
                key={b}
                onClick={() => {
                  setActiveBucket(b);
                  setActiveCategoryFilter("ALL"); // Reset category filter on tab change
                }}
                className={`px-3 py-2 text-[10px] font-black uppercase rounded-lg border transition-all ${
                  active
                    ? "bg-orange-500 text-gray-955 border-orange-500 font-black shadow-md"
                    : "border-gray-800 bg-gray-955 text-gray-400 hover:text-gray-250"
                }`}
              >
                {label} ({count})
              </button>
            );
          })}
        </div>

        {/* Batch Actions and Header Card */}
        {activeLeadsToDisplay.length > 0 && (
          <div className="flex flex-col sm:flex-row justify-between items-center bg-gray-955 p-3 rounded-xl border border-gray-855 gap-3">
            <div>
              <span className="text-[10px] text-gray-400 font-bold uppercase">
                {activeBucket.replace(/_/g, " ")} — {activeLeadsToDisplay.length} opportunity{activeLeadsToDisplay.length > 1 ? "s" : ""} matching category
              </span>
            </div>
            <div className="flex gap-2">
              {activeBucket === "READY_NOW" && (
                <button
                  onClick={() => handleApproveJourney(activeLeadsToDisplay.map(l => l.id))}
                  className="px-4 py-1.5 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow"
                >
                  <Zap className="w-3.5 h-3.5 fill-current" /> [APPROVE INTRO EMAILS] ({activeLeadsToDisplay.length})
                </button>
              )}
              {activeBucket === "FOLLOW_UP_DUE" && (
                <button
                  onClick={() => handleApproveJourney(activeLeadsToDisplay.map(l => l.id))}
                  className="px-4 py-1.5 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow"
                >
                  <Zap className="w-3.5 h-3.5 fill-current" /> [APPROVE FOLLOW-UPS] ({activeLeadsToDisplay.length})
                </button>
              )}
              {activeBucket === "HIGH_INTENT_FOUNDER_ACTION" && (
                <button
                  onClick={() => showToast("Reviewing high intent opportunities...")}
                  className="px-4 py-1.5 bg-red-600 hover:bg-red-700 text-white text-[9px] font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow"
                >
                  <Sparkles className="w-3.5 h-3.5" /> [REVIEW HIGH INTENT] ({activeLeadsToDisplay.length})
                </button>
              )}
              {activeBucket === "WARM_LEADS" && (
                <button
                  onClick={() => showToast("Reviewing warm leads...")}
                  className="px-4 py-1.5 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow"
                >
                  <Eye className="w-3.5 h-3.5" /> [REVIEW WARM] ({activeLeadsToDisplay.length})
                </button>
              )}
              {activeBucket === "NEEDS_ENRICHMENT" && (
                <button
                  onClick={() => showToast("Triggering background enrichment processes...")}
                  className="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-[9px] font-black uppercase rounded-lg transition-all flex items-center gap-1.5 shadow"
                >
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Verify/Enrich All {activeLeadsToDisplay.length} Emails
                </button>
              )}
            </div>
          </div>
        )}

        {/* Opportunity Leads Table */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl overflow-hidden shadow-lg">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-gray-855 text-gray-500 font-bold uppercase tracking-wider text-[8px]">
                  <th className="py-2.5 pl-4">Company</th>
                  <th className="py-2.5">Category</th>
                  <th className="py-2.5 text-center">Buying Fit</th>
                  <th className="py-2.5">Contactability</th>
                  <th className="py-2.5 text-center">Intent</th>
                  <th className="py-2.5 text-right">Est. Value</th>
                  <th className="py-2.5 text-center">Confidence</th>
                  <th className="py-2.5">Next Action</th>
                  <th className="py-2.5 text-right pr-4">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-855">
                {activeLeadsToDisplay.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-8 text-center text-gray-500 font-medium italic">
                      No opportunities in this bucket matching current search and category filters.
                    </td>
                  </tr>
                ) : (
                  activeLeadsToDisplay.map((l) => {
                    const isExpanded = expandedOpportunityId === l.id;
                    
                    // Resolve Buying Fit
                    const score = l.coffee_buying_score || l.score || 0;
                    const div = (l.division || "unknown").toLowerCase();
                    let buying_fit = "LOW";
                    if (score >= 75 || ["distributor", "wholesaler", "supermarket", "modern_trade", "grocery_chain", "cafe"].includes(div)) {
                      buying_fit = "HIGH";
                    } else if (score >= 45) {
                      buying_fit = "MEDIUM";
                    } else if (score >= 30) {
                      buying_fit = "LOW";
                    } else {
                      buying_fit = "REJECT";
                    }

                    // Resolve Contactability
                    const hasEmail = l.email && l.email.includes("@");
                    const hasPhone = l.phone && l.phone.trim().length >= 10;
                    let contactability = "UNREACHABLE";
                    if (hasEmail && hasPhone) {
                      contactability = "EMAIL_PHONE";
                    } else if (hasEmail) {
                      contactability = "EMAIL";
                    } else if (hasPhone) {
                      contactability = "PHONE";
                    }

                    // Resolve Intent
                    const stat = (l.status || "DISCOVERED").toUpperCase();
                    let intent = "COLD";
                    if (["REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED"].includes(stat)) {
                      intent = "HOT";
                    } else if (["SAMPLE_SENT", "PROPOSAL_SENT"].includes(stat)) {
                      intent = "WARM";
                    }

                    // Resolve next action
                    const nextAction = l.recommended_action || (
                      contactability === "EMAIL_PHONE" || contactability === "EMAIL" ? "INTRO EMAIL" :
                      contactability === "PHONE" ? "FOUNDER CALL" : "ENRICH CONTACT"
                    );

                    // Is approved
                    const isApproved = l.email_approved_by_founder && l.status !== "DISCOVERED";

                    return (
                      <React.Fragment key={l.id}>
                        <tr className="hover:bg-gray-900/35 transition-colors">
                          <td className="py-3.5 pl-4">
                            <div className="font-bold text-gray-250 flex items-center gap-1.5">
                              {l.company}
                              {l.distance_from_origin && (
                                <span className="text-[8px] bg-orange-500/10 text-orange-400 border border-orange-500/20 px-1.5 py-0.5 rounded-full font-bold">
                                  {l.distance_from_origin} km
                                </span>
                              )}
                            </div>
                            <div className="text-[9px] text-gray-500 mt-0.5">
                              {l.contact_persona || "role unknown"} · {l.contact_name || "decision maker not recorded"} · {l.city}
                            </div>
                            {(buying_fit === "REJECT" || l.email_rejected_by_founder) && (
                              <div className="text-[8px] text-red-400 mt-0.5 italic font-bold">
                                Disqualified: {l.coffee_buying_evidence || "Manual reject / Low fit score"}
                              </div>
                            )}
                          </td>
                          <td className="py-3.5 text-gray-400 font-medium uppercase text-[9px]">
                            {div.replace(/_/g, " ")}
                          </td>
                          <td className="py-3.5 text-center">
                            <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full border ${
                              buying_fit === "HIGH" ? "bg-green-500/10 text-green-400 border-green-500/20" :
                              buying_fit === "MEDIUM" ? "bg-amber-500/10 text-amber-400 border-amber-500/20" :
                              buying_fit === "LOW" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                              "bg-red-500/10 text-red-400 border-red-500/20"
                            }`}>
                              {buying_fit}
                            </span>
                          </td>
                          <td className="py-3.5">
                            <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full border ${
                              contactability === "EMAIL_PHONE" ? "bg-green-500/10 text-green-400 border-green-500/20" :
                              contactability === "EMAIL" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                              contactability === "PHONE" ? "bg-orange-500/10 text-orange-400 border-orange-500/20" :
                              "bg-gray-500/10 text-gray-500 border-gray-800"
                            }`}>
                              {contactability.replace("_", " + ")}
                            </span>
                          </td>
                          <td className="py-3.5 text-center font-mono font-bold">
                            <span className={
                              intent === "HOT" ? "text-red-400 animate-pulse font-black" :
                              intent === "WARM" ? "text-orange-400" : "text-blue-400"
                            }>
                              {intent}
                            </span>
                          </td>
                          <td className="py-3.5 text-right font-mono font-bold text-gray-300">
                            <div>{rs(l.estimated_value || 60000)}</div>
                            {l.expected_monthly_consumption_kg && l.expected_monthly_consumption_kg > 0 ? (
                              <div className="text-[7px] text-green-400 uppercase tracking-widest mt-0.5">
                                ✔ VERIFIED
                              </div>
                            ) : (
                              <div className="text-[7px] text-gray-500 uppercase tracking-widest mt-0.5">
                                MODELLED
                              </div>
                            )}
                          </td>
                          <td className="py-3.5 text-center font-mono font-bold text-blue-400">
                            {l.division_confidence ? `${Math.round(l.division_confidence * 100)}%` : "50%"}
                          </td>
                          <td className="py-3.5 font-bold uppercase text-[9px] text-gray-300">
                            {nextAction}
                          </td>
                          <td className="py-3.5 text-right pr-4 space-x-2 whitespace-nowrap">
                            <button
                              onClick={() => setSelectedOpportunityForMemory(l)}
                              className="text-[10px] text-blue-400 hover:underline font-bold"
                            >
                              Memory
                            </button>
                            <button
                              onClick={() => setExpandedOpportunityId(isExpanded ? null : l.id)}
                              className="text-[10px] text-gray-400 hover:underline font-bold"
                            >
                              {isExpanded ? "Hide Journey" : "View Journey"}
                            </button>
                            {l.status !== "ORDER_WON" && l.status !== "PAYMENT_VERIFIED" && (
                              <button
                                onClick={() => handleMarkWon(l)}
                                className="px-2.5 py-1 bg-green-500/10 hover:bg-green-500/20 text-green-400 border border-green-500/25 text-[9px] font-black uppercase rounded shadow-sm transition-all"
                              >
                                🎉 Mark Won
                              </button>
                            )}
                            {!isApproved && (
                              <button
                                disabled={busyLeads.has(l.id)}
                                onClick={() => handleApproveJourney([l.id])}
                                className="px-2.5 py-1 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded shadow-sm transition-all"
                              >
                                {busyLeads.has(l.id) ? "Queueing" : "Approve"}
                              </button>
                            )}
                          </td>
                        </tr>

                        {/* Expandable Journey Timeline Panel */}
                        {isExpanded && (
                          <tr>
                            <td colSpan={9} className="py-4 bg-gray-955 border-t border-b border-gray-850 px-4">
                              <div className="space-y-4 animate-in slide-in-from-top-4 duration-250">
                                
                                {/* Multi-Axis Details Dashboard Card */}
                                <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                                  <div className="bg-gray-900 border border-gray-855 p-3.5 rounded-xl space-y-2">
                                    <div className="text-[8px] text-gray-500 uppercase font-black tracking-wider">Contact & Verification</div>
                                    <div className="space-y-1.5 text-[10px]">
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Email:</span>
                                        <span className="text-gray-250 font-bold truncate max-w-[120px]" title={l.email}>{l.email || "Missing"}</span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Verification:</span>
                                        <span className={l.email_verified || l.email_verification_status === "VALID" ? "text-green-400 font-bold" : "text-amber-500"}>
                                          {l.email_verified || l.email_verification_status === "VALID" ? "✔ Verified" : "⚠ Unverified"}
                                        </span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Governance:</span>
                                        <span className="text-green-400 font-bold">Safe (Compliant)</span>
                                      </div>
                                    </div>
                                  </div>

                                  <div className="bg-gray-900 border border-gray-855 p-3.5 rounded-xl space-y-2">
                                    <div className="text-[8px] text-gray-500 uppercase font-black tracking-wider font-sans">Intent & Switching Signal</div>
                                    <div className="space-y-1.5 text-[10px]">
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Buying Intent:</span>
                                        <span className={`font-bold ${l.engagement_level === "HOT" ? "text-red-400 animate-pulse" : l.engagement_level === "WARM" ? "text-orange-400" : "text-blue-400"}`}>
                                          {l.engagement_level || "UNKNOWN"}
                                        </span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Switching Propensity:</span>
                                        <span className="text-gray-255 font-bold">
                                          {l.switching_signal?.signal || "UNKNOWN"}
                                        </span>
                                      </div>
                                      <div className="text-[8px] text-gray-400 italic truncate" title={l.switching_signal?.evidence}>
                                        Source: {l.switching_signal?.source || "Google Maps Remarks"}
                                      </div>
                                    </div>
                                  </div>

                                  <div className="bg-gray-900 border border-gray-855 p-3.5 rounded-xl space-y-2 font-sans">
                                    <div className="text-[8px] text-gray-500 uppercase font-black tracking-wider font-sans">Historical Conversion</div>
                                    <div className="space-y-1.5 text-[10px]">
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Segment Reply Rate:</span>
                                        <span className="text-orange-400 font-bold">
                                          {l.segment_performance?.rate || "INSUFFICIENT DATA"}
                                        </span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Observations:</span>
                                        <span className="text-gray-300 font-mono">
                                          {l.segment_performance?.observations ?? 0} runs
                                        </span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Priority Score:</span>
                                        <span className="text-green-400 font-bold font-mono">
                                          {l.priority_score ? Math.round(l.priority_score) : 0} pts
                                        </span>
                                      </div>
                                    </div>
                                  </div>

                                  <div className="bg-gray-900 border border-gray-855 p-3.5 rounded-xl space-y-2">
                                    <div className="text-[8px] text-gray-500 uppercase font-black tracking-wider">Next Action & Value</div>
                                    <div className="space-y-1.5 text-[10px]">
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Commercial Value:</span>
                                        <span className="text-green-400 font-bold font-sans">
                                          {rs(l.value || 60000)}
                                        </span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Value Type:</span>
                                        <span className="text-gray-400 bg-gray-955 px-1.5 py-0.5 rounded text-[8px] font-bold">
                                          {l.value_type || "MODELLED"}
                                        </span>
                                      </div>
                                      <div className="flex justify-between">
                                        <span className="text-gray-400">Next Action:</span>
                                        <span className="text-blue-400 font-bold">{l.next_action || "INTRO EMAIL"}</span>
                                      </div>
                                    </div>
                                  </div>
                                </div>

                                <div className="flex justify-between items-center pt-2">
                                  <h4 className="text-[10px] font-black text-gray-300 uppercase tracking-wider">
                                    ⚡ Autonomous Journey Workflow Stage Checklist
                                  </h4>
                                  <h4 className="text-[10px] font-black text-gray-300 uppercase tracking-wider">
                                    ⚡ Autonomous Journey Workflow Stage Checklist
                                  </h4>
                                  <span className="text-[9px] text-gray-500 font-medium">
                                    Status: {l.status}
                                  </span>
                                </div>
                                
                                <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-2.5 text-[9px] font-bold uppercase text-center font-sans">
                                  {getStageChecklist(l).map((stage, sIdx) => {
                                    const isDone = stage.state === "done";
                                    const isFail = stage.state === "failed";
                                    return (
                                      <div
                                        key={sIdx}
                                        title={stage.detail || (isDone ? "Recorded in the event log" : "No record of this step yet")}
                                        className={`p-2 rounded-lg border ${
                                          isDone ? "bg-green-500/10 border-green-500/20 text-green-400"
                                          : isFail ? "bg-red-500/15 border-red-500/40 text-red-400"
                                          : "bg-gray-900 border-red-500/20 text-red-400/60"}`}
                                      >
                                        <div className="flex items-center justify-center gap-1">
                                          {isDone ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
                                          {stage.label}
                                        </div>
                                        {stage.detail && (
                                          <div className="text-[7px] font-medium normal-case opacity-70 truncate mt-0.5">
                                            {stage.detail}
                                          </div>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>

                                {/* Inline contact form */}
                                {(() => {
                                  const onFileEmail = l.email_on_file ?? l.email ?? "";
                                  const onFilePhone = l.phone_on_file ?? l.phone ?? "";
                                  const currentVal = editedLeads[l.id] || {
                                    contact_name: l.contact_name || "",
                                    email: onFileEmail,
                                    phone: onFilePhone
                                  };
                                  const hasChanged = currentVal.contact_name !== (l.contact_name || "") ||
                                                     currentVal.email !== onFileEmail ||
                                                     currentVal.phone !== onFilePhone;

                                  return (
                                    <div className="bg-gray-900/60 border border-gray-850 rounded-xl p-4 space-y-3.5 shadow-inner animate-in fade-in-50 duration-200">
                                      <div className="flex justify-between items-center">
                                        <div className="flex items-center gap-2">
                                          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                                          <span className="text-[10px] font-black text-gray-250 uppercase tracking-widest font-sans">
                                            Edit Lead Contact Information
                                          </span>
                                        </div>
                                        {hasChanged && (
                                          <button
                                            disabled={savingLeadId === l.id}
                                            onClick={() => handleSaveLeadDetails(l.id)}
                                            className="px-2.5 py-0.5 bg-orange-500 hover:bg-orange-600 text-gray-955 text-[9px] font-black uppercase rounded shadow transition-all"
                                          >
                                            {savingLeadId === l.id ? "Saving..." : "Save Details"}
                                          </button>
                                        )}
                                      </div>
                                      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                        <div>
                                          <label className="text-[8px] text-gray-500 uppercase font-black block mb-1">Contact Name</label>
                                          <input
                                            type="text"
                                            value={currentVal.contact_name}
                                            onChange={(e) => setEditedLeads({
                                              ...editedLeads,
                                              [l.id]: { ...currentVal, contact_name: e.target.value }
                                            })}
                                            className="w-full bg-gray-955 text-gray-205 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
                                          />
                                        </div>
                                        <div>
                                          <label className="text-[8px] text-gray-500 uppercase font-black block mb-1 font-sans">
                                            Email Address
                                          </label>
                                          <input
                                            type="text"
                                            value={currentVal.email}
                                            onChange={(e) => setEditedLeads({
                                              ...editedLeads,
                                              [l.id]: { ...currentVal, email: e.target.value }
                                            })}
                                            className="w-full bg-gray-955 text-gray-205 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
                                          />
                                        </div>
                                        <div>
                                          <label className="text-[8px] text-gray-500 uppercase font-black block mb-1">Phone Number</label>
                                          <input
                                            type="text"
                                            value={currentVal.phone}
                                            onChange={(e) => setEditedLeads({
                                              ...editedLeads,
                                              [l.id]: { ...currentVal, phone: e.target.value }
                                            })}
                                            className="w-full bg-gray-955 text-gray-205 border border-gray-800 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-orange-500"
                                          />
                                        </div>
                                      </div>
                                    </div>
                                  );
                                })()}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>\n            {/* memory audit drawer */}
      {selectedOpportunityForMemory && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/75 backdrop-blur-sm">
          <div className="w-full max-w-lg bg-gray-900 h-full border-l border-gray-800 p-6 flex flex-col justify-between animate-in slide-in-from-right duration-200">
            <div>
              <div className="flex justify-between items-start border-b border-gray-800 pb-4">
                <div>
                  <h3 className="text-sm font-black text-white uppercase tracking-widest">{selectedOpportunityForMemory.company}</h3>
                  <p className="text-[10px] text-gray-500 mt-1">Audit Trail & Interaction Memory Log</p>
                </div>
                <button onClick={() => setSelectedOpportunityForMemory(null)} className="p-1 text-gray-500 hover:text-gray-200">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="mt-5 space-y-5 overflow-y-auto max-h-[76vh] pr-1">

                {/* What we already know — every line traces to a stored interaction */}
                <div>
                  <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2">What we know</h4>
                  {memoryLoading && <p className="text-[11px] text-gray-500 italic">Loading…</p>}
                  {!memoryLoading && (
                    <div className="bg-gray-955 border border-gray-850 rounded-xl p-3.5 space-y-1.5">
                      {([
                        ["Decision maker", memory?.summary?.decision_maker],
                        ["Current supplier", memory?.summary?.current_supplier],
                        ["Consumption", memory?.summary?.monthly_consumption_kg
                          ? `${memory.summary.monthly_consumption_kg} kg/month` : null],
                        ["Budget", memory?.summary?.budget_range],
                        ["Best time to call", memory?.summary?.preferred_contact_time],
                        ["Prefers", memory?.summary?.preferred_contact_method],
                      ] as Array<[string, string | number | null | undefined]>).map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-3 text-[11px]">
                          <span className="text-gray-500">{k}</span>
                          {/* Unknown stays visibly unknown rather than blank or invented */}
                          <span className={v ? "text-gray-200 font-semibold text-right" : "text-gray-600 italic"}>
                            {v || "not recorded yet"}
                          </span>
                        </div>
                      ))}
                      {!!memory?.summary?.risks?.length && (
                        <div className="pt-2 mt-2 border-t border-gray-850 space-y-1">
                          {memory.summary.risks.map((r, i) => (
                            <p key={i} className="text-[10px] text-amber-400/90">{r}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Record what happened on the call */}
                <div>
                  <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2">
                    Record an interaction
                  </h4>
                  <div className="bg-gray-955 border border-gray-850 rounded-xl p-3.5 space-y-2.5">
                    <div className="grid grid-cols-2 gap-2">
                      <select
                        value={interaction.method}
                        onChange={e => setInteraction({ ...interaction, method: e.target.value })}
                        className="bg-gray-900 border border-gray-800 rounded-lg px-2 py-1.5 text-[11px] text-gray-200"
                      >
                        {INTERACTION_METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                      <select
                        value={interaction.outcome}
                        onChange={e => setInteraction({ ...interaction, outcome: e.target.value })}
                        className="bg-gray-900 border border-gray-800 rounded-lg px-2 py-1.5 text-[11px] text-gray-200"
                      >
                        <option value="">Outcome…</option>
                        {INTERACTION_OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </div>

                    <textarea
                      value={interaction.remark}
                      onChange={e => setInteraction({ ...interaction, remark: e.target.value })}
                      rows={3}
                      placeholder="What was said? e.g. Spoke to the owner, buys Nescafe from a local distributor, asked us to call back after Independence Day."
                      className="w-full bg-gray-900 border border-gray-800 rounded-lg px-2.5 py-2 text-[11px] text-gray-200 placeholder:text-gray-600 resize-none"
                    />

                    <div className="grid grid-cols-2 gap-2">
                      {([
                        ["decision_maker", "Who you spoke to"],
                        ["designation", "Their role"],
                        ["current_supplier", "Buys from now"],
                        ["monthly_consumption_kg", "Kg / month"],
                        ["budget_range", "Budget"],
                        ["preferred_contact_time", "Best time to call"],
                        ["next_followup_date", "Follow up on"],
                      ] as Array<[keyof InteractionForm, string]>).map(([k, ph]) => (
                        <input
                          key={k}
                          value={interaction[k]}
                          onChange={e => setInteraction({ ...interaction, [k]: e.target.value })}
                          placeholder={ph}
                          className="bg-gray-900 border border-gray-800 rounded-lg px-2.5 py-1.5 text-[11px] text-gray-200 placeholder:text-gray-600"
                        />
                      ))}
                      <select
                        value={interaction.preferred_contact_method}
                        onChange={e => setInteraction({ ...interaction, preferred_contact_method: e.target.value })}
                        className="bg-gray-900 border border-gray-800 rounded-lg px-2 py-1.5 text-[11px] text-gray-200"
                      >
                        <option value="">Prefers…</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="phone">Phone</option>
                        <option value="email">Email</option>
                        <option value="visit">In person</option>
                      </select>
                    </div>

                    <p className="text-[9px] text-gray-600 leading-relaxed">
                      Leave anything you did not learn blank — it stays unknown. Saved facts are
                      reused in drafts and call briefs, and are never overwritten by a later blank.
                    </p>

                    <button
                      onClick={handleRecordInteraction}
                      disabled={savingInteraction}
                      className="w-full py-2 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-[11px] font-black uppercase rounded-lg transition-all"
                    >
                      {savingInteraction ? "Saving…" : "Save to memory"}
                    </button>
                  </div>
                </div>

                {/* Immutable history */}
                <div>
                  <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-2">
                    History{memory?.timeline?.length ? ` (${memory.timeline.length})` : ""}
                  </h4>
                  <div className="space-y-2">
                    {(memory?.timeline ?? []).map((t, idx) => (
                      <div key={idx} className="bg-gray-955 border border-gray-850 p-3 rounded-xl">
                        <div className="flex justify-between items-start gap-2">
                          <span className="text-[10px] font-bold text-gray-300 uppercase">
                            {(t.channel || t.type || "").replace(/_/g, " ")}
                          </span>
                          <span className="text-[9px] text-gray-500 shrink-0">
                            {t.at ? new Date(t.at).toLocaleDateString() : ""}
                          </span>
                        </div>
                        {t.detail && <p className="text-[10px] text-gray-500 mt-1 leading-relaxed">{t.detail}</p>}
                      </div>
                    ))}
                    {!memoryLoading && !(memory?.timeline ?? []).length && (
                      <div className="text-center py-8 text-gray-500 italic text-[11px]">
                        Nothing recorded against this business yet.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
            
            <button
              onClick={() => setSelectedOpportunityForMemory(null)}
              className="w-full py-2 bg-gray-955 hover:bg-gray-850 text-gray-400 hover:text-gray-200 text-xs font-black uppercase rounded-xl border border-gray-800 transition-all text-center mt-6"
            >
              Close Memory Log
            </button>
          </div>
        </div>
      )}

      {/* Custom Mark Won & Agent Commission Split Modal */}
      {showMarkWonModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-990/80 backdrop-blur-sm animate-fadeIn">
          <div className="bg-gray-900 border border-gray-855 w-full max-w-md rounded-2xl p-6 shadow-2xl space-y-5 animate-scaleIn">
            <div className="border-b border-gray-850 pb-3">
              <h3 className="text-xs font-black text-gray-150 uppercase tracking-widest flex items-center gap-1.5">
                🎉 Close Won: {markWonCompanyName}
              </h3>
              <p className="text-[9px] text-gray-500 mt-1 font-sans">
                Marking this deal won will record revenue and generate a strict 5% commission pool for AI agent upgrades.
              </p>
            </div>

            <div className="space-y-4">
              {/* Order Cash input */}
              <div>
                <label className="text-[8px] font-black text-gray-400 uppercase tracking-wider block mb-1 font-sans">
                  Actual Cash Received (INR)
                </label>
                <input
                  type="number"
                  value={markWonOrderValue}
                  onChange={(e) => setMarkWonOrderValue(Number(e.target.value))}
                  className="w-full bg-gray-955 border border-gray-850 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono focus:border-orange-500 focus:outline-none"
                  placeholder="e.g. 100000"
                />
                <span className="text-[8px] text-gray-500 block mt-1 font-mono">
                  Commission Pool (5%): ₹{Math.round(markWonOrderValue * 0.05)} (Funds AI Upgrades)
                </span>
              </div>

              {/* Commission Splits Override Sliders/Inputs */}
              <div className="space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-[8px] font-black text-gray-400 uppercase tracking-wider font-sans">
                    Agent Commission Splits (%)
                  </span>
                  <span
                    className={`text-[8px] font-mono font-bold ${
                      Number(splitDiscovery) + Number(splitOutreach) + Number(splitProposal) + Number(splitClosing) === 100
                        ? "text-green-400"
                        : "text-red-400"
                    }`}
                  >
                    Total: {Number(splitDiscovery) + Number(splitOutreach) + Number(splitProposal) + Number(splitClosing)}% / 100%
                  </span>
                </div>

                <div className="bg-gray-955 p-3 rounded-lg border border-gray-850 space-y-2.5">
                  {[
                    { label: "Discovery / Scoring", val: splitDiscovery, setVal: setSplitDiscovery },
                    { label: "Outreach / Sequences", val: splitOutreach, setVal: setSplitOutreach },
                    { label: "Proposal / Negotiation", val: splitProposal, setVal: setSplitProposal },
                    { label: "Follow-up / Closing", val: splitClosing, setVal: setSplitClosing }
                  ].map((s, idx) => (
                    <div key={idx} className="flex items-center justify-between gap-3 font-sans">
                      <span className="text-[9px] text-gray-400 truncate w-32">{s.label}</span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <input
                          type="number"
                          min="0"
                          max="100"
                          value={s.val}
                          onChange={(e) => s.setVal(Math.min(100, Math.max(0, Number(e.target.value))))}
                          className="w-12 bg-gray-900 border border-gray-850 text-center rounded px-1.5 py-0.5 text-[9px] text-gray-200 font-mono focus:border-orange-500 focus:outline-none"
                        />
                        <span className="text-[9px] text-gray-650">%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3 pt-3 font-sans">
              <button
                type="button"
                onClick={() => setShowMarkWonModal(false)}
                className="flex-1 py-2 bg-gray-955 hover:bg-gray-850 border border-gray-850 text-gray-400 text-xs font-black uppercase rounded-xl transition-all"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submitMarkWon}
                disabled={Number(splitDiscovery) + Number(splitOutreach) + Number(splitProposal) + Number(splitClosing) !== 100}
                className={`flex-1 py-2 text-xs font-black uppercase rounded-xl transition-all shadow-md ${
                  Number(splitDiscovery) + Number(splitOutreach) + Number(splitProposal) + Number(splitClosing) === 100
                    ? "bg-green-500 hover:bg-green-600 text-gray-955"
                    : "bg-gray-800 text-gray-600 cursor-not-allowed border border-gray-850"
                }`}
              >
                🎉 Confirm Win
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
