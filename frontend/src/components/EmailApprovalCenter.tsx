"use client";
import React, { useEffect, useState, useCallback } from "react";
import {
  Mail, Eye, CheckCircle, Edit3, Clock, XCircle,
  ChevronDown, ChevronRight, RefreshCw, Send,
  DollarSign, BarChart2, Zap, AlertTriangle,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface EmailPreview {
  to: string;
  to_name: string;
  from: string;
  from_name: string;
  subject: string;
  body: string;
  first_name: string;
}

interface QualityFlag {
  id: string;
  label: string;
  tip: string;
  detail?: string;
}

interface EmailQuality {
  score: number;
  grade: "A" | "B" | "C" | "F";
  blocks: QualityFlag[];
  warnings: QualityFlag[];
  passed: boolean;
}

interface InboxLead {
  draft_id: number;
  lead_id: number;
  company: string;
  contact_name: string;
  city: string;
  division: string;
  segment: string;
  email: string;
  aps: number;
  rrs: number;
  estimated_value: number;
  expected_margin: number;
  email_verification_status: string;
  email_confidence: number;
  is_government: boolean;
  lead_source: string;
  quality?: EmailQuality;
  preview: EmailPreview;
  created_at: string | null;
}

interface InboxData {
  leads: InboxLead[];
  total: number;
  total_pipeline: number;
  total_margin: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function inr(n: number): string {
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(1)}Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(1)}L`;
  if (n >= 1e3) return `₹${(n / 1e3).toFixed(0)}K`;
  return `₹${n}`;
}

const DIVISION_COLORS: Record<string, string> = {
  distributor: "text-amber-400 bg-amber-500/10 border-amber-500/25",
  wholesaler:   "text-orange-400 bg-orange-500/10 border-orange-500/25",
  wholesale:   "text-orange-400 bg-orange-500/10 border-orange-500/25",
  corporate_office:   "text-blue-400 bg-blue-500/10 border-blue-500/25",
  corporate:   "text-blue-400 bg-blue-500/10 border-blue-500/25",
  office_pantry: "text-blue-400 bg-blue-500/10 border-blue-500/25",
  manufacturing: "text-cyan-400 bg-cyan-500/10 border-cyan-500/25",
  facility_management: "text-gray-400 bg-gray-500/10 border-gray-500/25",
  hotel:      "text-green-400 bg-green-500/10 border-green-500/25",
  horeca:      "text-green-400 bg-green-500/10 border-green-500/25",
  restaurant:  "text-green-400 bg-green-500/10 border-green-500/25",
  cafe:        "text-green-400 bg-green-500/10 border-green-500/25",
  hospital:    "text-red-400 bg-red-500/10 border-red-500/25",
  school:      "text-purple-400 bg-purple-500/10 border-purple-500/25",
  college:     "text-purple-400 bg-purple-500/10 border-purple-500/25",
  government:  "text-purple-400 bg-purple-500/10 border-purple-500/25",
  corporate_gifting:     "text-pink-400 bg-pink-500/10 border-pink-500/25",
  gifting:     "text-pink-400 bg-pink-500/10 border-pink-500/25",
  private_label: "text-cyan-400 bg-cyan-500/10 border-cyan-500/25",
  exporter:    "text-amber-400 bg-amber-500/10 border-amber-500/25",
  institutional_buyer: "text-purple-400 bg-purple-500/10 border-purple-500/25",
};

const VERIF_BADGE: Record<string, string> = {
  VALID:      "text-green-400 bg-green-500/10 border-green-500/20",
  CATCH_ALL:  "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
  RISKY:      "text-amber-400 bg-amber-500/10 border-amber-500/20",
  UNVERIFIED: "text-gray-400 bg-gray-800 border-gray-700",
  INVALID:    "text-red-400 bg-red-500/10 border-red-500/20",
};

// ─── Preview Modal ────────────────────────────────────────────────────────────

function PreviewModal({
  lead, editBody, setEditBody, editMode, setEditMode,
  onClose, onApprove, onApproveLater, onReject, sending,
}: {
  lead: InboxLead;
  editBody: string;
  setEditBody: (v: string) => void;
  editMode: boolean;
  setEditMode: (v: boolean) => void;
  onClose: () => void;
  onApprove: (force?: boolean) => void;
  onApproveLater: () => void;
  onReject: () => void;
  sending: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-blue-400" />
            <span className="text-sm font-black text-gray-100">Email Preview</span>
            {lead.is_government && (
              <span className="ml-2 text-[10px] font-black px-2 py-0.5 rounded-full bg-purple-500/15 border border-purple-500/30 text-purple-400">
                GOV · MANUAL ONLY
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xl leading-none px-1">×</button>
        </div>

        {/* Meta */}
        <div className="px-5 py-3 bg-gray-950/50 border-b border-gray-800 space-y-1.5 text-xs">
          <div className="flex gap-2">
            <span className="text-gray-500 w-12 shrink-0">To</span>
            <span className="text-gray-200 font-mono">{lead.preview.to}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-12 shrink-0">From</span>
            <span className="text-gray-400">{lead.preview.from_name} &lt;{lead.preview.from}&gt;</span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-12 shrink-0">Subject</span>
            <span className="text-gray-200 font-medium">{lead.preview.subject}</span>
          </div>
        </div>

        {/* Quality flags panel */}
        {lead.quality && (!lead.quality.passed || lead.quality.warnings.length > 0) && (
          <div className={`px-5 py-3 border-b ${!lead.quality.passed ? "bg-red-500/5 border-red-500/20" : "bg-amber-500/5 border-amber-500/20"}`}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`text-[10px] font-black uppercase tracking-wider ${!lead.quality.passed ? "text-red-400" : "text-amber-400"}`}>
                Quality Gate — Score {lead.quality.score}/100 (Grade {lead.quality.grade})
              </span>
              {!lead.quality.passed && (
                <span className="text-[9px] px-1.5 py-0.5 bg-red-600/20 border border-red-500/30 text-red-400 rounded font-bold">
                  BLOCKED
                </span>
              )}
            </div>
            {lead.quality.blocks.map(f => (
              <div key={f.id} className="flex items-start gap-2 mb-1.5">
                <span className="text-red-400 text-[10px] font-black shrink-0 mt-0.5">✗</span>
                <div>
                  <span className="text-[10px] font-black text-red-300">{f.label}</span>
                  {f.detail && <span className="text-[9px] text-red-500 ml-1">({f.detail})</span>}
                  <p className="text-[9px] text-gray-500 mt-0.5">{f.tip}</p>
                </div>
              </div>
            ))}
            {lead.quality.warnings.map(f => (
              <div key={f.id} className="flex items-start gap-2 mb-1.5">
                <span className="text-amber-400 text-[10px] font-black shrink-0 mt-0.5">⚠</span>
                <div>
                  <span className="text-[10px] font-black text-amber-300">{f.label}</span>
                  {f.detail && <span className="text-[9px] text-amber-600 ml-1">({f.detail})</span>}
                  <p className="text-[9px] text-gray-500 mt-0.5">{f.tip}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {editMode ? (
            <textarea
              value={editBody}
              onChange={e => setEditBody(e.target.value)}
              className="w-full h-64 bg-gray-800 border border-gray-700 rounded-xl p-3 text-xs text-gray-300 font-mono resize-none focus:outline-none focus:border-blue-500"
            />
          ) : (
            <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap leading-relaxed">{lead.preview.body}</pre>
          )}
        </div>

        {/* Attachments */}
        <div className="px-5 py-3 border-t border-gray-800 flex items-center gap-3 flex-wrap">
          <span className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">Attachments</span>
          {["Product Catalogue", "Price List", "Company Profile"].map(a => (
            <span key={a} className="flex items-center gap-1 text-[10px] text-green-400 font-medium">
              <CheckCircle className="w-3 h-3" /> {a}
            </span>
          ))}
        </div>

        {/* Actions */}
        <div className="px-5 py-4 border-t border-gray-800 flex flex-wrap gap-2">
          {lead.quality && !lead.quality.passed ? (
            <div className="flex flex-col gap-1.5">
              <span className="text-[9px] text-red-400 font-bold">
                ⛔ Quality gate blocked — edit the draft to fix issues, then send.
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => { setEditMode(true); }}
                  className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-black rounded-xl transition-colors"
                >
                  <Edit3 className="w-3.5 h-3.5" /> Edit & Fix
                </button>
                <button
                  onClick={() => onApprove(true)}
                  disabled={sending}
                  className="flex items-center gap-2 px-3 py-2.5 bg-red-800/40 hover:bg-red-700/50 border border-red-600/40 disabled:opacity-50 text-red-300 text-[10px] font-black rounded-xl transition-colors"
                >
                  {sending ? "Sending…" : "Override & Send Anyway"}
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => onApprove(false)}
              disabled={sending}
              className="flex items-center gap-2 px-4 py-2.5 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-xs font-black rounded-xl transition-colors"
            >
              <CheckCircle className="w-3.5 h-3.5" />
              {sending ? "Sending…" : "Approve & Send"}
            </button>
          )}

          {!editMode ? (
            <button
              onClick={() => { setEditMode(true); setEditBody(lead.preview.body); }}
              className="flex items-center gap-2 px-4 py-2.5 bg-blue-600/20 hover:bg-blue-600/40 border border-blue-500/30 text-blue-400 text-xs font-black rounded-xl transition-colors"
            >
              <Edit3 className="w-3.5 h-3.5" /> Edit
            </button>
          ) : (
            <button
              onClick={() => setEditMode(false)}
              className="flex items-center gap-2 px-4 py-2.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs font-black rounded-xl transition-colors"
            >
              Preview
            </button>
          )}

          <button
            onClick={onApproveLater}
            className="flex items-center gap-2 px-4 py-2.5 bg-amber-600/20 hover:bg-amber-600/40 border border-amber-500/30 text-amber-400 text-xs font-black rounded-xl transition-colors"
          >
            <Clock className="w-3.5 h-3.5" /> Approve Later
          </button>

          <button
            onClick={onReject}
            className="flex items-center gap-2 px-4 py-2.5 bg-red-600/20 hover:bg-red-600/40 border border-red-500/30 text-red-400 text-xs font-black rounded-xl transition-colors"
          >
            <XCircle className="w-3.5 h-3.5" /> Reject
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function EmailApprovalCenter() {
  const [open, setOpen] = useState(true);
  const [data, setData] = useState<InboxData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [preview, setPreview] = useState<InboxLead | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [editBody, setEditBody] = useState("");
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/b2b/email/approval-inbox");
      if (r.ok) setData(await r.json());
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const generateDrafts = async () => {
    setGenerating(true);
    try {
      const r = await fetch("/api/v1/b2b/email/generate-drafts", { method: "POST" });
      if (r.ok) {
        const d = await r.json();
        showToast(`✓ ${d.drafted} drafts generated and ready for review`);
        await load();
      }
    } catch { /* silent */ }
    finally { setGenerating(false); }
  };

  const toggleSelect = (draftId: number) => {
    setSelected(prev => {
      const n = new Set(prev);
      n.has(draftId) ? n.delete(draftId) : n.add(draftId);
      return n;
    });
  };

  const selectAll = () => {
    if (data) setSelected(new Set(data.leads.map(l => l.draft_id)));
  };

  const clearSelected = () => setSelected(new Set());

  const sendDrafts = async (draftIds: number[], customBody?: string, force = false) => {
    setSending(true);
    try {
      const r = await fetch("/api/v1/b2b/email/approve-and-send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_ids: draftIds, custom_body: customBody || null, force_send: force }),
      });
      if (r.ok) {
        const d = await r.json();
        const blocked = (d.results || []).filter((x: { status: string }) => x.status === "quality_blocked");
        const total = (d.sent_real || 0) + (d.sent_simulated || 0);
        if (blocked.length > 0 && total === 0) {
          showToast(`⛔ ${blocked.length} email${blocked.length !== 1 ? "s" : ""} blocked by quality gate — fix issues or edit draft`);
        } else {
          showToast(`✓ ${total} email${total !== 1 ? "s" : ""} sent${d.simulate ? " (simulated)" : ""}${blocked.length > 0 ? ` · ${blocked.length} blocked` : ""}`);
        }
        setSelected(new Set());
        setPreview(null);
        await load();
      }
    } catch { /* silent */ }
    finally { setSending(false); }
  };

  const approveLater = async (draftId: number) => {
    await fetch(`/api/v1/b2b/email/approve-later/${draftId}`, { method: "POST" });
    showToast("Deferred — will stay in inbox");
    setPreview(null);
    await load();
  };

  const rejectDraft = async (draftId: number) => {
    await fetch(`/api/v1/b2b/email/reject-draft/${draftId}`, { method: "POST" });
    showToast("Email rejected and removed from inbox");
    setPreview(null);
    await load();
  };

  const saveDraftEdit = async (draftId: number, body: string) => {
    await fetch(`/api/v1/b2b/email/draft/${draftId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
  };

  const leads = data?.leads || [];
  const selLeads = leads.filter(l => selected.has(l.draft_id));
  const selPipeline = selLeads.reduce((a, l) => a + l.estimated_value, 0);
  const selMargin = selLeads.reduce((a, l) => a + l.expected_margin, 0);

  return (
    <>
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl animate-fade-in">
          {toast}
        </div>
      )}

      {preview && (
        <PreviewModal
          lead={preview}
          editBody={editBody}
          setEditBody={setEditBody}
          editMode={editMode}
          setEditMode={setEditMode}
          onClose={() => { setPreview(null); setEditMode(false); }}
          onApprove={async (force = false) => {
            if (editMode && editBody !== preview.preview.body) {
              await saveDraftEdit(preview.draft_id, editBody);
            }
            await sendDrafts([preview.draft_id], editMode ? editBody : undefined, force);
          }}
          onApproveLater={() => approveLater(preview.draft_id)}
          onReject={() => rejectDraft(preview.draft_id)}
          sending={sending}
        />
      )}

      <div id="email-approval" className="mb-6 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">

        {/* Collapsible header */}
        <button
          onClick={() => setOpen(v => !v)}
          className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-800/40 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="relative">
              <Mail className="w-4 h-4 text-blue-400" />
              {leads.length > 0 && (
                <span className="absolute -top-1.5 -right-1.5 min-w-[14px] h-3.5 bg-red-500 rounded-full text-[8px] text-white flex items-center justify-center font-black px-0.5">
                  {leads.length > 9 ? "9+" : leads.length}
                </span>
              )}
            </div>
            <div className="text-left">
              <span className="text-sm font-black text-gray-100">Approval Inbox</span>
              <p className="text-[10px] text-gray-500 mt-0.5">No email leaves without your approval</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {data && (
              <div className="hidden md:flex items-center gap-3 text-[10px] font-bold">
                <span className="text-gray-400">{data.total} pending</span>
                {data.total_pipeline > 0 && (
                  <>
                    <span className="text-blue-400">{inr(data.total_pipeline)} pipeline</span>
                    <span className="text-green-400">{inr(data.total_margin)} margin</span>
                  </>
                )}
              </div>
            )}
            {open ? <ChevronDown className="w-4 h-4 text-gray-600" /> : <ChevronRight className="w-4 h-4 text-gray-600" />}
          </div>
        </button>

        {open && (
          <div className="border-t border-gray-800">

            {/* Toolbar */}
            <div className="flex items-center justify-between px-5 py-3 bg-gray-950/40 border-b border-gray-800 gap-2 flex-wrap">
              <div className="flex items-center gap-2">
                <button
                  onClick={generateDrafts}
                  disabled={generating}
                  className="flex items-center gap-1.5 text-[11px] font-black px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                >
                  <Zap className={`w-3 h-3 ${generating ? "animate-pulse" : ""}`} />
                  {generating ? "Generating…" : "Generate Drafts"}
                </button>
                <button onClick={load} className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors">
                  <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
                </button>
              </div>
              {leads.length > 0 && (
                <div className="flex items-center gap-3 text-[10px]">
                  <button onClick={selectAll} className="text-blue-400 hover:text-blue-300 font-bold">Select All</button>
                  {selected.size > 0 && (
                    <button onClick={clearSelected} className="text-gray-500 hover:text-gray-300 font-medium">Clear</button>
                  )}
                </div>
              )}
            </div>

            {/* Bulk action bar */}
            {selected.size > 0 && (
              <div className="px-5 py-3 bg-blue-500/5 border-b border-blue-500/20 flex items-center justify-between flex-wrap gap-3">
                <div className="flex items-center gap-4 text-xs">
                  <span className="font-black text-blue-400">{selected.size} selected</span>
                  <span className="text-gray-400 flex items-center gap-1">
                    <BarChart2 className="w-3 h-3" />
                    Pipeline: <span className="text-gray-200 font-bold ml-1">{inr(selPipeline)}</span>
                  </span>
                  <span className="text-gray-400 flex items-center gap-1">
                    <DollarSign className="w-3 h-3" />
                    Margin: <span className="text-green-400 font-bold ml-1">{inr(selMargin)}</span>
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => sendDrafts(Array.from(selected))}
                    disabled={sending}
                    className="flex items-center gap-1.5 px-4 py-2 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-xs font-black rounded-lg transition-colors"
                  >
                    <Send className="w-3 h-3" />
                    {sending ? "Sending…" : `Approve & Send ${selected.size}`}
                  </button>
                  <button onClick={clearSelected} className="text-xs text-gray-500 hover:text-gray-300 px-2 py-2">Cancel</button>
                </div>
              </div>
            )}

            {/* Table */}
            {leads.length === 0 ? (
              <div className="px-5 py-12 text-center">
                <Mail className="w-8 h-8 text-gray-700 mx-auto mb-3" />
                <p className="text-gray-500 text-sm font-medium">No emails pending approval</p>
                <p className="text-gray-600 text-xs mt-1">
                  Click &ldquo;Generate Drafts&rdquo; to create emails for your DISCOVERED leads
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-gray-800 text-[10px] text-gray-500 font-bold uppercase tracking-wider">
                      <th className="px-4 py-2.5 w-8">
                        <input
                          type="checkbox"
                          checked={selected.size === leads.length && leads.length > 0}
                          onChange={e => e.target.checked ? selectAll() : clearSelected()}
                          className="w-3 h-3 accent-blue-500"
                        />
                      </th>
                      <th className="text-left px-3 py-2.5">Company</th>
                      <th className="text-left px-3 py-2.5">Contact</th>
                      <th className="text-left px-3 py-2.5">City</th>
                      <th className="text-left px-3 py-2.5">Segment</th>
                      <th className="text-right px-3 py-2.5">Exp. Margin</th>
                      <th className="text-center px-3 py-2.5">APS</th>
                      <th className="text-center px-3 py-2.5">RRS</th>
                      <th className="text-center px-3 py-2.5">Email</th>
                      <th className="text-center px-3 py-2.5">Quality</th>
                      <th className="text-center px-3 py-2.5">Preview</th>
                      <th className="text-center px-3 py-2.5">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/60">
                    {leads.map(lead => {
                      const divColor = DIVISION_COLORS[(lead.division || "corporate").toLowerCase()] || "text-gray-400 bg-gray-800 border-gray-700";
                      const verifColor = VERIF_BADGE[lead.email_verification_status] || VERIF_BADGE.UNVERIFIED;
                      const isSel = selected.has(lead.draft_id);

                      return (
                        <tr key={lead.draft_id} className={`transition-colors ${isSel ? "bg-blue-500/5" : "hover:bg-gray-800/30"}`}>
                          <td className="px-4 py-3">
                            <input type="checkbox" checked={isSel} onChange={() => toggleSelect(lead.draft_id)} className="w-3 h-3 accent-blue-500" />
                          </td>

                          <td className="px-3 py-3">
                            <div className="font-bold text-gray-200 leading-tight whitespace-nowrap">{lead.company}</div>
                            <div className="text-[9px] text-gray-600 mt-0.5">{lead.lead_source}</div>
                          </td>

                          <td className="px-3 py-3">
                            <div className="text-gray-300 whitespace-nowrap">{lead.contact_name || "—"}</div>
                            <div className="text-[9px] text-gray-600 font-mono mt-0.5 max-w-[130px] truncate">{lead.email}</div>
                          </td>

                          <td className="px-3 py-3 text-gray-400 whitespace-nowrap">{lead.city || "—"}</td>

                          <td className="px-3 py-3">
                            <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${divColor} whitespace-nowrap`}>
                              {lead.is_government ? "GOV" : lead.segment}
                            </span>
                          </td>

                          <td className="px-3 py-3 text-right">
                            <div className="text-green-400 font-black">{inr(lead.expected_margin)}</div>
                            <div className="text-[9px] text-gray-600">{inr(lead.estimated_value)}</div>
                          </td>

                          <td className="px-3 py-3 text-center">
                            <span className={`font-black text-sm tabular-nums ${lead.aps >= 70 ? "text-green-400" : lead.aps >= 40 ? "text-amber-400" : "text-gray-500"}`}>
                              {lead.aps}
                            </span>
                          </td>

                          <td className="px-3 py-3 text-center">
                            <span className={`font-black text-sm tabular-nums ${lead.rrs >= 70 ? "text-blue-400" : lead.rrs >= 40 ? "text-yellow-400" : "text-gray-500"}`}>
                              {lead.rrs}
                            </span>
                          </td>

                          <td className="px-3 py-3 text-center">
                            <span className={`text-[9px] font-black px-1.5 py-0.5 rounded border ${verifColor}`}>
                              {lead.email_verification_status === "UNVERIFIED" ? "?" : lead.email_verification_status.slice(0, 5)}
                              {lead.email_confidence > 0 && ` ${lead.email_confidence}%`}
                            </span>
                          </td>

                          {/* Quality gate badge */}
                          <td className="px-3 py-3 text-center">
                            {lead.quality ? (
                              <div className="flex flex-col items-center gap-0.5">
                                <span className={`text-xs font-black tabular-nums ${
                                  lead.quality.grade === "A" ? "text-green-400" :
                                  lead.quality.grade === "B" ? "text-blue-400" :
                                  lead.quality.grade === "C" ? "text-amber-400" : "text-red-400"
                                }`}>
                                  {lead.quality.grade} {lead.quality.score}
                                </span>
                                {!lead.quality.passed && (
                                  <span className="text-[8px] text-red-400 font-bold">
                                    {lead.quality.blocks.length} block{lead.quality.blocks.length !== 1 ? "s" : ""}
                                  </span>
                                )}
                                {lead.quality.passed && lead.quality.warnings.length > 0 && (
                                  <span className="text-[8px] text-amber-400 font-bold">
                                    {lead.quality.warnings.length} warn
                                  </span>
                                )}
                              </div>
                            ) : (
                              <span className="text-[9px] text-gray-600">—</span>
                            )}
                          </td>

                          <td className="px-3 py-3 text-center">
                            <button
                              onClick={() => { setPreview(lead); setEditMode(false); setEditBody(lead.preview.body); }}
                              className="p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-blue-400 transition-colors"
                              title="Preview email"
                            >
                              <Eye className="w-3.5 h-3.5" />
                            </button>
                          </td>

                          <td className="px-3 py-3">
                            <div className="flex items-center gap-1 justify-center">
                              <button
                                onClick={() => sendDrafts([lead.draft_id])}
                                disabled={sending}
                                title="Approve & Send"
                                className="p-1.5 rounded-lg bg-green-600/20 hover:bg-green-600/40 border border-green-600/30 text-green-400 transition-colors disabled:opacity-40"
                              >
                                <CheckCircle className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => { setPreview(lead); setEditMode(true); setEditBody(lead.preview.body); }}
                                title="Edit"
                                className="p-1.5 rounded-lg bg-blue-600/20 hover:bg-blue-600/40 border border-blue-600/30 text-blue-400 transition-colors"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => approveLater(lead.draft_id)}
                                title="Approve Later"
                                className="p-1.5 rounded-lg bg-amber-600/20 hover:bg-amber-600/40 border border-amber-600/30 text-amber-400 transition-colors"
                              >
                                <Clock className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => rejectDraft(lead.draft_id)}
                                title="Reject"
                                className="p-1.5 rounded-lg bg-red-600/20 hover:bg-red-600/40 border border-red-600/30 text-red-400 transition-colors"
                              >
                                <XCircle className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Workflow stages */}
            <div className="px-5 py-3 border-t border-gray-800 flex flex-wrap gap-x-2 gap-y-1 items-center">
              <span className="text-[9px] text-gray-600 font-bold uppercase mr-1">Flow</span>
              {[
                ["Discovered", "text-gray-500"],
                ["Draft Generated", "text-blue-400"],
                ["⟶ Awaiting Approval", "text-amber-400 font-black"],
                ["Approved", "text-green-400"],
                ["EMAIL_SENT", "text-emerald-400"],
                ["Opened", "text-yellow-400"],
                ["Replied", "text-purple-400"],
              ].map(([label, cls]) => (
                <span key={label} className={`text-[9px] font-mono ${cls}`}>{label}</span>
              ))}
            </div>

            {/* Policy note */}
            <div className="px-5 py-3 border-t border-gray-800 bg-gray-950/30">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-3 h-3 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <p className="text-[10px] text-amber-400 font-black">100% Manual Approval Enforced</p>
                  <p className="text-[9px] text-gray-600 mt-0.5">
                    All sources require approval: Google Maps · IndiaMART · TradeIndia · LinkedIn.
                    Auto-approval for Reorder Reminders and Proposal Follow-ups can be enabled later in settings.
                  </p>
                </div>
              </div>
            </div>

          </div>
        )}
      </div>
    </>
  );
}
