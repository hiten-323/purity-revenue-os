/**
 * DB-backed scheduled-call dispatcher.
 *
 * Runs in-process inside the Express server. Every SCHEDULER_TICK_MS it:
 *   1. Finds queued rows whose scheduledAt <= now(), atomically claims them
 *      by updating status='dispatching', and calls LiveKit to originate the
 *      outbound SIP call.
 *   2. Sweeps rows that have been 'dispatching' for > STUCK_AFTER_MS without
 *      terminal outcome — treats them as no-answer, applies retry backoff or
 *      final-fail tagging.
 *
 * Retry policy (configurable via env):
 *   attempts=0 → initial dispatch
 *   no-answer / dispatch-error → attempts=1, requeued at +RETRY_BACKOFF_1_MS
 *   no-answer / dispatch-error → attempts=2, requeued at +RETRY_BACKOFF_2_MS
 *   attempts >= MAX_ATTEMPTS   → status=failed, outcome=no_answer,
 *                                tag cod-no-answer written to Shopify
 *
 * Retry scheduledAt is always passed through DND adjustment so retries that
 * would fall in the 21:00–09:00 IST window roll to the next morning.
 *
 * Terminal outcomes are set by the LiveKit tool webhooks in server.js when
 * Priya calls confirm_order / cancel_order / request_human_agent /
 * request_callback — those handlers call markScheduledCallOutcome().
 */

import { triggerLivekitCall } from '../trigger-livekit-call.js';
import { getShopBranding } from './shops.js';
import { adjustForDnd } from './dnd.js';
import {
  limits as guardLimits, classifyDispatchError, getPause, setPause, inflightCount,
  dispatchBudget, alertFounder, providerOf, WedgeMonitor, tailFile,
} from './dispatch_guard.js';
import { execFile } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';

// ── Tunables ──────────────────────────────────────────────────────────
const TICK_MS            = Number(process.env.SCHEDULER_TICK_MS        ?? 30_000);
// Purity guard (2026-10-03): default 1 dispatch per tick (was 10) and at most
// SCHEDULER_MAX_INFLIGHT (default 2) calls outstanding. See dispatch_guard.js.
const GUARD              = guardLimits();
const MAX_PER_TICK       = GUARD.maxPerTick;
const MAX_INFLIGHT       = GUARD.maxInflight;
const STUCK_AFTER_MS     = Number(process.env.SCHEDULER_STUCK_AFTER_MS ?? 5 * 60_000);
const MAX_ATTEMPTS       = Number(process.env.CALL_MAX_ATTEMPTS        ?? 3);
const RETRY_BACKOFF_1_MS = Number(process.env.CALL_RETRY_1_MS          ?? 30 * 60_000);  // 30min
const RETRY_BACKOFF_2_MS = Number(process.env.CALL_RETRY_2_MS          ?? 2 * 60 * 60_000); // 2h

/**
 * DISPATCH_MODE controls whether the scheduler actually places phone calls.
 *   live    — full production. triggerLivekitCall fires, customer receives
 *             a real PSTN call.
 *   dry_run — beta-test mode. Scheduler picks up due rows, logs the full
 *             payload, marks them done(dry_run), but DOES NOT call the
 *             customer. Lets you exercise the entire HMAC/allowlist/phone/
 *             DND/idempotency pipeline against real Shopify orders without
 *             ringing anyone's phone.
 *
 * Default: live (so a missing env doesn't surprise-disable production).
 * For first-store beta testing, set DISPATCH_MODE=dry_run in .env.
 */
export const DISPATCH_MODE = (process.env.DISPATCH_MODE || 'live').toLowerCase();
const VALID_MODES = ['live', 'dry_run'];
if (!VALID_MODES.includes(DISPATCH_MODE)) {
  throw new Error(`Invalid DISPATCH_MODE: "${DISPATCH_MODE}". Must be one of: ${VALID_MODES.join(', ')}`);
}

/**
 * Start the scheduler. Returns a stop() fn. Only one scheduler loop should
 * run per process — server.js calls startScheduler() once in app init.
 */
let wedgeMonitor = null;
export function getWedgeMonitor() { return wedgeMonitor; }

function restartAgentWorker() {
  const name = process.env.AGENT_PM2_NAME || 'nuraveda-voice-agent';
  return new Promise((resolve, reject) => {
    execFile(process.platform === 'win32' ? 'pm2.cmd' : 'pm2', ['restart', name],
      { shell: process.platform === 'win32', timeout: 120_000, windowsHide: true },
      (err, stdout, stderr) => {
        if (err) return reject(new Error(`${err.message} ${String(stderr || '').slice(0, 300)}`));
        console.warn(`[dispatch-guard] pm2 restart ${name} ok`);
        resolve(stdout);
      });
  });
}

