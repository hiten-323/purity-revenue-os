// Profile hooks for the Nuraveda voice sidecar (ai-voice-agent/src/server.js).
//
// server.js imports profiles/<id>/index.js and calls onNoAnswer({prisma, row,
// reason}) when the scheduler gives up on a ScheduledCall (no answer, busy,
// SIP/dispatch error, stuck-dispatch sweep) after CALL_MAX_ATTEMPTS. Before
// this file existed the purity-coffee-b2b profile had no index.js, so every
// such call was logged as "no onNoAnswer hook" and Python never learned the
// call had ended -- the lead stayed call_status=CALLING forever.
//
// This hook only REPORTS a termination to Purity's backend
// (POST /api/v1/founder/ai-call-status). It never dials, retries, or sends.

const PURITY_API_BASE = (process.env.PURITY_API_BASE || 'http://127.0.0.1:8003').replace(/\/$/, '');
const PURITY_API_ADMIN_SECRET = process.env.PURITY_API_ADMIN_SECRET || '';

// Scheduler reason text -> backend termination reason (call_intelligence
// taxonomy.ENGINE_TERMINATIONS). Order matters: most specific first.
export function classifyReason(reason, disposition) {
  const r = String(reason || '').toLowerCase();
  const d = String(disposition || '').toLowerCase();
  const sip = (r.match(/\b([4-6]\d\d)\b/) || [])[1];
  if (r.includes('stuck-dispatch')) return { reason: 'STUCK_DISPATCH', sip_status_code: null };
  if (d === 'busy' || /\bbusy\b/.test(r) || sip === '486' || sip === '600') {
    return { reason: 'BUSY', sip_status_code: sip ? Number(sip) : null };
  }
  if (d === 'no_answer' || /no.?answer|not answered|ring.?timeout|temporarily unavailable/.test(r)
      || ['408', '480', '487'].includes(sip)) {
    return { reason: 'NO_ANSWER', sip_status_code: sip ? Number(sip) : null };
  }
  if (d === 'voicemail') return { reason: 'VOICEMAIL', sip_status_code: null };
  if (d === 'timeout' || /timed? ?out/.test(r)) return { reason: 'TIMEOUT', sip_status_code: sip ? Number(sip) : null };
  if (sip) return { reason: 'SIP_ERROR', sip_status_code: Number(sip) };
  return { reason: 'DISPATCH_ERROR', sip_status_code: null };
}

function payloadOf(row) {
  const p = row && row.payload;
  if (!p) return {};
  if (typeof p === 'string') {
    try { return JSON.parse(p); } catch { return {}; }
  }
  return p;
}

export async function onNoAnswer({ prisma, row, reason }) {
  const payload = payloadOf(row);
  const leadId = Number(payload.lead_id || 0);
  if (!leadId) {
    console.log(`[purity-coffee-b2b] final-fail ${row && row.orderName}: no lead_id in payload, nothing to report`);
    return;
  }
  let attempt = null;
  try {
    attempt = await prisma.callAttempt.findFirst({
      where: { shop: row.shop, orderId: row.orderId },
      orderBy: { startedAt: 'desc' },
    });
  } catch (err) {
    console.warn('[purity-coffee-b2b] could not read CallAttempt:', err.message);
  }
  const cls = classifyReason(reason || row.lastError, attempt && attempt.disposition);
  const body = {
    lead_id: leadId,
    reason: cls.reason,
    sip_status_code: cls.sip_status_code,
    call_ref: payload.call_ref || '',
    room_name: (attempt && attempt.roomName) || row.roomName || '',
    sip_call_id: (attempt && attempt.sipCallId) || row.sipCallId || '',
    provider_call_id: row.id || '',
    started_at: attempt && attempt.startedAt ? new Date(attempt.startedAt).toISOString() : '',
    ended_at: new Date().toISOString(),
    attempts: row.attempts ?? null,
    detail: String(reason || row.lastError || '').slice(0, 400),
  };
  try {
    const res = await fetch(`${PURITY_API_BASE}/api/v1/founder/ai-call-status`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(PURITY_API_ADMIN_SECRET ? { 'X-Api-Admin-Secret': PURITY_API_ADMIN_SECRET } : {}),
      },
      body: JSON.stringify(body),
    });
    const out = await res.json().catch(() => ({}));
    console.log(`[purity-coffee-b2b] final-fail lead=${leadId} reason=${cls.reason} -> HTTP ${res.status} call_status=${out.call_status || '?'}`);
  } catch (err) {
    // The backend reconciler will still close this lead after
    // CALL_RECONCILER_STALE_MINUTES; nothing is retried from here.
    console.warn('[purity-coffee-b2b] ai-call-status report failed:', err.message);
  }
}
