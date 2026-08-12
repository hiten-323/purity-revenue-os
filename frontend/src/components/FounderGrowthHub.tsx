"use client";
import React from "react";
import { Target, RefreshCw, FileText } from "lucide-react";

// Minimal lead interface — structurally compatible with B2BLead in page.tsx
export interface HubLead {
  id: number;
  company: string;
  contact_name?: string | null;
  email?: string | null;
  phone?: string | null;
  whatsapp_number?: string | null;
  city?: string | null;
  division?: string | null;
  status?: string | null;
  estimated_value?: number | null;
  probability?: number | null;
  rrs?: number | null;   // V1.1: computed server-side by the Decision Engine
}

// ── Channel configuration ──────────────────────────────────────
type ChannelId = "distributor"|"retail"|"horeca"|"gifting"|"corporate"|"private_label"|"tender"|"analytics";

interface CallStep { label: string; text: string; }

interface ChannelConfig {
  id: ChannelId;
  label: string;
  icon: string;
  divisions: string[];
  accent: string;
  emailSubject: string;
  emailBody: (name: string, city: string) => string;
  whatsappMsg: (name: string, city: string) => string;
  aiScript: (name: string, city: string) => CallStep[];
  tenderMode?: true;
  discoverySegments: string[];
}

const sig = "\n\nWarm regards,\nHiten Jain\nFounder, Purity Beans | Pure Pantry Provisions\n+91 98889 97212 | +91 98555 93323\nconnect@purepantryprovisions.com";
const gr = (n: string) => n ? `Dear ${n},` : "Hi,";