export function startScheduler(prisma, { onFinalFail, restartAgent = restartAgentWorker, isAgentIdle } = {}) {
  let stopped = false;

  // No call is active when nothing is mid-dispatch here and no ScheduledCall
  // is still inside its call window.
  const idle = isAgentIdle || (async () => inflightNow === 0
    && (await inflightCount(prisma, { stuckAfterMs: STUCK_AFTER_MS })) === 0);
  wedgeMonitor = new WedgeMonitor({ isIdle: idle, restart: restartAgent });
  const agentLog = process.env.AGENT_LOG_PATH
    || path.join(os.homedir(), '.pm2', 'logs', 'nuraveda-voice-agent-out.log');
  const stopTail = process.env.WEDGE_LOG_TAIL === '0' ? () => {} : tailFile(agentLog, (l) => wedgeMonitor.onLogLine(l));

  async function tick() {
    if (stopped) return;
    try {
      await wedgeMonitor.tick();
      await Promise.all([
        dispatchDue(prisma, onFinalFail),
        sweepStuck(prisma, onFinalFail),
      ]);
    } catch (err) {
      console.error('[scheduler] tick error:', err);
    }
  }

  // Kick off one tick immediately, then interval.
  tick();
  const handle = setInterval(tick, TICK_MS);

  const modeLabel = DISPATCH_MODE === 'dry_run' ? '🔒 DRY-RUN (no real calls)' : '🟢 LIVE (real calls)';
  console.log(`[scheduler] started — mode=${modeLabel} tick=${TICK_MS}ms, max-per-tick=${MAX_PER_TICK}, max-inflight=${MAX_INFLIGHT}, stuck-after=${STUCK_AFTER_MS}ms, max-attempts=${MAX_ATTEMPTS}`);
  const p0 = getPause();
  if (p0) console.warn(`[scheduler] dispatch is PAUSED (kind=${p0.kind} since ${p0.since}): ${p0.reason}`);

  return function stop() {
    stopped = true;
    clearInterval(handle);
    stopTail();
  };
}

let inflightNow = 0;
let pauseLogged = null;

export async function dispatchDue(prisma, onFinalFail) {
  const now = new Date();
  const pause = getPause();
  if (pause) {
    if (pauseLogged !== pause.updatedAt) {
      console.warn(`[scheduler] dispatch paused (kind=${pause.kind}) — not dispatching: ${pause.reason}`);
      pauseLogged = pause.updatedAt;
    }
    return;
  }
  pauseLogged = null;
  const inflight = await inflightCount(prisma, { stuckAfterMs: STUCK_AFTER_MS, now: now.getTime() });
  const take = dispatchBudget({ inflight: Math.max(inflight, inflightNow), maxPerTick: MAX_PER_TICK, maxInflight: MAX_INFLIGHT });
  if (take <= 0) return;
  const due = await prisma.scheduledCall.findMany({
    where: { status: 'queued', scheduledAt: { lte: now } },
    orderBy: { scheduledAt: 'asc' },
    take,
  });

  if (!due.length) return;

  for (const row of due) {
    // Claim atomically — if another tick or process raced us, bail out
    const claimed = await prisma.scheduledCall.updateMany({
      where: { id: row.id, status: 'queued' },
      data:  { status: 'dispatching', lastAttemptAt: now },
    });
    if (claimed.count === 0) continue;

    inflightNow += 1;
    dispatchOne(prisma, row, onFinalFail)
      .catch(err => console.error('[scheduler] dispatchOne threw unhandled:', err))
      .finally(() => { inflightNow = Math.max(0, inflightNow - 1); });
  }
}

