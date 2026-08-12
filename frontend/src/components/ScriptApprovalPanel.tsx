"use client";
import React, { useEffect, useState, useCallback } from "react";
import { Mail, MessageCircle, Phone, Briefcase, Globe, Check, Pencil, ChevronDown, ChevronRight, ShieldCheck } from "lucide-react";

// Channel Scripts — the "approve beforehand" gate. The founder reviews and
// approves each channel's outreach script per segment ONCE; the Auto-Warm
// Engine then only warms leads through approved channels.

interface Channel {
  channel: string; label: string; default_text: string;
  custom_body: string | null; approved: boolean; default_approved: boolean;
}
interface Segment { segment: string; channels: Channel[]; approved_count: number; total_channels: number; }

const ICON: Record<string, React.ReactNode> = {
  email: <Mail className="w-3.5 h-3.5" />, whatsapp: <MessageCircle className="w-3.5 h-3.5" />,
  ai_call: <Phone className="w-3.5 h-3.5" />, linkedin: <Briefcase className="w-3.5 h-3.5" />,
  facebook: <Globe className="w-3.5 h-3.5" />,
};

export default function ScriptApprovalPanel() {
  const [data, setData] = useState<Segment[] | null>(null);
  const [open, setOpen] = useState(true);
  const [seg, setSeg] = useState<string>("distributor");
  const [editing, setEditing] = useState<{ segment: string; channel: string; label: string } | null>(null);
  const [editText, setEditText] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3000); };

  const load = useCallback(async () => {
    const r = await fetch("/api/v1/outreach/scripts").catch(() => null);
    const d = r?.ok ? await r.json().catch(() => null) : null;
    if (d?.segments) setData(d.segments);
  }, []);
  useEffect(() => { load(); }, [load]);

  const setApproval = async (segment: string, channel: string, approved: boolean, custom_body?: string) => {
    await fetch("/api/v1/outreach/scripts/approve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segment, channel, approved, custom_body }),
    });
    showToast(approved ? "Script approved — warming enabled" : "Script paused");
    await load();
  };

  const approveAllForSegment = async (segment: string) => {
    await fetch(`/api/v1/outreach/scripts/approve-all?segment=${encodeURIComponent(segment)}`, { method: "POST" });
    showToast("All channels approved for this segment");
    await load();
  };

  const current = data?.find(s => s.segment === seg);
  const totalApproved = data?.reduce((a, s) => a + s.approved_count, 0) || 0;
  const totalChannels = data?.reduce((a, s) => a + s.total_channels, 0) || 0;

  return (
    <div className="mb-5 bg-gray-900 border border-cyan-500/20 rounded-xl overflow-hidden">
      {toast && <div className="fixed bottom-6 right-6 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs font-bold px-4 py-3 rounded-xl shadow-2xl">{toast}</div>}
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-gray-800/40">
        <div className="flex items-center gap-2.5">
          <ShieldCheck className="w-4 h-4 text-cyan-400" />
          <div className="text-left">
            <span className="text-xs font-black text-gray-100">Channel Scripts — Approve Beforehand</span>
            <p className="text-[10px] text-gray-500 mt-0.5">Approve each channel’s outreach script once. Warming only uses approved channels.</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] font-black text-cyan-400">{totalApproved}/{totalChannels} approved</span>
          {open ? <ChevronDown className="w-4 h-4 text-gray-600" /> : <ChevronRight className="w-4 h-4 text-gray-600" />}
        </div>
      </button>

      {open && data && (
        <div className="border-t border-gray-800 p-4">
          {/* segment selector */}
          <div className="flex items-center gap-1.5 mb-3 flex-wrap">
            {data.map(s => (
              <button key={s.segment} onClick={() => setSeg(s.segment)}
                className={`px-2.5 py-1 rounded-lg text-[10px] font-black transition-all ${
                  seg === s.segment ? "bg-cyan-500/15 border border-cyan-500/40 text-cyan-400" : "text-gray-500 hover:text-gray-300 border border-transparent"}`}>
                {s.segment.replace("_", " ")} <span className="opacity-60">{s.approved_count}/{s.total_channels}</span>
              </button>
            ))}
          </div>

          {current && (
            <div className="space-y-2">
              <div className="flex justify-end">
                <button onClick={() => approveAllForSegment(current.segment)}
                  className="text-[9px] font-black px-2.5 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/25">
                  Approve all 5 channels
                </button>
              </div>
              {current.channels.map(c => (
                <div key={c.channel} className={`rounded-xl border px-4 py-3 ${c.approved ? "border-emerald-500/25 bg-emerald-500/5" : "border-gray-800 bg-gray-950/50"}`}>
                  <div className="flex items-center gap-3">
                    <span className={c.approved ? "text-emerald-400" : "text-gray-500"}>{ICON[c.channel]}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-black text-gray-200">{c.label}</span>
                        {c.approved
                          ? <span className="text-[8px] font-black px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">APPROVED{c.default_approved ? " (default)" : ""}</span>
                          : <span className="text-[8px] font-black px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">NEEDS APPROVAL</span>}
                      </div>
                      <p className="text-[9px] text-gray-500 truncate mt-0.5 font-mono">{(c.custom_body || c.default_text).split("\n")[0]}</p>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button onClick={() => { setEditing({ segment: current.segment, channel: c.channel, label: c.label }); setEditText(c.custom_body || c.default_text); }}
                        className="p-1.5 rounded-lg bg-blue-500/15 border border-blue-500/40 text-blue-400 hover:bg-blue-500/25" title="Review / edit script"><Pencil className="w-3.5 h-3.5" /></button>
                      {c.approved
                        ? <button onClick={() => setApproval(current.segment, c.channel, false)} className="text-[9px] font-black px-2.5 py-1.5 rounded-lg border border-gray-700 text-gray-400 hover:text-gray-200">Pause</button>
                        : <button onClick={() => setApproval(current.segment, c.channel, true)} className="flex items-center gap-1 text-[9px] font-black px-2.5 py-1.5 rounded-lg bg-emerald-500/15 border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/25"><Check className="w-3 h-3" /> Approve</button>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* edit modal */}
      {editing && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={() => setEditing(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 w-full max-w-xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-black mb-3 flex items-center gap-2">{ICON[editing.channel]} {editing.label} script — {editing.segment.replace("_", " ")}</h3>
            <textarea value={editText} onChange={e => setEditText(e.target.value)} rows={10}
              className="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs font-sans leading-relaxed" />
            <div className="flex items-center justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)} className="px-4 py-2 text-xs font-bold text-gray-400 hover:text-gray-200">Cancel</button>
              <button onClick={() => { setApproval(editing.segment, editing.channel, true, editText); setEditing(null); }}
                className="px-4 py-2 text-xs font-black rounded-lg bg-emerald-500 hover:bg-emerald-400 text-gray-950">Save &amp; Approve</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
