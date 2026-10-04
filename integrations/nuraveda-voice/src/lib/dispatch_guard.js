/**
 * Dispatch guard for the Nuraveda voice sidecar scheduler.
 *
 * Four protections, all of which only ever REDUCE dialling:
 *   1. Volume caps   — at most SCHEDULER_MAX_PER_TICK dispatches per tick
 *                      (default 1) and at most SCHEDULER_MAX_INFLIGHT calls
 *                      outstanding at once (default 2).
 *   2. Pause flag    — a small JSON file (DISPATCH_PAUSE_FILE, default
 *                      <cwd>/data/dispatch_pause.json). While it says paused,
 *                      the scheduler dispatches nothing and /calls/dispatch
 *                      refuses new work with 503 (so the backend releases the
 *                      reservation instead of counting an attempt).
 *                      kind: 'credits' | 'wedge' | 'manual'. Only 'wedge' is
 *                      ever cleared automatically (after a healthy agent
 *                      restart); credits/manual need a person.
 *   3. Error classes — classifyDispatchError() separates provider credit
 *                      exhaustion (pause the whole queue, alert founder), never-
 *                      rang failures (agent never joined / trunk refused before
 *                      ringing: no retry, not counted, DISPATCH_FAILED) and
 *                      rang failures (busy / no answer: unchanged retry policy).
 *   4. Wedge monitor — tails the agent worker's PM2 log for "runner
 *                      initialization timed out" / "job executor is
 *                      unresponsive" and counts consecutive agent-join
 *                      failures; on a wedge it pauses dispatch and restarts the
 *                      agent worker ONLY when no call is active (rate limited),
 *                      then clears the wedge pause once the worker re-registers.
 *                      Sarvam/credit errors in the agent log pause the queue.
 *
 * Nothing in this module places, retries, or rings a call.
 */
import fs from 'node:fs';
import path from 'node:path';

const num = (v, d) => {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : d;
};

export function limits(env = process.env) {
  return {
    maxPerTick: Math.max(1, num(env.SCHEDULER_MAX_PER_TICK, 1)),
    maxInflight: Math.max(1, num(env.SCHEDULER_MAX_INFLIGHT, 2)),
    wedgeJoinFailures: Math.max(1, num(env.WEDGE_JOIN_FAILURES, 2)),
    wedgeUnresponsiveCount: Math.max(1, num(env.WEDGE_UNRESPONSIVE_COUNT, 2)),
    wedgeWindowMs: num(env.WEDGE_WINDOW_MS, 5 * 60_000),
    maxRestartsPerHour: num(env.WEDGE_MAX_RESTARTS_PER_HOUR, 3),
    recoveryGraceMs: num(env.WEDGE_RECOVERY_GRACE_MS, 90_000),
  };
}

// ── error classification ────────────────────────────────────────────────
const CREDIT_RE = /\b402\b|\b4030\b|insufficient[ _-]?credit|no credits|insufficient_quota|payment required|out of credit/i;
const AGENT_NOT_JOINED_RE = /did not join room/i;
// Trunk/carrier refused the INVITE before the callee's phone rang.
const PRE_RING_SIP = new Set(['400', '401', '403', '404', '407', '410', '484', '488',
  '500', '501', '502', '503', '504']);
const RANG_SIP = new Set(['486', '600', '603', '480', '408', '487']);

export function providerOf(message) {
  const m = String(message || '').toLowerCase();
  if (m.includes('sarvam')) return 'sarvam';
  if (m.includes('sip') || m.includes('insufficient credits')) return 'plivo';
  return 'unknown';
}

/**
 * @returns {{kind: 'credits'|'never_rang'|'rang'|'unknown', reason: string, sip: number|null}}
 */
export function classifyDispatchError(message) {
  const m = String(message || '');
  const sip = (m.match(/\b([4-6]\d\d)\b/) || [])[1] || null;
  if (CREDIT_RE.test(m)) return { kind: 'credits', reason: 'CREDITS_EXHAUSTED', sip: sip ? Number(sip) : null };
  if (AGENT_NOT_JOINED_RE.test(m)) return { kind: 'never_rang', reason: 'AGENT_NOT_JOINED', sip: null };
  if (/sip call failed/i.test(m) && sip && PRE_RING_SIP.has(sip)) {
    return { kind: 'never_rang', reason: 'PRE_RING_FAILURE', sip: Number(sip) };
  }
  if (/sip request timed out/i.test(m)) return { kind: 'never_rang', reason: 'PRE_RING_FAILURE', sip: null };
  if (sip && RANG_SIP.has(sip)) return { kind: 'rang', reason: sip === '486' || sip === '600' || sip === '603' ? 'BUSY' : 'NO_ANSWER', sip: Number(sip) };
  return { kind: 'unknown', reason: 'DISPATCH_ERROR', sip: sip ? Number(sip) : null };
}

