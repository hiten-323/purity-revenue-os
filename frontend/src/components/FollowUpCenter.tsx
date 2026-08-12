"use client";
import React, { useEffect, useState, useCallback } from "react";
import {
  Mail, MessageCircle, Eye, Edit3, CheckCircle, Send,
  ChevronDown, ChevronRight, RefreshCw, Zap, Clock,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface EmailPreview {
  subject: string;
  body: string;
  to: string;
  from: string;
}

interface FollowUpLead {
  draft_id: number;
  lead_id: number;
  company: string;
  contact_name: string;
  city: string;
  division: string;
  email: string;
  phone: string;
  whatsapp_number: string;
  email_opens: number;
  days_stalled: number;
  score: number;
  estimated_value: number;
  draft_status: string;
  email_preview: EmailPreview;
  whatsapp_message: string;
  whatsapp_url: string | null;
}

interface InboxData {
  leads: FollowUpLead[];
  total: number;
  total_pipeline: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function inr(n: number): string {
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(1)}Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(1)}L`;
  if (n >= 1e3) return `₹${(n / 1e3).toFixed(0)}K`;
  return `₹${n}`;
}

const DIV_COLOR: Record<string, string> = {
  distributor: "text-amber-400 bg-amber-500/10 border-amber-500/25",
  wholesale:   "text-orange-400 bg-orange-500/10 border-orange-500/25",
  corporate:   "text-blue-400 bg-blue-500/10 border-blue-500/25",
  horeca:      "text-green-400 bg-green-500/10 border-green-500/25",
  government:  "text-purple-400 bg-purple-500/10 border-purple-500/25",
  retail:      "text-cyan-400 bg-cyan-500/10 border-cyan-500/25",
  gifting:     "text-pink-400 bg-pink-500/10 border-pink-500/25",
};

// ─── Preview Modal ────────────────────────────────────────────────────────────

function EmailModal({
  lead, editBody, setEditBody, editMode, setEditMode,
  onClose, onSend, sending,
}: {
  lead: FollowUpLead;
  editBody: string;
  setEditBody: (v: string) => void;
  editMode: boolean;
  setEditMode: (v: boolean) => void;
  onClose: () => void;
  onSend: () => void;
  sending: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-amber-400" />
            <span className="text-sm font-black text-gray-100">Follow-up Email Preview</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-400 font-bold">
              2nd Touch · {lead.days_stalled}d stalled
            </span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xl px-1">×</button>
        </div>

        <div className="px-5 py-3 bg-gray-950/50 border-b border-gray-800 space-y-1.5 text-xs">
          <div className="flex gap-2">
            <span className="text-gray-500 w-12 shrink-0">To</span>
            <span className="text-gray-200 font-mono">{lead.email_preview.to}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-12 shrink-0">From</span>
            <span className="text-gray-400">{lead.email_preview.from}</span>
          </div>
          <div className="flex gap-2">
            <span className="text-gray-500 w-12 shrink-0">Subject</span>
            <span className="text-gray-200 font-medium">{lead.email_preview.subject}</span>
          </div>
          <div className="flex gap-2 mt-1">
            <span className="text-gray-500 w-12 shrink-0">Opens</span>
            <span className={`text-xs font-bold ${lead.email_opens > 0 ? "text-green-400" : "text-gray-600"}`}>
              {lead.email_opens > 0 ? `✓ Opened ${lead.email_opens}×` : "Not opened"}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {editMode ? (
            <textarea
              value={editBody}
              onChange={e => setEditBody(e.target.value)}
              className="w-full h-64 bg-gray-800 border border-gray-700 rounded-xl p-3 text-xs text-gray-300 font-mono resize-none focus:outline-none focus:border-amber-500"
            />
          ) : (
            <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap leading-relaxed">{editBody}</pre>
          )}
        </div>

        <div className="px-5 py-4 border-t border-gray-800 flex flex-wrap gap-2">
          <button
            onClick={onSend}
            disabled={sending}
            className="flex items-center gap-2 px-4 py-2.5 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-xs font-black rounded-xl transition-colors"
          >
            <Send className="w-3.5 h-3.5" />
            {sending ? "Sending…" : "Approve & Send"}
          </button>
          {!editMode ? (
            <button
              onClick={() => setEditMode(true)}
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
            onClick={onClose}
            className="flex items-center gap-2 px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-400 text-xs font-black rounded-xl transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── WhatsApp Bulk Modal ──────────────────────────────────────────────────────

function WhatsAppModal({
  leads, onClose, onDone,
}: {
  leads: FollowUpLead[];
  onClose: () => void;
  onDone: (leadIds: number[]) => void;
}) {
  const [sent, setSent] = useState<Set<number>>(new Set());

  const markSent = (leadId: number) => {
    setSent(prev => new Set(prev).add(leadId));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <MessageCircle className="w-4 h-4 text-green-400" />
            <span className="text-sm font-black text-gray-100">Bulk WhatsApp Follow-up</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 border border-green-500/20 text-green-400 font-bold">
              {leads.length} messages
            </span>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xl px-1">×</button>
        </div>

        <p className="px-5 py-3 text-[10px] text-gray-500 border-b border-gray-800">
          Click &ldquo;Send via WhatsApp&rdquo; for each contact — opens WhatsApp Web with the message pre-filled.
          Mark as sent when done.
        </p>

        <div className="flex-1 overflow-y-auto divide-y divide-gray-800">
          {leads.map(lead => {
            const isSent = sent.has(lead.lead_id);
            return (
              <div key={lead.lead_id} className={`px-5 py-3 ${isSent ? "opacity-40" : ""}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-black text-gray-200">{lead.company}</span>
                      {lead.contact_name && <span className="text-[10px] text-gray-500">{lead.contact_name}</span>}
                      <span className="text-[9px] text-gray-600">{lead.days_stalled}d stalled</span>
                    </div>
                    <pre className="mt-2 text-[9px] text-gray-500 font-mono whitespace-pre-wrap bg-gray-800/60 rounded-lg px-3 py-2 leading-relaxed max-h-24 overflow-y-auto">
                      {lead.whatsapp_message}
                    </pre>
                  </div>
                  <div className="flex flex-col gap-1.5 shrink-0 mt-0.5">
                    {lead.whatsapp_url && !isSent && (
                      <a
                        href={lead.whatsapp_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={() => setTimeout(() => markSent(lead.lead_id), 1500)}
                        className="flex items-center gap-1 px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white text-[10px] font-black rounded-lg transition-colors whitespace-nowrap"
                      >
                        <MessageCircle className="w-3 h-3" /> Send WA
                      </a>
                    )}
                    {isSent && (
                      <span className="flex items-center gap-1 text-[10px] text-green-400 font-black">
                        <CheckCircle className="w-3 h-3" /> Sent
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="px-5 py-4 border-t border-gray-800 flex justify-between items-center">
          <span className="text-[10px] text-gray-500">{sent.size} of {leads.length} sent</span>
          <button
            onClick={() => { onDone(Array.from(sent)); onClose(); }}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 text-xs font-black rounded-lg transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function FollowUpCenter() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<InboxData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [emailModal, setEmailModal] = useState<FollowUpLead | null>(null);
  const [waModal, setWaModal] = useState(false);
  const [editBody, setEditBody] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/v1/b2b/followup/inbox");
      if (r.ok) setData(await r.json());
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const generateDrafts = async () => {
    setGenerating(true);
    try {
      const r = await fetch("/api/v1/b2b/followup/generate-drafts", { method: "POST" });
      if (r.ok) {
        const d = await r.json();
        showToast(`✓ ${d.drafted} follow-up drafts generated`);
        await load();
      }
    } catch { /* silent */ }
    finally { setGenerating(false); }
  };

  const saveDraftEdit = async (draftId: number, body: string) => {
    await fetch(`/api/v1/b2b/email/draft/${draftId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
  };

  const sendDrafts = async (draftIds: number[], customBody?: string) => {
    setSending(true);
    try {
      const r = await fetch("/api/v1/b2b/email/approve-and-send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_ids: draftIds, custom_body: customBody || null }),
      });
      if (r.ok) {
        const d = await r.json();
        const total = (d.sent_real || 0) + (d.sent_simulated || 0);
        showToast(`✓ ${total} follow-up${total !== 1 ? "s" : ""} sent${d.simulate ? " (simulated)" : ""}`);
        setSelected(new Set());
        setEmailModal(null);
        await load();
      }
    } catch { /* silent */ }
    finally { setSending(false); }
  };

  const toggleSelect = (draftId: number) => {
    setSelected(prev => { const n = new Set(prev); n.has(draftId) ? n.delete(draftId) : n.add(draftId); return n; });
  };

  const leads = data?.leads || [];
  const selLeads = leads.filter(l => selected.has(l.draft_id));
  const waLeads = selLeads.filter(l => l.whatsapp_url);

  return (
    <>
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl">
          {toast}
        </div>
      )}

      {emailModal && (
        <EmailModal
          lead={emailModal}
          editBody={editBody}
          setEditBody={setEditBody}
          editMode={editMode}
          setEditMode={setEditMode}
          onClose={() => { setEmailModal(null); setEditMode(false); }}
          onSend={async () => {
            if (editMode && editBody !== emailModal.email_preview.body) {
              await saveDraftEdit(emailModal.draft_id, editBody);
            }
            await sendDrafts([emailModal.draft_id], editMode ? editBody : undefined);
          }}
          sending={sending}
        />
      )}

      {waModal && selLeads.length > 0 && (
        <WhatsAppModal
          leads={waLeads.length > 0 ? waLeads : selLeads}
          onClose={() => setWaModal(false)}
          onDone={() => { showToast("WhatsApp messages queued"); }}
        />
      )}

      <div className="mb-6 bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">

        <button
          onClick={() => { setOpen(v => !v); if (!open && leads.length === 0) load(); }}
          className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-800/40 transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="relative">
              <Clock className="w-4 h-4 text-amber-400" />
              {leads.length > 0 && (
                <span className="absolute -top-1.5 -right-1.5 min-w-[14px] h-3.5 bg-amber-500 rounded-full text-[8px] text-black flex items-center justify-center font-black px-0.5">
                  {leads.length > 9 ? "9+" : leads.length}
                </span>
              )}
            </div>
            <div className="text-left">
              <span className="text-sm font-black text-gray-100">Follow-up Center</span>
              <p className="text-[10px] text-gray-500 mt-0.5">2nd-touch emails + WhatsApp for stalled leads (3+ days)</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {data && data.total > 0 && (
              <div className="hidden md:flex items-center gap-3 text-[10px] font-bold">
                <span className="text-amber-400">{data.total} ready</span>
                <span className="text-blue-400">{inr(data.total_pipeline)} pipeline</span>
                <span className="text-green-400">{leads.filter(l => l.email_opens > 0).length} opened 1st email</span>
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
                  className="flex items-center gap-1.5 text-[11px] font-black px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white rounded-lg transition-colors"
                >
                  <Zap className={`w-3 h-3 ${generating ? "animate-pulse" : ""}`} />
                  {generating ? "Generating…" : "Generate Follow-ups"}
                </button>
                <button onClick={load} className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors">
                  <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
                </button>
              </div>
              {leads.length > 0 && (
                <div className="flex items-center gap-2 text-[10px]">
                  <button
                    onClick={() => setSelected(new Set(leads.map(l => l.draft_id)))}
                    className="text-blue-400 hover:text-blue-300 font-bold"
                  >
                    Select All
                  </button>
                  {selected.size > 0 && (
                    <button onClick={() => setSelected(new Set())} className="text-gray-500 hover:text-gray-300">Clear</button>
                  )}
                </div>
              )}
            </div>

            {/* Bulk action bar */}
            {selected.size > 0 && (
              <div className="px-5 py-3 bg-amber-500/5 border-b border-amber-500/20 flex items-center justify-between flex-wrap gap-3">
                <span className="text-xs font-black text-amber-400">{selected.size} selected</span>
                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    onClick={() => sendDrafts(Array.from(selected))}
                    disabled={sending}
                    className="flex items-center gap-1.5 px-3 py-2 bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-[11px] font-black rounded-lg transition-colors"
                  >
                    <Mail className="w-3 h-3" />
                    {sending ? "Sending…" : `Bulk Email (${selected.size})`}
                  </button>
                  <button
                    onClick={() => setWaModal(true)}
                    className="flex items-center gap-1.5 px-3 py-2 bg-green-700/30 hover:bg-green-700/50 border border-green-600/30 text-green-400 text-[11px] font-black rounded-lg transition-colors"
                  >
                    <MessageCircle className="w-3 h-3" />
                    Bulk WhatsApp ({selLeads.filter(l => l.whatsapp_url).length})
                  </button>
                  <button onClick={() => setSelected(new Set())} className="text-[10px] text-gray-500 hover:text-gray-300 px-2 py-2">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Table */}
            {leads.length === 0 ? (
              <div className="px-5 py-12 text-center">
                <Clock className="w-8 h-8 text-gray-700 mx-auto mb-3" />
                <p className="text-gray-500 text-sm font-medium">No stalled leads needing follow-up</p>
                <p className="text-gray-600 text-xs mt-1">
                  Leads move here 3 days after EMAIL_SENT with no reply.
                  Click &ldquo;Generate Follow-ups&rdquo; to draft messages.
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
                          onChange={e => e.target.checked
                            ? setSelected(new Set(leads.map(l => l.draft_id)))
                            : setSelected(new Set())}
                          className="w-3 h-3 accent-amber-500"
                        />
                      </th>
                      <th className="text-left px-3 py-2.5">Company</th>
                      <th className="text-left px-3 py-2.5">Contact</th>
                      <th className="text-left px-3 py-2.5">City</th>
                      <th className="text-left px-3 py-2.5">Segment</th>
                      <th className="text-center px-3 py-2.5">Stalled</th>
                      <th className="text-center px-3 py-2.5">1st Open</th>
                      <th className="text-center px-3 py-2.5">Email Draft</th>
                      <th className="text-center px-3 py-2.5">WhatsApp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800/60">
                    {leads.map(lead => {
                      const divColor = DIV_COLOR[(lead.division || "corporate").toLowerCase()] || "text-gray-400 bg-gray-800 border-gray-700";
                      const isSel = selected.has(lead.draft_id);

                      return (
                        <tr key={lead.draft_id} className={`transition-colors ${isSel ? "bg-amber-500/5" : "hover:bg-gray-800/30"}`}>
                          <td className="px-4 py-3">
                            <input type="checkbox" checked={isSel} onChange={() => toggleSelect(lead.draft_id)} className="w-3 h-3 accent-amber-500" />
                          </td>

                          <td className="px-3 py-3">
                            <div className="font-bold text-gray-200 whitespace-nowrap">{lead.company}</div>
                            <div className="text-[9px] text-gray-600 mt-0.5 font-mono truncate max-w-[120px]">{lead.email}</div>
                          </td>

                          <td className="px-3 py-3 text-gray-300 whitespace-nowrap">{lead.contact_name || "—"}</td>

                          <td className="px-3 py-3 text-gray-400 whitespace-nowrap">{lead.city || "—"}</td>

                          <td className="px-3 py-3">
                            <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${divColor}`}>
                              {lead.division || "—"}
                            </span>
                          </td>

                          <td className="px-3 py-3 text-center">
                            <span className={`text-sm font-black tabular-nums ${lead.days_stalled >= 7 ? "text-red-400" : "text-amber-400"}`}>
                              {lead.days_stalled}d
                            </span>
                          </td>

                          <td className="px-3 py-3 text-center">
                            {lead.email_opens > 0 ? (
                              <span className="text-[10px] font-black text-green-400">✓ {lead.email_opens}×</span>
                            ) : (
                              <span className="text-[10px] text-gray-600">—</span>
                            )}
                          </td>

                          <td className="px-3 py-3 text-center">
                            <div className="flex items-center justify-center gap-1">
                              <button
                                onClick={() => {
                                  setEmailModal(lead);
                                  setEditBody(lead.email_preview.body);
                                  setEditMode(false);
                                }}
                                className="p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-blue-400 transition-colors"
                                title="Preview email"
                              >
                                <Eye className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => {
                                  setEmailModal(lead);
                                  setEditBody(lead.email_preview.body);
                                  setEditMode(true);
                                }}
                                className="p-1.5 rounded-lg bg-blue-600/20 hover:bg-blue-600/40 border border-blue-600/30 text-blue-400 transition-colors"
                                title="Edit draft"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => sendDrafts([lead.draft_id])}
                                disabled={sending}
                                className="p-1.5 rounded-lg bg-green-600/20 hover:bg-green-600/40 border border-green-600/30 text-green-400 transition-colors disabled:opacity-40"
                                title="Send now"
                              >
                                <Send className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </td>

                          <td className="px-3 py-3 text-center">
                            {lead.whatsapp_url ? (
                              <a
                                href={lead.whatsapp_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-green-600/20 hover:bg-green-600/40 border border-green-600/30 text-green-400 text-[10px] font-black rounded-lg transition-colors"
                              >
                                <MessageCircle className="w-3 h-3" /> WA
                              </a>
                            ) : (
                              <span className="text-[10px] text-gray-600">No phone</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Note */}
            <div className="px-5 py-3 border-t border-gray-800 bg-gray-950/30">
              <p className="text-[9px] text-gray-600">
                Follow-up emails are personalised based on whether the recipient opened the 1st email (open tracking pixel).
                WhatsApp messages open in WhatsApp Web with the message pre-filled — click Send there to deliver.
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
