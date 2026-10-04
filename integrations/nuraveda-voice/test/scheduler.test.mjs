// Scheduler behaviour with the dispatch guard, against an in-memory fake
// Prisma and a stubbed triggerLivekitCall. Nothing dials.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sched-'));
fs.mkdirSync(path.join(tmp, 'src', 'lib'), { recursive: true });
for (const f of ['scheduler.js', 'dispatch_guard.js']) {
  fs.copyFileSync(path.join(here, '..', 'src', 'lib', f), path.join(tmp, 'src', 'lib', f));
}
fs.writeFileSync(path.join(tmp, 'src', 'lib', 'shops.js'), 'export const getShopBranding = () => ({});\n');
fs.writeFileSync(path.join(tmp, 'src', 'lib', 'dnd.js'), 'export const adjustForDnd = (d) => d;\n');
fs.writeFileSync(path.join(tmp, 'src', 'trigger-livekit-call.js'),
  'export async function triggerLivekitCall(p) { return globalThis.__trigger(p); }\n');
fs.writeFileSync(path.join(tmp, 'package.json'), '{"type":"module"}');

const PAUSE = path.join(tmp, 'pause.json');
process.env.DISPATCH_PAUSE_FILE = PAUSE;
process.env.WEDGE_LOG_TAIL = '0';
process.env.DISPATCH_MODE = 'live';
delete process.env.SCHEDULER_MAX_PER_TICK;
delete process.env.SCHEDULER_MAX_INFLIGHT;
process.env.PURITY_API_BASE = 'http://api.test';
const alerts = [];
globalThis.fetch = async (url, init) => { alerts.push({ url, body: JSON.parse(init.body) }); return { status: 200, json: async () => ({ sent: true }) }; };

const S = await import(pathToFileURL(path.join(tmp, 'src', 'lib', 'scheduler.js')).href);
const G = await import(pathToFileURL(path.join(tmp, 'src', 'lib', 'dispatch_guard.js')).href);

function fakePrisma(rows) {
  const attempts = [];
  const match = (r, where) => Object.entries(where || {}).every(([k, v]) => {
    if (v && typeof v === 'object' && !(v instanceof Date)) {
      if ('lte' in v) return r[k] <= v.lte;
      if ('gte' in v) return r[k] && r[k] >= v.gte;
      if ('lt' in v) return r[k] && r[k] < v.lt;
    }
    return (r[k] ?? null) === v;
  });
  return {
    rows, attempts, finds: 0,
    scheduledCall: {
      async findMany({ where, take }) { this._p.finds += 1; return rows.filter((r) => match(r, where)).slice(0, take).map((r) => ({ ...r })); },
      async count({ where }) { return rows.filter((r) => match(r, where)).length; },
      async updateMany({ where, data }) {
        const hit = rows.filter((r) => match(r, where));
        hit.forEach((r) => Object.assign(r, data));
        return { count: hit.length };
      },
      async update({ where, data }) {
        const r = rows.find((x) => x.id === where.id);
        for (const [k, v] of Object.entries(data)) {
          r[k] = v && typeof v === 'object' && 'increment' in v ? (r[k] || 0) + v.increment : v;
        }
        return r;
      },
    },
    callAttempt: {
      async create({ data }) { attempts.push({ ...data, endedAt: null, startedAt: new Date() }); return data; },
      async findFirst() { return attempts.filter((a) => !a.endedAt).at(-1) || null; },
      async update({ data }) { const a = attempts.filter((x) => !x.endedAt).at(-1); Object.assign(a, data); return a; },
    },
  };
}
function bind(p) { p.scheduledCall._p = p; return p; }

const row = (id, extra = {}) => ({ id, shop: 'purity', orderId: `o${id}`, orderName: `purity:Lead ${id}`,
  phone: '+919800000000', status: 'queued', attempts: 0, outcome: null, scheduledAt: new Date(0),
  profile: 'purity-coffee-b2b', payload: { lead_id: id }, ...extra });
const tick = () => new Promise((r) => setTimeout(r, 30));

beforeEach(() => { try { fs.unlinkSync(PAUSE); } catch {} alerts.length = 0; });

test('paused queue dispatches nothing', async () => {
  G.setPause('manual', 'founder paused auto-calling');
  const p = bind(fakePrisma([row(1)]));
  let dialled = 0;
  globalThis.__trigger = async () => { dialled += 1; return { room_name: 'r' }; };
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(dialled, 0);
  assert.equal(p.finds, 0);
  assert.equal(p.rows[0].status, 'queued');
});

test('default: 1 dispatch per tick, and never beyond 2 in flight', async () => {
  const p = bind(fakePrisma([row(1), row(2), row(3), row(4)]));
  const pending = [];
  globalThis.__trigger = () => new Promise((res) => pending.push(res)); // calls stay up
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(pending.length, 1);
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(pending.length, 2);
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(pending.length, 2, 'third call must wait for a free in-flight slot');
  pending.forEach((r) => r({ room_name: 'r' }));
  await tick();
});

test('agent never joined: no retry, attempts not counted, labelled dispatch_failed, backend told AGENT_NOT_JOINED', async () => {
  const p = bind(fakePrisma([row(10)]));
  const reported = [];
  globalThis.__trigger = async () => { throw new Error('agent ai-voice-agent-priya did not join room x within 45000ms — refusing to ring customer into silence'); };
  await S.dispatchDue(p, async (r, reason) => reported.push(reason));
  await tick();
  const r = p.rows[0];
  assert.equal(r.status, 'failed');
  assert.equal(r.outcome, 'dispatch_failed');
  assert.equal(r.attempts, 0);
  assert.equal(reported.length, 1);
  assert.match(reported[0], /^AGENT_NOT_JOINED:/);
});

test('Plivo 402: whole queue paused, row not retried or counted, founder alerted', async () => {
  const p = bind(fakePrisma([row(20), row(21)]));
  const reported = [];
  globalThis.__trigger = async () => { throw new Error('SIP call failed: 402 Insufficient Credits (permission_denied)'); };
  await S.dispatchDue(p, async (r, reason) => reported.push(reason));
  await tick();
  assert.equal(p.rows[0].status, 'failed');
  assert.equal(p.rows[0].outcome, 'credits_exhausted');
  assert.equal(p.rows[0].attempts, 0);
  assert.equal(G.getPause().kind, 'credits');
  assert.match(reported[0], /^CREDITS_EXHAUSTED:/);
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].url, 'http://api.test/api/v1/founder/system-alert');
  assert.equal(alerts[0].body.kind, 'CREDITS_EXHAUSTED');
  // the other queued row is not dialled while paused
  let dialled = 0;
  globalThis.__trigger = async () => { dialled += 1; return { room_name: 'r' }; };
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(dialled, 0);
  assert.equal(p.rows[1].status, 'queued');
});

test('busy (the phone rang) keeps the existing retry policy and counts', async () => {
  const p = bind(fakePrisma([row(30)]));
  globalThis.__trigger = async () => { throw new Error('SIP call failed: 486 User Busy (failed_precondition)'); };
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(p.rows[0].status, 'queued');
  assert.equal(p.rows[0].attempts, 1);
});

test('final fail is labelled by what happened, not always no_answer', async () => {
  const p = bind(fakePrisma([row(40, { attempts: 2 })]));
  globalThis.__trigger = async () => { throw new Error('SIP call failed: 486 User Busy (failed_precondition)'); };
  await S.dispatchDue(p, null);
  await tick();
  assert.equal(p.rows[0].status, 'failed');
  assert.equal(p.rows[0].outcome, 'busy');
});