export const CHANNELS: ChannelConfig[] = [
  {
    id: "distributor", label: "Distributors", icon: "D", accent: "indigo",
    divisions: ["distributor"],
    discoverySegments: ["distributor"],
    emailSubject: "Distribution Partnership — Purity Beans Premium Coffee | Pan-India",
    emailBody: (n, c) => `${gr(n)}\n\nI am reaching out from Pure Pantry Provisions — the company behind Purity Beans, a premium coffee brand seeking strong distribution partners in ${c || "your region"}.\n\nWhat we offer:\n- Distributor margins 28-35%\n- Arabica + Robusta blends, FSSAI compliant\n- Barcode-ready packaging with field support\n- Free sample kit — no commitment required\n\nCould you spare 15 minutes this week for a quick call?${sig}`,
    whatsappMsg: (n, c) => `Namaste ${n || ""}${n ? " ji" : ""}\n\nMain Hiten Jain hun — Purity Beans ka Founder.\n${c || "aapke sheher"} mein distribution ke liye email bheja tha. Mila?\n\n- Margin: 28-35%\n- Free sample kit\n\nKya is hafte 15 min milenge?\n+91 98889 97212`,
    aiScript: (n, c) => [
      { label: "Opening",      text: `"Namaste, kya main ${n || "aap"} ji se baat kar sakta hun?"` },
      { label: "Introduction", text: `"Ji, main Purity Beans coffee ki taraf se call kar raha hun — email aur WhatsApp bheja tha."` },
      { label: "Hook",         text: `"Hum ${c || "aapke sheher"} mein distributor partner dhundh rahe hain. 5 minutes hain?"` },
      { label: "Value Prop",   text: `"Distributor margin 28-35% aur free sample kit — koi commitment nahi."` },
      { label: "CTA",          text: `"Kya is hafte ek choti meeting rakh sakte hain?"` },
    ],
  },
  {
    id: "retail", label: "Retail / Modern Trade", icon: "R", accent: "blue",
    divisions: ["retail", "modern_trade", "grocery"],
    discoverySegments: ["grocery"],
    emailSubject: "Stock Purity Beans Coffee — Retailer Partnership",
    emailBody: (n, c) => `${gr(n)}\n\nI am Hiten from Purity Beans, a premium coffee brand building retail presence in ${c || "your region"}.\n\nWhy stock Purity Beans:\n- High retail margins (22-28%)\n- Fast-moving SKUs — 250g, 500g, 1kg formats\n- FSSAI certified, barcoded, shelf-ready\n- POS support & in-store visibility materials\n- Free trial stock for first order\n\nCan I send you a sample pack?${sig}`,
    whatsappMsg: (n, c) => `Namaste ${n || ""}${n ? " ji" : ""}\n\nPurity Beans premium coffee ${c || "aapke store"} mein rakhne ke baare mein email kiya tha.\n\n- Retail margin: 22-28%\n- Ready shelf stock\n\nFree sample pack bhej sakta hun. 10 min milenge?\n+91 98889 97212`,
    aiScript: (n, c) => [
      { label: "Opening", text: `"Namaste, ${n || "aap"} ji se baat ho sakti hai?"` },
      { label: "Pitch",   text: `"Purity Beans coffee ${c || "aapke area"} mein stock karne ke baare mein call kar raha tha."` },
      { label: "Hook",    text: `"22-28% margin aur shelf-ready packaging — kya aap sample dekhna chahenge?"` },
      { label: "CTA",     text: `"Kya is hafte sample pack bhej sakta hun?"` },
    ],
  },
  {
    id: "horeca", label: "HORECA", icon: "H", accent: "amber",
    divisions: ["horeca"],
    discoverySegments: ["horeca"],
    emailSubject: "Premium Coffee Supply for Your Establishment | Purity Beans",
    emailBody: (n, c) => `${gr(n)}\n\nI am Hiten from Purity Beans — we supply premium roasted coffee to hotels, restaurants and cafes in ${c || "your region"}.\n\nWhy Purity Beans for your establishment:\n- Freshly roasted Arabica & Robusta blends\n- Custom grind profiles for espresso, filter & French press\n- Competitive wholesale pricing with consistent supply\n- Free tasting session for your team\n\nWe would love to understand your current setup and volumes. Can we schedule a quick call?${sig}`,
    whatsappMsg: (n, c) => `Namaste ${n || ""}${n ? " ji" : ""}\n\nPurity Beans se hun — ${c || "aapke establishment"} ke liye premium coffee supply par email kiya tha.\n\nFree tasting session arrange kar sakte hain. 10 min milenge?\n+91 98889 97212`,
    aiScript: (n, c) => [
      { label: "Opening",  text: `"Namaste, ${n || "aap"} ji se baat ho sakti hai?"` },
      { label: "Pitch",    text: `"Purity Beans premium coffee supply ke baare mein call kar raha tha."` },
      { label: "Qualify",  text: `"Aap currently coffee kahan se lete hain aur monthly kitna use hota hai?"` },
      { label: "Value",    text: `"Hum freshly roasted coffee wholesale price par dete hain — free tasting ke saath."` },
      { label: "CTA",      text: `"Kya is hafte ek tasting session rakh sakte hain?"` },
    ],
  },
  {
    id: "gifting", label: "Corporate Gifting", icon: "G", accent: "rose",
    divisions: ["gifting"],
    discoverySegments: ["corporate"],
    emailSubject: "Premium Coffee Gift Hampers for Corporate Gifting | Purity Beans",
    emailBody: (n, c) => `${gr(n)}\n\nI am Hiten from Purity Beans. With the gifting season approaching, we would love to offer your team premium coffee gift hampers for employees, clients, and partners.\n\nWhat we offer:\n- Custom branded coffee hampers (Rs.499 to Rs.2,999 per unit)\n- MOQ as low as 50 units\n- Festival / occasion-specific packaging\n- Bulk discount on 200+ units\n- Pan-India delivery with tracking\n\nCan I share our gifting catalogue?${sig}`,
    whatsappMsg: (n, c) => `Namaste ${n || ""}${n ? " ji" : ""}\n\nPurity Beans se hun. Corporate gifting ke liye premium coffee hampers — Rs.499 se shuru, custom branding available.\n\nCatalogue share karun?\n+91 98889 97212`,
    aiScript: (n, c) => [
      { label: "Opening", text: `"Namaste, ${n || "aap"} HR/Admin department se hain?"` },
      { label: "Pitch",   text: `"Purity Beans se hun — corporate gifting ke liye premium coffee hampers offer kar raha tha."` },
      { label: "Hook",    text: `"Rs.499 se shuru, custom branding aur bulk discount available hai."` },
      { label: "CTA",     text: `"Festival season aa rahi hai — kya catalogue share kar sakta hun?"` },
    ],
  },
  {
    id: "corporate", label: "Corporate Pantry", icon: "C", accent: "teal",
    divisions: ["corporate"],
    discoverySegments: ["corporate"],
    emailSubject: "Upgrade Your Office Coffee | Purity Beans Pantry Program",
    emailBody: (n, c) => `${gr(n)}\n\nI am Hiten from Purity Beans. We supply premium coffee to corporate offices, factories, and institutions in ${c || "your region"} through our pantry subscription program.\n\nWhat we offer:\n- Monthly supply subscription (flexible quantities)\n- Freshly roasted beans & instant coffee blends\n- Auto-replenishment with 48-hr delivery\n- Significant cost saving vs. retail (30-40%)\n\nCould we understand your current coffee consumption and discuss a pilot?${sig}`,
    whatsappMsg: (n, c) => `Namaste ${n || ""}${n ? " ji" : ""}\n\nPurity Beans se hun — ${c || "aapke office"} ke liye premium coffee pantry program par email kiya tha.\n\nRetail se 30-40% sasta. Pilot ke baare mein baat karein?\n+91 98889 97212`,
    aiScript: (n, c) => [
      { label: "Opening", text: `"Namaste, ${n || "aap"} Admin/Procurement team se hain?"` },
      { label: "Pitch",   text: `"Purity Beans — office coffee pantry program ke baare mein call kar raha tha."` },
      { label: "Qualify", text: `"Aapke office mein kitne employees hain aur coffee kahan se aati hai?"` },
      { label: "Value",   text: `"Hum monthly subscription dete hain — retail se 30-40% sasta, auto-replenishment ke saath."` },
      { label: "CTA",     text: `"Kya ek small pilot start kar sakte hain?"` },
    ],
  },
  {
    id: "private_label", label: "Private Label", icon: "P", accent: "purple",
    divisions: ["private_label"],
    discoverySegments: ["private_label"],
    emailSubject: "Private Label Coffee Manufacturing | Pure Pantry Provisions",
    emailBody: (n, c) => `${gr(n)}\n\nI am Hiten from Pure Pantry Provisions. We offer private label coffee manufacturing — roasting, blending, packaging, and fulfilment — for D2C brands, exporters, and retail chains.\n\nOur capabilities:\n- Custom roast profiles (light, medium, dark)\n- White-label & branded packaging\n- MOQ from 100 kg\n- FSSAI + export compliance\n- Pilot batch available within 2 weeks\n\nWould you like to explore a pilot?${sig}`,
    whatsappMsg: (n, c) => `Namaste ${n || ""}${n ? " ji" : ""}\n\nPure Pantry Provisions se hun. Private label coffee manufacturing — custom roast, branded packaging, MOQ 100 kg se.\n\nPilot batch 2 weeks mein. Baat karein?\n+91 98889 97212`,
    aiScript: (n, c) => [
      { label: "Opening",    text: `"Namaste, ${n || "aap"} ji se baat ho sakti hai?"` },
      { label: "Pitch",      text: `"Private label coffee manufacturing ke baare mein email kiya tha."` },
      { label: "Qualify",    text: `"Aapka brand kis segment mein hai aur monthly volume kya hoga?"` },
      { label: "Value Prop", text: `"MOQ 100 kg se, custom packaging, FSSAI compliant — pilot 2 weeks mein."` },
      { label: "CTA",        text: `"Kya ek NDA sign karke pilot discuss kar sakte hain?"` },
    ],
  },
  {
    id: "tender", label: "Govt / PSU Tenders", icon: "T", accent: "green",
    divisions: ["tender", "government"],
    discoverySegments: ["tender"],
    emailSubject: "Coffee Supply Tender Bid | Purity Beans — Pure Pantry Provisions",
    emailBody: (n, c) => `${gr(n)}\n\nPure Pantry Provisions (Purity Beans) wishes to submit a bid for your coffee supply requirement.\n\nOur credentials:\n- FSSAI licensed manufacturer\n- ISO-grade quality standards\n- Pan-India supply capability\n- GST registered, government invoice compliant\n- Competitive pricing with volume discounts\n\nKindly share the tender document or RFQ so we may submit our technical and financial bid.${sig}`,
    whatsappMsg: () => "",
    aiScript: () => [],
    tenderMode: true,
  },
];