// ── pause flag ──────────────────────────────────────────────────────────
export function pauseFile(env = process.env) {
  return env.DISPATCH_PAUSE_FILE || path.join(process.cwd(), 'data', 'dispatch_pause.json');
}

export function getPause(file = pauseFile()) {
  try {
    const p = JSON.parse(fs.readFileSync(file, 'utf8'));
    return p && p.paused ? p : null;
  } catch {
    return null;
  }
}

export function setPause(kind, reason, { file = pauseFile(), now = new Date() } = {}) {
  const cur = getPause(file);
  // Never downgrade a credit/manual pause to a wedge pause (a wedge pause is
  // auto-cleared; the stronger one must survive that).
  if (cur && cur.kind !== 'wedge' && kind === 'wedge') return cur;
  const next = { paused: true, kind, reason: String(reason || '').slice(0, 500),
    since: cur && cur.kind === kind ? cur.since : now.toISOString(), updatedAt: now.toISOString() };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(next, null, 1));
  if (!cur || cur.kind !== kind) console.warn(`[dispatch-guard] DISPATCH PAUSED kind=${kind}: ${next.reason}`);
  return next;
}

/** Clear the pause only if it is of `kind` (wedge auto-recovery). */
export function clearPause(kind, { file = pauseFile() } = {}) {
  const cur = getPause(file);
  if (!cur || cur.kind !== kind) return false;
  fs.unlinkSync(file);
  console.log(`[dispatch-guard] dispatch pause (${kind}) cleared`);
  return true;
}

// ── in-flight ──────────────────────────────────────────────────────────
export async function inflightCount(prisma, { stuckAfterMs, now = Date.now() }) {
  return prisma.scheduledCall.count({
    where: { status: 'dispatching', outcome: null, lastAttemptAt: { gte: new Date(now - stuckAfterMs) } },
  });
}

/** How many rows this tick may claim. */
export function dispatchBudget({ inflight, maxPerTick, maxInflight }) {
  return Math.max(0, Math.min(maxPerTick, maxInflight - inflight));
}

