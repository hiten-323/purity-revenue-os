import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dg-'));
const G = await import('../src/lib/dispatch_guard.js');

const REAL = {
  notJoined: 'agent ai-voice-agent-priya did not join room purity-coffee-b2b-x-1 within 45000ms — refusing to ring customer into silence',
  plivo402: 'SIP call failed: 402 Insufficient Credits (permission_denied)',
  sip503: 'SIP call failed: 503 Service Unavailable (failed_precondition)',
  sip400: 'SIP call failed: 400 Bad Request (invalid_argument)',
  busy: 'SIP call failed: 486 User Busy (failed_precondition)',
  sipTimeout: 'twirp error unknown: sip request timed out',
  aborted: 'The operation was aborted due to timeout',
  sarvam: '{"level":50,"error":"Sarvam STT API error 402: {\\"error\\":{\\"message\\":\\"No credits available.\\",\\"code\\":\\"insufficient_quota_error\\"}}","msg":"provider recognize task failed"}',
};

test('classifies the real 2026-09/10 sidecar errors', () => {
  assert.deepEqual(G.classifyDispatchError(REAL.notJoined), { kind: 'never_rang', reason: 'AGENT_NOT_JOINED', sip: null });
  assert.equal(G.classifyDispatchError(REAL.plivo402).kind, 'credits');
  assert.equal(G.classifyDispatchError('Plivo error 4030 insufficient balance').kind, 'credits');
  assert.equal(G.classifyDispatchError(REAL.sip503).reason, 'PRE_RING_FAILURE');
  assert.equal(G.classifyDispatchError(REAL.sip400).reason, 'PRE_RING_FAILURE');
  assert.equal(G.classifyDispatchError(REAL.sipTimeout).reason, 'PRE_RING_FAILURE');
  assert.deepEqual(G.classifyDispatchError(REAL.busy), { kind: 'rang', reason: 'BUSY', sip: 486 });
  assert.equal(G.classifyDispatchError('SIP call failed: 480 Temporarily Unavailable').reason, 'NO_ANSWER');
  // ambiguous -> counted like before, never silently released
  assert.equal(G.classifyDispatchError(REAL.aborted).kind, 'unknown');
  assert.equal(G.providerOf(REAL.plivo402), 'plivo');
  assert.equal(G.providerOf(REAL.sarvam), 'sarvam');
});

test('limits default to 1 per tick and 2 in flight', () => {
  const l = G.limits({});
  assert.equal(l.maxPerTick, 1);
  assert.equal(l.maxInflight, 2);
  assert.equal(G.limits({ SCHEDULER_MAX_PER_TICK: '2' }).maxPerTick, 2);
  assert.equal(G.limits({ SCHEDULER_MAX_PER_TICK: 'junk' }).maxPerTick, 1);
  assert.equal(G.dispatchBudget({ inflight: 0, maxPerTick: 1, maxInflight: 2 }), 1);
  assert.equal(G.dispatchBudget({ inflight: 1, maxPerTick: 2, maxInflight: 2 }), 1);
  assert.equal(G.dispatchBudget({ inflight: 2, maxPerTick: 2, maxInflight: 2 }), 0);
  assert.equal(G.dispatchBudget({ inflight: 5, maxPerTick: 2, maxInflight: 2 }), 0);
});

test('pause file: credit pause survives a wedge pause and wedge auto-clear', () => {
  const file = path.join(tmp, 'p1.json');
  assert.equal(G.getPause(file), null);
  G.setPause('credits', 'plivo 402', { file });
  G.setPause('wedge', 'runner timed out', { file });
  assert.equal(G.getPause(file).kind, 'credits');
  assert.equal(G.clearPause('wedge', { file }), false);
  assert.equal(G.getPause(file).kind, 'credits');
  assert.equal(G.clearPause('credits', { file }), true);
  assert.equal(G.getPause(file), null);
  G.setPause('wedge', 'x', { file });
  G.setPause('credits', 'y', { file });
  assert.equal(G.getPause(file).kind, 'credits');
});

function monitor(opts = {}) {
  const file = path.join(tmp, `m-${Math.random()}.json`);
  const calls = { restart: 0, alerts: [] };
  let now = 1_000_000;
  const m = new G.WedgeMonitor({
    file,
    env: { WEDGE_MAX_RESTARTS_PER_HOUR: '2', WEDGE_RECOVERY_GRACE_MS: '60000', ...(opts.env || {}) },
    isIdle: async () => (opts.idle ?? true),
    restart: async () => { calls.restart += 1; },
    alert: async (kind, detail, key) => { calls.alerts.push({ kind, key }); return { sent: true }; },
    clock: () => now,
    log: { log() {}, warn() {}, error() {} },
  });
  return { m, file, calls, advance: (ms) => { now += ms; } };
}