async function dispatchOne(prisma, row, onFinalFail) {
  const payload = row.payload || {};

  // ── DRY-RUN GATE ──────────────────────────────────────────────────
  // Beta-test mode: log everything, persist the dry-run, do not call
  // LiveKit. Customer never hears their phone ring.
  if (DISPATCH_MODE === 'dry_run') {
    console.log(`[scheduler] DRY-RUN dispatch ${row.orderName} (${row.shop}) phone=${row.phone} lang=${row.lang} payload=${JSON.stringify(payload)}`);
    await prisma.callAttempt.create({
      data: {
        shop: row.shop, orderId: row.orderId, orderName: row.orderName,
        phone: row.phone,
        disposition: 'dry_run',
        notes: 'DISPATCH_MODE=dry_run — no real call placed',
        endedAt: new Date(),
      },
    });
    await prisma.scheduledCall.update({
      where: { id: row.id },
      data:  { status: 'done', outcome: 'dry_run', attempts: { increment: 1 } },
    });
    console.log(`[scheduler] DRY-RUN done ${row.orderName} — flip DISPATCH_MODE=live to enable real calls`);
    return;
  }

  // ── LIVE PATH ─────────────────────────────────────────────────────
  // Split into two phases (issue #10):
  //   1. Place the outbound call. Failures here are "real" dispatch failures
  //      — requeue with backoff.
  //   2. Persist the attempt + update the scheduled row. Failures here happen
  //      AFTER the customer's phone may already be ringing. We MUST NOT
  //      auto-retry in that case, because we would place a duplicate call.
  let placement;
  try {
    // Per-shop branding only matters for COD-confirm today (multi-tenant
    // store-name override). Other profiles get an empty branding bag and
    // fall back to STORE_NAME / STORE_CATEGORY env.
    const branding = row.shop ? getShopBranding(row.shop) : {};

    placement = await triggerLivekitCall({
      phone:    row.phone,
      lang:     row.lang,
      profile:  row.profile || 'ai-voice-agent',
      // Pass the profile's payload verbatim. Each key becomes one
      // participant attribute the profile's renderContext reads. No
      // more COD-specific camelCase translation.
      payload:  payload,
      identity: {
        shop:       row.shop,
        entityRef:  row.orderId,
        entityName: row.orderName,
      },
      branding: { name: branding.name, category: branding.category },
    });
  } catch (err) {
    const msg = err?.message || String(err);
    console.error(`[scheduler] dispatch failed ${row.orderName}:`, msg);
    const cls = classifyDispatchError(msg);
    if (cls.reason === 'AGENT_NOT_JOINED') wedgeMonitor?.noteJoinFailure(msg);
    else wedgeMonitor?.noteJoinSuccess();
    if (cls.kind === 'credits') {
      await handleCreditsExhausted(prisma, row, msg, onFinalFail);
      return;
    }
    if (cls.kind === 'never_rang') {
      await handleNeverRang(prisma, row, cls.reason, msg, onFinalFail);
      return;
    }
    await handleFailure(prisma, row, msg, onFinalFail);
    return;
  }
  wedgeMonitor?.noteJoinSuccess();

  // ── From here, the call was placed. Any failure below is POST-PLACEMENT
  //    bookkeeping and must NOT auto-requeue. Log loudly for manual recovery
  //    and leave the row in 'dispatching' so the stuck-sweep handles it only
  //    after STUCK_AFTER_MS with no terminal outcome — by which time the
  //    LiveKit tool webhooks will likely have landed an outcome anyway.
  const roomName = placement?.room_name || null;
  const sipCallId = placement?.sip?.sipCallId || placement?.sip?.sip_call_id || null;

  try {
    await prisma.callAttempt.create({
      data: {
        shop: row.shop, orderId: row.orderId, orderName: row.orderName,
        phone: row.phone, roomName, sipCallId,
      },
    });
    await prisma.scheduledCall.update({
      where: { id: row.id },
      data:  { roomName, sipCallId, attempts: { increment: 1 } },
    });
    console.log(`[scheduler] dispatched ${row.orderName} (${row.shop}) attempt=${row.attempts + 1} room=${roomName}`);
  } catch (err) {
    // Call is already live externally. Do NOT call handleFailure(), do NOT
    // requeue. Dump everything needed for manual reconciliation and move on.
    console.error(
      `[scheduler] POST-DISPATCH BOOKKEEPING FAILED for ${row.orderName} (${row.shop})` +
      ` — call was already placed (room=${roomName} sipCallId=${sipCallId}).` +
      ` NOT auto-retrying to avoid a duplicate call. Manual review needed.` +
      ` scheduledCall.id=${row.id} error=${err?.message || err}`
    );
  }
}

/**
 * Provider credit exhausted (Plivo 402/4030, Sarvam 402): the phone never
 * rang. Pause the WHOLE queue (no per-call retries), do not count the attempt,
 * tell the backend (attempt released, DISPATCH_FAILED) and alert the founder.
 */