// ── Pipeline stage definitions ─────────────────────────────────
const STANDARD_PIPELINE = [
  { key: "cold",     label: "1. Discovery",    statuses: ["DISCOVERED","QUALIFIED","COLD"],           color: "border-gray-700 bg-gray-800/40",          dot: "bg-gray-500"   },
  { key: "email",    label: "2. Email",         statuses: ["INTRO_EMAIL_SENT","EMAIL_SENT"],           color: "border-blue-800/40 bg-blue-900/10",       dot: "bg-blue-400"   },
  { key: "whatsapp", label: "3. WhatsApp",      statuses: ["WHATSAPP_SENT"],                           color: "border-emerald-800/30 bg-emerald-900/10", dot: "bg-emerald-400"},
  { key: "ai_call",  label: "4. AI Call",       statuses: ["AI_CALLED"],                               color: "border-violet-800/30 bg-violet-900/10",   dot: "bg-violet-400" },
  { key: "founder",  label: "5. Founder Call",  statuses: ["FOUNDER_CALLED","REPLIED"],                color: "border-amber-800/30 bg-amber-900/10",     dot: "bg-amber-400"  },
  { key: "meeting",  label: "6. Meeting",       statuses: ["MEETING_BOOKED","MEETING_COMPLETED"],      color: "border-cyan-800/30 bg-cyan-900/10",       dot: "bg-cyan-400"   },
  { key: "sample",   label: "7. Sample",        statuses: ["SAMPLE_SENT"],                             color: "border-orange-800/30 bg-orange-900/10",   dot: "bg-orange-400" },
  { key: "proposal", label: "8. Proposal",      statuses: ["PROPOSAL_SENT"],                           color: "border-rose-800/30 bg-rose-900/10",       dot: "bg-rose-400"   },
  { key: "won",      label: "9. Won",           statuses: ["ORDER_WON","ONBOARDED"],                   color: "border-green-800/30 bg-green-900/10",     dot: "bg-green-400"  },
  { key: "reorder",  label: "10. Reorder",      statuses: ["REORDER_PREDICTED","ACCOUNT_GROWTH","UPSELL_OFFERED"], color: "border-teal-800/30 bg-teal-900/10", dot: "bg-teal-400"},
];

