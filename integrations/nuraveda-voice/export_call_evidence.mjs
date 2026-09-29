// READ-ONLY export of sidecar call evidence for the stuck-call backfill.
//
// Run from the ai-voice-agent directory (so @prisma/client resolves there):
//   node /path/to/export_call_evidence.mjs > sidecar_calls.json
// then:
//   python backend/scripts/backfill_stuck_calls.py --sidecar-export sidecar_calls.json
//
// Only findMany/count are used. Nothing is written, dialled or sent.
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();
const PROFILE = process.env.EXPORT_PROFILE || 'purity-coffee-b2b';
const SINCE = new Date(process.env.EXPORT_SINCE || Date.now() - 30 * 86400e3);

function leadIdOf(payload) {
  try {
    const p = typeof payload === 'string' ? JSON.parse(payload) : (payload || {});
    const n = Number(p.lead_id || 0);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch { return null; }
}

try {
  const calls = await prisma.scheduledCall.findMany({
    where: { profile: PROFILE, createdAt: { gte: SINCE } },
    orderBy: { updatedAt: 'asc' },
  });
  const out = [];
  for (const c of calls) {
    const leadId = leadIdOf(c.payload);
    if (!leadId) continue;
    const attempts = await prisma.callAttempt.findMany({
      where: { shop: c.shop, orderId: c.orderId },
      orderBy: { startedAt: 'asc' },
    });
    const last = attempts[attempts.length - 1] || null;
    const userTurns = await prisma.callTurn.count({
      where: { shop: c.shop, orderId: c.orderId, role: 'user' },
    });
    const sip = String(c.lastError || '').match(/\b([4-6]\d\d)\b/);
    out.push({
      lead_id: leadId,
      scheduled_call_id: c.id,
      status: c.status,
      disposition: (last && last.disposition) || c.outcome || null,
      outcome: c.outcome,
      attempts: c.attempts,
      user_turns: userTurns,
      sip_status: sip ? Number(sip[1]) : null,
      last_error: c.lastError ? String(c.lastError).slice(0, 200) : null,
      room_name: (last && last.roomName) || c.roomName,
      started_at: last && last.startedAt,
      ended_at: last && last.endedAt,
    });
  }
  process.stdout.write(JSON.stringify(out, null, 1));
} finally {
  await prisma.$disconnect();
}