export async function handleCreditsExhausted(prisma, row, reason, onFinalFail) {
  const provider = providerOf(reason);
  setPause('credits', `${provider}: ${reason}`);
  await closeOpenAttempt(prisma,
    { shop: row.shop, orderId: row.orderId, roomName: row.roomName, sipCallId: row.sipCallId },
    'credits_exhausted', reason);
  await prisma.scheduledCall.update({
    where: { id: row.id },
    data: { status: 'failed', outcome: 'credits_exhausted', lastError: reason },
  });
  console.warn(`[scheduler] CREDITS EXHAUSTED ${row.orderName}: ${reason} — queue paused, no retry, attempt not counted`);
  if (wedgeMonitor) wedgeMonitor.creditPause(`${provider}: ${reason}`, provider);
  else await alertFounder('CREDITS_EXHAUSTED', `${provider}: ${reason}\n\nVoice call queue paused.`, `credits:${provider}`);
  if (typeof onFinalFail === 'function') {
    try { await onFinalFail(row, `CREDITS_EXHAUSTED: ${reason}`); } catch (err) { console.error('[scheduler] onFinalFail threw:', err); }
  }
}

/**
 * The business's phone never rang (agent never joined the room, or the trunk
 * refused before ringing). Not an attempt: no sidecar retry, attempts not
 * incremented, labelled dispatch_failed (never no_answer). The backend
 * releases the lead's attempt and owns any later retry.
 */
export async function handleNeverRang(prisma, row, termination, reason, onFinalFail) {
  await closeOpenAttempt(prisma,
    { shop: row.shop, orderId: row.orderId, roomName: row.roomName, sipCallId: row.sipCallId },
    'dispatch_failed', reason);
  await prisma.scheduledCall.update({
    where: { id: row.id },
    data: { status: 'failed', outcome: 'dispatch_failed', lastError: reason },
  });
  console.log(`[scheduler] DISPATCH_FAILED (${termination}) ${row.orderName}: ${reason} — never rang, not retried, attempt not counted`);
  if (typeof onFinalFail === 'function') {
    try { await onFinalFail(row, `${termination}: ${reason}`); } catch (err) { console.error('[scheduler] onFinalFail threw:', err); }
  }
}

async function sweepStuck(prisma, onFinalFail) {
  const threshold = new Date(Date.now() - STUCK_AFTER_MS);
  const stuck = await prisma.scheduledCall.findMany({
    where: { status: 'dispatching', lastAttemptAt: { lt: threshold }, outcome: null },
    take: MAX_PER_TICK,
  });

  for (const row of stuck) {
    console.warn(`[scheduler] stuck-dispatch ${row.orderName} (${row.shop}) — treating as no-answer`);
    await handleFailure(prisma, row, 'no-answer (stuck-dispatch sweep)', onFinalFail);
  }
}

/**
 * Close the latest open CallAttempt for this scheduled call (if any).
 * Idempotent — if no open row exists (e.g. dispatch failed before the
 * attempt was created, or it was already closed), this is a no-op.
 *
 * Match priority: roomName → sipCallId → (shop, orderId) latest open. The
 * fallback by (shop, orderId) is necessary for cases where dispatchOne
 * threw before persisting roomName/sipCallId on the scheduledCall row.
 *
 * Issue #13: handleFailure() previously left CallAttempt rows open with
 * disposition=null, endedAt=null whenever the scheduler resolved a call
 * via no-answer / stuck-dispatch / final-fail. Tool-driven outcomes
 * (confirm/cancel/etc.) closed the attempt; failures didn't. Caused
 * orphaned half-rows that broke turnCount, audio joins, and dashboards.
 */
async function closeOpenAttempt(prisma, { shop, orderId, roomName, sipCallId }, disposition, notes) {
  if (!shop || !orderId) return null;
  let where;
  if (roomName) {
    where = { shop, orderId: String(orderId), roomName, endedAt: null };
  } else if (sipCallId) {
    where = { shop, orderId: String(orderId), sipCallId, endedAt: null };
  } else {
    where = { shop, orderId: String(orderId), endedAt: null };
  }
  const latest = await prisma.callAttempt.findFirst({
    where, orderBy: { startedAt: 'desc' },
  });
  if (!latest) return null;
  await prisma.callAttempt.update({
    where: { id: latest.id },
    data:  { endedAt: new Date(), disposition, notes },
  });
  return latest;
}