const TENDER_PIPELINE = [
  { key: "found",     label: "1. Found",      statuses: ["DISCOVERED","QUALIFIED"],        color: "border-gray-700 bg-gray-800/40",         dot: "bg-gray-500"   },
  { key: "eligible",  label: "2. Eligible",   statuses: ["TENDER_ELIGIBLE"],               color: "border-blue-800/40 bg-blue-900/10",      dot: "bg-blue-400"   },
  { key: "emd",       label: "3. EMD",        statuses: ["TENDER_EMD"],                    color: "border-amber-800/30 bg-amber-900/10",    dot: "bg-amber-400"  },
  { key: "bid",       label: "4. Bid Filed",  statuses: ["EMAIL_SENT","INTRO_EMAIL_SENT"], color: "border-indigo-800/30 bg-indigo-900/10",  dot: "bg-indigo-400" },
  { key: "technical", label: "5. Technical",  statuses: ["REPLIED","MEETING_BOOKED"],      color: "border-cyan-800/30 bg-cyan-900/10",      dot: "bg-cyan-400"   },
  { key: "financial", label: "6. Financial",  statuses: ["PROPOSAL_SENT"],                 color: "border-rose-800/30 bg-rose-900/10",      dot: "bg-rose-400"   },
  { key: "awarded",   label: "7. Awarded",    statuses: ["ORDER_WON","ONBOARDED"],         color: "border-green-800/30 bg-green-900/10",    dot: "bg-green-400"  },
];

// V1.1: RRS comes from the backend Decision Engine (lead.rrs) — never computed in UI.
function leadRRS(lead: HubLead): number {
  return lead.rrs ?? 0;
}