test('wedge: runner init timeout pauses dispatch and restarts the idle agent, then recovers', async () => {
  const { m, file, calls } = monitor();
  await m.onLogLine('{"level":40,"err":{"message":"runner initialization timed out"}}');
  assert.equal(G.getPause(file).kind, 'wedge');
  assert.equal(calls.restart, 1);
  m.onLogLine('{"level":30,"msg":"registered worker","id":"AW_x"}');
  assert.equal(G.getPause(file), null);
});

test('wedge: never restarts while a call is active', async () => {
  const { m, file, calls } = monitor({ idle: false });
  await m.onLogLine('runner initialization timed out');
  assert.equal(G.getPause(file).kind, 'wedge');
  assert.equal(calls.restart, 0);
  assert.equal(await m.tryRecover(), 'busy');
  assert.equal(calls.restart, 0);
});

test('wedge: one unresponsive warning is noise, two in the window is a wedge', async () => {
  const { m, file, calls } = monitor();
  m.onLogLine('{"msg":"job executor is unresponsive"}');
  assert.equal(G.getPause(file), null);
  await m.onLogLine('{"msg":"job executor is unresponsive"}');
  assert.equal(G.getPause(file).kind, 'wedge');
  assert.equal(calls.restart, 1);
});

test('wedge: consecutive agent-join failures (jobs not starting) trigger it; a success resets', async () => {
  const { m, file } = monitor();
  m.noteJoinFailure('did not join');
  m.noteJoinSuccess();
  m.noteJoinFailure('did not join');
  assert.equal(G.getPause(file), null);
  await m.noteJoinFailure('did not join');
  assert.equal(G.getPause(file).kind, 'wedge');
});

test('wedge: restart budget exhausted -> stays paused and alerts the founder once', async () => {
  const { m, file, calls, advance } = monitor();
  for (let i = 0; i < 2; i++) {
    await m.wedge('runner initialization timed out');
    advance(61_000);
    await m.tick();               // grace elapsed -> recovered
    assert.equal(G.getPause(file), null);
  }
  assert.equal(await m.wedge('runner initialization timed out'), 'gave_up');
  await m.wedge('runner initialization timed out');
  assert.equal(calls.restart, 2);
  assert.equal(G.getPause(file).kind, 'wedge');
  assert.deepEqual(calls.alerts.map((a) => a.kind), ['VOICE_AGENT_WEDGED']);
});

test('agent log Sarvam 402 pauses the whole queue (credits) and alerts once', () => {
  const { m, file, calls } = monitor();
  m.onLogLine(REAL.sarvam);
  m.onLogLine(REAL.sarvam);
  const p = G.getPause(file);
  assert.equal(p.kind, 'credits');
  assert.deepEqual(calls.alerts, [{ kind: 'CREDITS_EXHAUSTED', key: 'credits:sarvam' }]);
  // a wedge cannot clear or downgrade it
  m.recovered('test');
  assert.equal(G.getPause(file).kind, 'credits');
});

test('ordinary slow-inference lines are ignored', () => {
  const { m, file } = monitor();
  m.onLogLine('{"level":40,"delay":2402,"msg":"inference is slower than realtime"}');
  m.onLogLine('{"level":40,"delay":402,"msg":"inference is slower than realtime"}');
  assert.equal(G.getPause(file), null);
});

test('alertFounder posts to the backend system-alert endpoint with the admin secret', async () => {
  const seen = [];
  const out = await G.alertFounder('CREDITS_EXHAUSTED', 'plivo 402', 'credits:plivo', {
    env: { PURITY_API_BASE: 'http://api.test/', PURITY_API_ADMIN_SECRET: 's3' },
    fetchImpl: async (url, init) => { seen.push({ url, init }); return { status: 200, json: async () => ({ sent: true }) }; },
  });
  assert.equal(out.sent, true);
  assert.equal(seen[0].url, 'http://api.test/api/v1/founder/system-alert');
  assert.equal(seen[0].init.headers['X-Api-Admin-Secret'], 's3');
  assert.equal(JSON.parse(seen[0].init.body).key, 'credits:plivo');
});

test('tailFile only reads lines appended after start', async () => {
  const f = path.join(tmp, 'agent.log');
  fs.writeFileSync(f, 'runner initialization timed out\n');
  const got = [];
  const stop = G.tailFile(f, (l) => got.push(l), { intervalMs: 20 });
  fs.appendFileSync(f, 'line a\nline b\n');
  await new Promise((r) => setTimeout(r, 120));
  stop();
  assert.deepEqual(got, ['line a', 'line b']);
});