/**
 * Shared failure-handling: either requeue with backoff or finalize as failed.
 * attemptsAlreadyIncremented: dispatchOne increments on success-path.
 * For failures we decide based on row.attempts as it currently stands.
 *
 * Always closes the open CallAttempt — for retries, the previous attempt
 * is closed before the next dispatch creates a fresh one (one CallAttempt
 * per dispatch is the invariant). For final-fail, the attempt closes with
 * disposition='no_answer'. For pre-call dispatch errors the attempt may
 * not exist yet; closeOpenAttempt is a no-op in that case.
 */
async function handleFailure(prisma, row, reason, onFinalFail) {
  const nextAttempts = (row.attempts || 0) + 1;

  // Disposition reflects the failure mode. 'no_answer' for stuck-sweep
  // (call placed, customer never picked up); 'dispatch_error' for
  // pre-call failures (triggerLivekitCall threw — no SIP placed).
  const cls = classifyDispatchError(reason);
  const disposition = reason && reason.includes('stuck-dispatch') ? 'no_answer'
    : (cls.reason === 'BUSY' ? 'busy' : (cls.reason === 'NO_ANSWER' ? 'no_answer' : 'dispatch_error'));
  await closeOpenAttempt(
    prisma,
    { shop: row.shop, orderId: row.orderId, roomName: row.roomName, sipCallId: row.sipCallId },
    disposition,
    reason,
  );

  if (nextAttempts >= MAX_ATTEMPTS) {
    await prisma.scheduledCall.update({
      where: { id: row.id },
      data: {
        status:    'failed',
        // Label what actually happened last (was always 'no_answer').
        outcome:   disposition,
        attempts:  nextAttempts,
        lastError: reason,
      },
    });
    console.log(`[scheduler] FINAL FAIL ${row.orderName} after ${nextAttempts} attempts: ${reason}`);
    if (typeof onFinalFail === 'function') {
      try {
        await onFinalFail(row, reason);
      } catch (err) {
        console.error('[scheduler] onFinalFail threw:', err);
      }
    }
    return;
  }

  const backoffMs = nextAttempts === 1 ? RETRY_BACKOFF_1_MS : RETRY_BACKOFF_2_MS;
  const nextAt = adjustForDnd(new Date(Date.now() + backoffMs));

  await prisma.scheduledCall.update({
    where: { id: row.id },
    data: {
      status:        'queued',
      scheduledAt:   nextAt,
      attempts:      nextAttempts,
      lastError:     reason,
    },
  });

  console.log(`[scheduler] retry-queued ${row.orderName} attempt ${nextAttempts}/${MAX_ATTEMPTS} at ${nextAt.toISOString()}`);
}

/**
 * Called by LiveKit tool webhooks (confirm/cancel/agent/callback) to mark a
 * scheduled call as terminally resolved. Safe to call multiple times — first
 * outcome wins.
 */
export async function markScheduledCallOutcome(prisma, { shop, orderId, outcome, notes }) {
  if (!shop || !orderId || !outcome) return null;
  const row = await prisma.scheduledCall.findUnique({ where: { shop_orderId: { shop, orderId: String(orderId) } } });
  if (!row) return null;

  // Issue #11: atomic conditional update. The previous read-then-write could
  // race — two tool callbacks both observing outcome:null would both write,
  // letting the later call overwrite the first terminal outcome. updateMany
  // with outcome:null in the filter is a single SQL UPDATE ... WHERE outcome
  // IS NULL, so only the first write commits. The count tells us whether
  // WE were that first write.
  const claim = await prisma.scheduledCall.updateMany({
    where: { id: row.id, outcome: null },
    data: {
      status:    'done',
      outcome,
      lastError: null,
    },
  });

  if (claim.count === 0) {
    // Someone else got here first. Re-fetch for the current terminal state
    // and return it as a no-op — do NOT close an attempt either.
    const existing = await prisma.scheduledCall.findUnique({ where: { id: row.id } });
    console.log(`[scheduler] outcome=${outcome} ignored for ${shop}/${orderId} — already terminal (${existing?.outcome})`);
    return existing;
  }

  // We won the race — close the latest attempt record with OUR outcome.
  const latestAttempt = await prisma.callAttempt.findFirst({
    where: { shop, orderId: String(orderId), endedAt: null },
    orderBy: { startedAt: 'desc' },
  });
  if (latestAttempt) {
    await prisma.callAttempt.update({
      where: { id: latestAttempt.id },
      data: { endedAt: new Date(), disposition: outcome, notes },
    });
  }

  console.log(`[scheduler] outcome=${outcome} recorded for ${shop}/${orderId}`);
  return prisma.scheduledCall.findUnique({ where: { id: row.id } });
}