// ── TouchpointEngine ───────────────────────────────────────────
function TouchpointEngine({ channel, leads, updateStatus, showToast }: {
  channel: ChannelConfig;
  leads: HubLead[];
  updateStatus: (company: string, status: string) => Promise<void>;
  showToast: (m: string, t: "ok"|"err"|"info") => void;
}) {
  const [emailModal, setEmailModal] = React.useState<{ lead: HubLead; body: string } | null>(null);
  const [emailSending, setEmailSending] = React.useState(false);
  const [callModal, setCallModal] = React.useState<HubLead | null>(null);
  const pipeline = channel.tenderMode ? TENDER_PIPELINE : STANDARD_PIPELINE;

  const openEmail = (lead: HubLead) =>
    setEmailModal({ lead, body: channel.emailBody(lead.contact_name || "", lead.city || "") });

  const sendEmail = async () => {
    // V1.1: queue a draft in the Approval Inbox — never send directly.
    if (!emailModal) return;
    const { lead, body } = emailModal;
    if (!lead.email) { showToast("No email for this lead", "err"); return; }
    setEmailSending(true);
    try {
      const res = await fetch("/api/v1/b2b/distributor-campaign/send-one", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to_email: lead.email, to_name: lead.contact_name || "", city: lead.city || "", subject: channel.emailSubject, body }),
      });
      if (!res.ok) throw new Error();
      showToast("Draft queued for approval — " + lead.company, "ok");
      setEmailModal(null);
    } catch { showToast("Draft failed — check backend", "err"); }
    finally { setEmailSending(false); }
  };

  const sendWhatsApp = (lead: HubLead) => {
    const phone = (lead.whatsapp_number || lead.phone || "").replace(/\D/g, "");
    const msg = channel.whatsappMsg(lead.contact_name || "", lead.city || "");
    window.open("https://api.whatsapp.com/send?phone=" + phone + "&text=" + encodeURIComponent(msg), "_blank");
    updateStatus(lead.company, "WHATSAPP_SENT");
    showToast("WhatsApp opened — " + lead.company, "info");
  };

  const getNextCTA = (lead: HubLead, stageKey: string) => {
    if (channel.tenderMode) return null;
    if (stageKey === "cold") return lead.email
      ? <button onClick={() => openEmail(lead)} className="cta-btn bg-blue-600/20 hover:bg-blue-600 text-blue-400 hover:text-white border-blue-600/30">Approve Email</button>
      : <span className="cta-dim">No email on file</span>;
    if (stageKey === "email")
      return <button onClick={() => sendWhatsApp(lead)} className="cta-btn bg-emerald-600/20 hover:bg-emerald-600 text-emerald-400 hover:text-white border-emerald-600/30">Send WhatsApp</button>;
    if (stageKey === "whatsapp")
      return <button onClick={() => setCallModal(lead)} className="cta-btn bg-violet-600/20 hover:bg-violet-600 text-violet-400 hover:text-white border-violet-600/30">AI Call (Hindi)</button>;
    if (stageKey === "ai_call") return (
      <div className="flex gap-1">
        <a href={"tel:" + (lead.phone || lead.whatsapp_number || "").replace(/\D/g, "")} className="cta-btn flex-1 text-center bg-amber-600/20 hover:bg-amber-600 text-amber-400 hover:text-white border-amber-600/30">Call Now</a>
        <button onClick={() => updateStatus(lead.company, "FOUNDER_CALLED")} className="cta-btn flex-1 bg-amber-600/20 hover:bg-amber-600 text-amber-400 hover:text-white border-amber-600/30">Mark Done</button>
      </div>
    );
    if (stageKey === "founder")
      return <button onClick={() => updateStatus(lead.company, "MEETING_BOOKED")} className="cta-btn bg-cyan-600/20 hover:bg-cyan-600 text-cyan-400 hover:text-white border-cyan-600/30">Book Meeting</button>;
    if (stageKey === "meeting")
      return <button onClick={() => updateStatus(lead.company, "SAMPLE_SENT")} className="cta-btn bg-orange-600/20 hover:bg-orange-600 text-orange-400 hover:text-white border-orange-600/30">Send Sample</button>;
    if (stageKey === "sample")
      return <button onClick={() => updateStatus(lead.company, "PROPOSAL_SENT")} className="cta-btn bg-rose-600/20 hover:bg-rose-600 text-rose-400 hover:text-white border-rose-600/30">Send Proposal</button>;
    if (stageKey === "proposal")
      return <button onClick={() => updateStatus(lead.company, "ORDER_WON")} className="cta-btn bg-green-600/20 hover:bg-green-600 text-green-400 hover:text-white border-green-600/30">Mark Won</button>;
    if (stageKey === "won")
      return <button onClick={() => updateStatus(lead.company, "REORDER_PREDICTED")} className="cta-btn bg-teal-600/20 hover:bg-teal-600 text-teal-400 hover:text-white border-teal-600/30">Flag Reorder</button>;
    return null;
  };

  return (
    <>
      {/* Stage flow summary — 5 per row, 2 rows for 10 stages */}
      <div className="grid grid-cols-5 gap-1 mb-4">
        {pipeline.map(col => {
          const n = leads.filter(l => col.statuses.includes(l.status || "")).length;
          const pillCls = n > 0 ? col.color : "border-gray-800/40 opacity-30";
          const numCls = n > 0 ? "text-white" : "text-gray-600";
          const shortLabel = col.label.replace(/^\d+\.\s*/, "");
          return (
            <div key={col.key} className={"flex flex-col items-center gap-0.5 py-1.5 px-1 rounded-lg border " + pillCls}>
              <span className={"text-[11px] font-black " + numCls}>{n}</span>
              <span className="text-[7px] text-gray-500 text-center leading-tight font-bold">{shortLabel}</span>
            </div>
          );
        })}
      </div>

      {/* Vertical stage rows */}
      <div className="space-y-2">
        {pipeline.map(col => {
          const colLeads = leads.filter(l => col.statuses.includes(l.status || ""));
          if (colLeads.length === 0) {
            return (
              <div key={col.key} className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-800/30 opacity-25">
                <span className={"w-1.5 h-1.5 rounded-full shrink-0 " + col.dot} />
                <span className="text-[8px] text-gray-600 font-bold uppercase tracking-widest">{col.label}</span>
                <span className="text-[8px] text-gray-700 ml-auto">empty</span>
              </div>
            );
          }
          return (
            <div key={col.key} className={"rounded-xl border overflow-hidden " + col.color}>
              <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5">
                <span className={"w-2 h-2 rounded-full shrink-0 " + col.dot} />
                <span className="text-[9px] font-black text-gray-300 uppercase tracking-widest">{col.label}</span>
                <span className={"ml-1 text-[8px] font-black px-1.5 py-0.5 rounded-full text-white " + col.dot}>{colLeads.length}</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2 p-3">
                {colLeads.map(lead => {
                  const rrs = leadRRS(lead);
                  const margin = Math.round((lead.estimated_value || 0) * 0.31 / 1000);
                  const rrsCls = rrs >= 75 ? "text-green-400" : rrs >= 50 ? "text-amber-400" : "text-gray-600";
                  return (
                    <div key={lead.id} className="bg-gray-950/70 border border-gray-800 rounded-lg p-2.5 hover:border-gray-600 transition-all flex flex-col gap-1.5">
                      <div className="flex items-start justify-between gap-1">
                        <p className="text-[10px] font-bold text-gray-200 line-clamp-1 leading-tight flex-1">{lead.company}</p>
                        <span className={"text-[8px] font-black shrink-0 " + rrsCls}>{rrs}</span>
                      </div>
                      <div className="flex items-center justify-between gap-1">
                        <p className="text-[8px] text-gray-500 line-clamp-1 flex-1">
                          {lead.city}{lead.contact_name ? " | " + lead.contact_name : ""}
                        </p>
                        <span className="text-[8px] text-emerald-400 font-bold shrink-0">Rs.{margin}k</span>
                      </div>
                      {getNextCTA(lead, col.key)}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Email Approval Modal */}
      {emailModal && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-gray-900 border border-blue-500/30 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[90vh]">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
              <div>
                <p className="text-[11px] font-black text-gray-100 uppercase tracking-widest">Approve Intro Email</p>
                <p className="text-[9px] text-gray-500 mt-0.5">{emailModal.lead.company} | {emailModal.lead.email}</p>
              </div>
              <button onClick={() => setEmailModal(null)} className="text-gray-500 hover:text-gray-300 text-lg leading-none">x</button>
            </div>
            <div className="px-5 py-3 border-b border-gray-800 bg-gray-950/40">
              <p className="text-[8px] text-gray-500 font-black uppercase tracking-widest">Subject</p>
              <p className="text-[10px] text-gray-300 mt-0.5">{channel.emailSubject}</p>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              <p className="text-[8px] text-gray-500 font-black uppercase tracking-widest mb-2">Body — edit before sending</p>
              <textarea value={emailModal.body} onChange={e => setEmailModal({ ...emailModal, body: e.target.value })}
                className="w-full bg-gray-950 border border-gray-700 rounded-lg p-3 text-[10px] text-gray-200 font-mono leading-relaxed resize-none focus:outline-none focus:border-blue-500" rows={18} />
            </div>
            <div className="flex gap-3 px-5 py-4 border-t border-gray-800">
              <button onClick={() => setEmailModal(null)} className="flex-1 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs font-bold rounded-lg transition-all">Cancel</button>
              <button onClick={sendEmail} disabled={emailSending}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-black rounded-lg transition-all flex items-center justify-center gap-2">
                {emailSending ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Sending...</> : "Approve & Send"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* AI Call Script Modal */}
      {callModal && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-gray-900 border border-violet-500/30 rounded-xl w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
              <div>
                <p className="text-[11px] font-black text-gray-100 uppercase tracking-widest">AI Call Script (Hindi)</p>
                <p className="text-[9px] text-gray-500 mt-0.5">{callModal.company} | {callModal.phone || callModal.whatsapp_number || "No phone"}</p>
              </div>
              <button onClick={() => setCallModal(null)} className="text-gray-500 hover:text-gray-300 text-lg leading-none">x</button>
            </div>
            <div className="px-5 py-4 space-y-2.5 max-h-96 overflow-y-auto">
              {channel.aiScript(callModal.contact_name || "", callModal.city || "").map(s => (
                <div key={s.label} className="bg-gray-950 border border-gray-800 rounded-lg p-3">
                  <p className="text-[8px] font-black text-violet-400 uppercase tracking-widest mb-1">{s.label}</p>
                  <p className="text-[10px] text-gray-300 leading-relaxed">{s.text}</p>
                </div>
              ))}
              <p className="text-[9px] text-gray-600 text-center pt-1">Vapi voice AI — coming next sprint</p>
            </div>
            <div className="flex gap-3 px-5 py-4 border-t border-gray-800">
              <a href={"tel:" + (callModal.phone || callModal.whatsapp_number || "").replace(/\D/g, "")}
                className="flex-1 py-2.5 bg-amber-600/20 hover:bg-amber-600 text-amber-400 hover:text-white border border-amber-600/30 text-[10px] font-black rounded-lg transition-all text-center">
                Call Now
              </a>
              <button onClick={() => { updateStatus(callModal.company, "AI_CALLED"); setCallModal(null); showToast(callModal.company + " marked Called", "ok"); }}
                className="flex-1 py-2.5 bg-violet-600 hover:bg-violet-500 text-white text-[10px] font-black rounded-lg transition-all">
                Mark Called
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── FounderGrowthHub ───────────────────────────────────────────
interface FounderGrowthHubProps {
  b2bLeads: HubLead[];
  showToast: (m: string, t: "ok"|"err"|"info") => void;
  fetchData: () => void;
}

export default function FounderGrowthHub({ b2bLeads, showToast, fetchData }: FounderGrowthHubProps) {
  const [activeChannel, setActiveChannel] = React.useState<ChannelId>("distributor");
  const [campaignSending, setCampaignSending] = React.useState(false);
  const [campaignSent, setCampaignSent] = React.useState<Set<number>>(new Set());

  const updateStatus = React.useCallback(async (company: string, status: string) => {
    try {
      await fetch("/api/v1/b2b/leads/status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, status }),
      });
      fetchData();
    } catch { showToast("Status update failed", "err"); }
  }, [fetchData, showToast]);

  const allChannels = [...CHANNELS, {
    id: "analytics" as ChannelId, label: "Analytics", icon: "A",
    divisions: [] as string[], accent: "gray", emailSubject: "", discoverySegments: [],
    emailBody: () => "", whatsappMsg: () => "", aiScript: () => [],
  }];

  const channel = CHANNELS.find(c => c.id === activeChannel);
  const channelLeads = activeChannel === "analytics"
    ? b2bLeads
    : b2bLeads.filter(l => (channel?.divisions || []).includes(l.division || ""));

  const totalPipeline = b2bLeads.reduce((s, l) => s + (l.estimated_value || 0), 0);

  // Money Today Queue
  const moneyQueue = b2bLeads.filter(l => {
    const rrs = leadRRS(l);
    const hot = ["REPLIED","MEETING_BOOKED","MEETING_COMPLETED","SAMPLE_SENT","PROPOSAL_SENT"].includes(l.status || "");
    return rrs >= 75 && (l.estimated_value || 0) >= 25000 && hot;
  }).slice(0, 10);

  // Campaign helpers
  const coldWithEmail = channelLeads.filter(l =>
    ["DISCOVERED","QUALIFIED","COLD"].includes(l.status || "") && l.email
  );

  const sendBulkCampaign = async () => {
    // V1.1: bulk campaigns create drafts in the Approval Inbox — never send.
    if (!channel) return;
    setCampaignSending(true);
    try {
      const res = await fetch("/api/v1/b2b/email/generate-drafts", { method: "POST" });
      const data = res.ok ? await res.json() : { drafted: 0 };
      setCampaignSent(new Set(coldWithEmail.map(l => l.id)));
      showToast(`${data.drafted || 0} drafts queued — review in the Approval Inbox`, "ok");
    } catch { showToast("Draft generation failed", "err"); }
    setCampaignSending(false);
    fetchData();
  };

  // Analytics data
  const byChannel = CHANNELS.filter(c => c.id !== "analytics").map(c => ({
    label: c.label, icon: c.icon,
    count: b2bLeads.filter(l => c.divisions.includes(l.division || "")).length,
    value: b2bLeads.filter(l => c.divisions.includes(l.division || "")).reduce((s, l) => s + (l.estimated_value || 0), 0),
  }));

  const funnelStages = [
    { label: "Discovery", n: b2bLeads.filter(l => ["DISCOVERED","QUALIFIED","COLD"].includes(l.status||"")).length },
    { label: "Email",     n: b2bLeads.filter(l => ["INTRO_EMAIL_SENT","EMAIL_SENT"].includes(l.status||"")).length },
    { label: "WhatsApp",  n: b2bLeads.filter(l => l.status === "WHATSAPP_SENT").length },
    { label: "Call",      n: b2bLeads.filter(l => ["AI_CALLED","FOUNDER_CALLED"].includes(l.status||"")).length },
    { label: "Meeting",   n: b2bLeads.filter(l => ["REPLIED","MEETING_BOOKED","MEETING_COMPLETED"].includes(l.status||"")).length },
    { label: "Sample",    n: b2bLeads.filter(l => l.status === "SAMPLE_SENT").length },
    { label: "Proposal",  n: b2bLeads.filter(l => l.status === "PROPOSAL_SENT").length },
    { label: "Won",       n: b2bLeads.filter(l => ["ORDER_WON","ONBOARDED"].includes(l.status||"")).length },
  ];

  return (
    <div className="bg-gray-900 border border-indigo-500/20 rounded-xl shadow-2xl mb-6 overflow-hidden">
      {/* Header */}
      <div className="px-5 pt-5 pb-4 border-b border-gray-800">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-4">
          <div>
            <h2 className="text-xs font-black text-white uppercase tracking-widest flex items-center gap-2">
              <Target className="w-4 h-4 text-indigo-400" />
              Founder Growth Hub
            </h2>
            <p className="text-[10px] text-gray-500 mt-0.5">
              {b2bLeads.length} leads | Rs.{(totalPipeline / 100000).toFixed(1)}L pipeline | Epicentre: Abohar
            </p>
          </div>
          {moneyQueue.length > 0 && (
            <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-1.5">
              <span className="text-amber-400 text-[10px] font-black">{moneyQueue.length} leads need Founder action TODAY</span>
            </div>
          )}
        </div>

        {/* Channel tabs */}
        <div className="flex gap-1 flex-wrap">
          {allChannels.map(c => {
            const count = c.id === "analytics" ? b2bLeads.length
              : b2bLeads.filter(l => c.divisions.includes(l.division || "")).length;
            const isActive = activeChannel === c.id;
            return (
              <button key={c.id} onClick={() => setActiveChannel(c.id)}
                className={"flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-wider transition-all " +
                  (isActive ? "bg-indigo-600 text-white shadow-lg" : "bg-gray-800 text-gray-500 hover:text-gray-300")}>
                <span>{c.icon}</span>
                <span className="hidden sm:inline">{c.label}</span>
                {count > 0 && (
                  <span className={"text-[8px] px-1 rounded-full " + (isActive ? "bg-white/20 text-white" : "bg-gray-700 text-gray-400")}>
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="p-5">
        {/* Money Today Queue */}
        {moneyQueue.length > 0 && activeChannel !== "analytics" && (
          <div className="mb-5 bg-amber-500/5 border border-amber-500/20 rounded-xl p-4">
            <p className="text-[9px] font-black uppercase tracking-widest text-amber-400 mb-3">Money Today — Founder Action Required</p>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {moneyQueue.map(lead => (
                <div key={lead.id} className="shrink-0 w-44 bg-gray-950 border border-amber-500/20 rounded-lg p-2.5">
                  <p className="text-[9px] font-bold text-gray-200 line-clamp-1">{lead.company}</p>
                  <p className="text-[8px] text-gray-500">{lead.city}</p>
                  <p className="text-[8px] text-emerald-400 font-bold mt-1">Rs.{Math.round((lead.estimated_value || 0) * 0.31 / 1000)}k margin</p>
                  <p className="text-[8px] text-amber-400 font-bold mt-0.5">{lead.status}</p>
                  {(lead.phone || lead.whatsapp_number) && (
                    <a href={"tel:" + (lead.phone || lead.whatsapp_number || "").replace(/\D/g, "")}
                      className="mt-2 block text-center text-[8px] font-black py-1 bg-amber-600/20 hover:bg-amber-600 text-amber-400 hover:text-white border border-amber-600/30 rounded transition-all">
                      Call Now
                    </a>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Analytics */}
        {activeChannel === "analytics" ? (
          <div className="space-y-6">
            <div>
              <p className="text-[9px] font-black uppercase tracking-widest text-gray-500 mb-3">Revenue Pipeline by Channel</p>
              <div className="space-y-2">
                {byChannel.map(c => (
                  <div key={c.label} className="flex items-center gap-3">
                    <span className="text-[10px] w-36 text-gray-400 shrink-0">{c.icon} {c.label}</span>
                    <div className="flex-1 bg-gray-800 rounded-full h-2 overflow-hidden">
                      <div className="h-2 bg-indigo-500 rounded-full"
                        style={{ width: totalPipeline > 0 ? (c.value / totalPipeline * 100).toFixed(0) + "%" : "0%" }} />
                    </div>
                    <span className="text-[9px] font-bold text-gray-300 w-16 text-right">Rs.{(c.value / 100000).toFixed(1)}L</span>
                    <span className="text-[9px] text-gray-600 w-6 text-right">{c.count}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-[9px] font-black uppercase tracking-widest text-gray-500 mb-3">Universal Conversion Funnel</p>
              <div className="flex items-end gap-2 h-24">
                {funnelStages.map((s, i) => {
                  const max = funnelStages[0].n || 1;
                  const h = Math.max(8, (s.n / max) * 96);
                  return (
                    <div key={s.label} className="flex-1 flex flex-col items-center gap-1">
                      <span className="text-[8px] font-black text-gray-200">{s.n}</span>
                      <div className="w-full rounded-t" style={{ height: h + "px", background: "hsl(" + (220 + i * 15) + ", 70%, 50%)" }} />
                      <span className="text-[7px] text-gray-600 text-center leading-tight">{s.label}</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: "Total Leads", val: b2bLeads.length, color: "text-indigo-400" },
                { label: "Pipeline", val: "Rs." + (totalPipeline / 100000).toFixed(1) + "L", color: "text-emerald-400" },
                { label: "Emailed", val: b2bLeads.filter(l => ["INTRO_EMAIL_SENT","EMAIL_SENT"].includes(l.status||"")).length, color: "text-blue-400" },
                { label: "Won", val: b2bLeads.filter(l => l.status === "ORDER_WON").length, color: "text-green-400" },
              ].map(s => (
                <div key={s.label} className="bg-gray-950 border border-gray-800 rounded-lg p-3 text-center">
                  <p className={"text-lg font-black " + s.color}>{s.val}</p>
                  <p className="text-[9px] text-gray-500">{s.label}</p>
                </div>
              ))}
            </div>
          </div>
        ) : channel ? (
          <div>
            {/* Channel pipeline */}
            <div className="flex items-center justify-between mb-3">
              <p className="text-[9px] font-black uppercase tracking-widest text-gray-500">
                {channel.icon} {channel.label} | {channelLeads.length} leads
              </p>
              <button
                onClick={sendBulkCampaign}
                disabled={campaignSending || coldWithEmail.length === 0}
                className="text-[8px] font-black px-2.5 py-1 bg-blue-600/20 hover:bg-blue-600 text-blue-400 hover:text-white border border-blue-600/30 rounded-lg transition-all disabled:opacity-30 flex items-center gap-1">
                {campaignSending
                  ? <><RefreshCw className="w-3 h-3 animate-spin" /> Sending...</>
                  : <><FileText className="w-3 h-3" /> Bulk Email ({coldWithEmail.length} cold)</>}
              </button>
            </div>
            <TouchpointEngine
              channel={channel}
              leads={channelLeads}
              updateStatus={updateStatus}
              showToast={showToast}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