// ── founder alert ───────────────────────────────────────────────────────
export async function alertFounder(kind, detail, key, { env = process.env, fetchImpl = globalThis.fetch } = {}) {
  const base = (env.PURITY_API_BASE || 'http://127.0.0.1:8003').replace(/\/$/, '');
  const secret = env.PURITY_API_ADMIN_SECRET || '';
  try {
    const res = await fetchImpl(`${base}/api/v1/founder/system-alert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(secret ? { 'X-Api-Admin-Secret': secret } : {}) },
      body: JSON.stringify({ kind, detail: String(detail || '').slice(0, 3500), key: key || kind }),
    });
    const out = await res.json().catch(() => ({}));
    console.warn(`[dispatch-guard] founder alert ${kind} -> HTTP ${res.status} sent=${out.sent} ${out.reason || ''}`);
    return out;
  } catch (err) {
    console.error(`[dispatch-guard] founder alert ${kind} failed: ${err.message}`);
    return { sent: false, reason: err.message };
  }
}

// ── wedge monitor ───────────────────────────────────────────────────────
const WEDGE_HARD_RE = /runner initialization timed out/i;
const WEDGE_SOFT_RE = /job executor is unresponsive/i;
const REGISTERED_RE = /registered worker/i;
const AGENT_CREDIT_RE = /API error 402|402 Payment Required|No credits available|insufficient_quota|Insufficient Credits/i;

export class WedgeMonitor {
  constructor({ isIdle, restart, alert = alertFounder, env = process.env,
    file = pauseFile(env), clock = () => Date.now(), log = console } = {}) {
    this.isIdle = isIdle;
    this.restartFn = restart;
    this.alert = alert;
    this.env = env;
    this.file = file;
    this.clock = clock;
    this.log = log;
    this.lim = limits(env);
    this.joinFailures = 0;
    this.softHits = [];
    this.restarts = [];
    this.restartingSince = null;
    this.gaveUpAlerted = false;
    this.creditAlerted = false;
  }

  /** One line from the agent worker log. */
  onLogLine(line) {
    if (!line) return;
    if (AGENT_CREDIT_RE.test(line)) {
      this.creditPause(`agent provider out of credit: ${line.slice(0, 300)}`, providerOf(line));
      return;
    }
    if (REGISTERED_RE.test(line) && this.restartingSince) {
      this.recovered('agent worker re-registered');
      return;
    }
    if (WEDGE_HARD_RE.test(line)) return this.wedge('runner initialization timed out');
    if (WEDGE_SOFT_RE.test(line)) {
      const now = this.clock();
      this.softHits = this.softHits.filter((t) => now - t < this.lim.wedgeWindowMs);
      this.softHits.push(now);
      if (this.softHits.length >= this.lim.wedgeUnresponsiveCount) {
        this.wedge(`job executor is unresponsive x${this.softHits.length} in ${Math.round(this.lim.wedgeWindowMs / 1000)}s`);
      }
    }
  }

  noteJoinFailure(detail) {
    this.joinFailures += 1;
    if (this.joinFailures >= this.lim.wedgeJoinFailures) {
      this.wedge(`agent did not join ${this.joinFailures} consecutive calls (jobs not starting): ${String(detail || '').slice(0, 200)}`);
    }
  }

  noteJoinSuccess() {
    this.joinFailures = 0;
  }

  creditPause(reason, provider = 'unknown') {
    setPause('credits', reason, { file: this.file });
    if (!this.creditAlerted) {
      this.creditAlerted = true;
      this.alert('CREDITS_EXHAUSTED',
        `${reason}\n\nThe voice call queue is paused (no retries). Top up ${provider} credits, then resume dispatch manually.`,
        `credits:${provider}`);
    }
  }

  wedge(reason) {
    setPause('wedge', reason, { file: this.file });
    this.log.warn(`[dispatch-guard] WEDGE detected: ${reason}`);
    return this.tryRecover(reason);
  }

  async tryRecover(reason = 'wedge') {
    const now = this.clock();
    if (this.restartingSince) return 'restarting';
    this.restarts = this.restarts.filter((t) => now - t < 3600_000);
    if (this.restarts.length >= this.lim.maxRestartsPerHour) {
      if (!this.gaveUpAlerted) {
        this.gaveUpAlerted = true;
        this.alert('VOICE_AGENT_WEDGED',
          `Voice agent wedged again (${reason}) after ${this.restarts.length} restarts in the last hour. Dispatch stays paused until you resume it.`,
          'agent_wedged');
      }
      return 'gave_up';
    }
    let idle = false;
    try { idle = await this.isIdle(); } catch (err) { this.log.warn(`[dispatch-guard] idle check failed: ${err.message}`); }
    if (!idle) {
      this.log.warn('[dispatch-guard] wedge: a call is active — not restarting the agent yet (dispatch paused)');
      return 'busy';
    }
    this.restarts.push(now);
    this.restartingSince = now;
    this.joinFailures = 0;
    this.softHits = [];
    this.log.warn(`[dispatch-guard] restarting voice agent worker (no call active; restart ${this.restarts.length}/${this.lim.maxRestartsPerHour} this hour)`);
    try {
      await this.restartFn();
    } catch (err) {
      this.log.error(`[dispatch-guard] agent restart failed: ${err.message}`);
      this.restartingSince = null;
      return 'restart_failed';
    }
    return 'restarted';
  }

  recovered(why) {
    this.restartingSince = null;
    this.gaveUpAlerted = false;
    clearPause('wedge', { file: this.file });
    this.log.log(`[dispatch-guard] wedge recovered (${why})`);
  }

  /** Called every scheduler tick. */
  async tick() {
    const p = getPause(this.file);
    if (this.restartingSince && this.clock() - this.restartingSince >= this.lim.recoveryGraceMs) {
      this.recovered(`grace ${Math.round(this.lim.recoveryGraceMs / 1000)}s elapsed after restart`);
    } else if (p && p.kind === 'wedge' && !this.restartingSince) {
      await this.tryRecover(p.reason);
    }
  }
}

/** Poll-tail a log file (from its current end) and feed new lines to onLine. */
export function tailFile(file, onLine, { intervalMs = 5000 } = {}) {
  let pos = 0;
  try { pos = fs.statSync(file).size; } catch { pos = 0; }
  let buf = '';
  const h = setInterval(() => {
    let size;
    try { size = fs.statSync(file).size; } catch { return; }
    if (size < pos) pos = 0; // rotated / truncated
    if (size === pos) return;
    const len = Math.min(size - pos, 4 * 1024 * 1024);
    const fd = fs.openSync(file, 'r');
    try {
      const b = Buffer.alloc(len);
      fs.readSync(fd, b, 0, len, pos);
      pos += len;
      buf += b.toString('utf8');
    } finally { fs.closeSync(fd); }
    const lines = buf.split(/\r?\n/);
    buf = lines.pop() || '';
    for (const l of lines) {
      try { onLine(l); } catch (err) { console.error('[dispatch-guard] log line handler:', err.message); }
    }
  }, intervalMs);
  h.unref?.();
  return () => clearInterval(h);
}
