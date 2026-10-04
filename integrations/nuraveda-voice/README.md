# Nuraveda voice sidecar: call-result integration

These files belong in the **ai-voice-agent** checkout (the Node/LiveKit
sidecar run by PM2 as `nuraveda-voice` and `nuraveda-voice-agent`). They are
kept here so they are versioned and reviewed with the backend change. They
are **not** deployed automatically.

| file | goes to | why |
|---|---|---|
| `profiles/purity-coffee-b2b/index.js` | `ai-voice-agent/profiles/purity-coffee-b2b/index.js` (new) | `server.js` `onFinalFail` calls `profile.onNoAnswer` when the scheduler gives up (no answer / busy / SIP error / stuck dispatch). Without this file, those calls were never reported, so leads stayed `CALLING`. The hook POSTs `/api/v1/founder/ai-call-status`. |
| `profiles/purity-coffee-b2b/agent.js.patch` | apply to `ai-voice-agent/profiles/purity-coffee-b2b/agent.js` | `renderContext` reads `call_ref`, `opener_variant`, `script_variant`, `call_brief`; the system prompt gets the brief as advisory notes (hard rules still win); `reportOutcome` sends `call_ref` + variant + timestamps (+ `turns` when the engine passes them) so Python matches the exact attempt. |
| `export_call_evidence.mjs` | run from the ai-voice-agent dir | Read-only Prisma export of ScheduledCall/CallAttempt/CallTurn dispositions for `backend/scripts/backfill_stuck_calls.py --sidecar-export`. |

Optional engine improvement (`src/livekit-agent.js`, not patched here since
the live copy diverges): pass `{ turns, room_name, sip_call_id, answered_at,
termination_reason: 'HANGUP_NO_OUTCOME' }` as the `extra` argument of the
Close-handler `reportOutcome(ctxMut.v, 'FAILED', ...)` call (~line 689), from
the turn buffer `postTurn` already keeps. Everything works without it; the
transcript simply stays in the sidecar `CallTurn` table.

Apply order: backend first (the new endpoint must exist), then these files,
then restart `nuraveda-voice` and `nuraveda-voice-agent`. The backend accepts
reports with or without the new fields, so order mistakes degrade to "no
extra detail", never to a lost outcome.

## Dispatch guard (2026-10-03: volume caps, wedge detection, credit errors, never-rang)

| file | goes to | why |
|---|---|---|
| `src/lib/dispatch_guard.js` | `ai-voice-agent/src/lib/dispatch_guard.js` (new) | Volume caps, pause flag, error classes, wedge monitor (see module docstring). |
| `src/lib/scheduler.js` | replaces `ai-voice-agent/src/lib/scheduler.js` | Uses the guard: 1 dispatch/tick, ≤2 in flight, no dispatch while paused; agent-never-joined / pre-ring SIP failures are `failed/dispatch_failed` with **no retry and no attempt increment** and are reported to the backend as never-rang; Plivo 402/4030 pauses the whole queue (`kind=credits`) and alerts the founder; final-fail outcome is labelled `busy`/`no_answer`/`dispatch_error` by what happened. |
| `src/server.js.patch` | apply to `ai-voice-agent/src/server.js` | `/calls/dispatch` returns 503 while paused (the backend then releases its reservation instead of counting an attempt); `/health` shows `dispatch_pause` and `dispatch_limits`. |
| `profiles/purity-coffee-b2b/index.js` | (updated) | Maps `AGENT_NOT_JOINED` / `PRE_RING_FAILURE` / `CREDITS_EXHAUSTED` / `dispatch_failed` so the backend releases the attempt (`DISPATCH_FAILED`, never `NO_ANSWER`). |

Env (all optional): `SCHEDULER_MAX_PER_TICK` (1), `SCHEDULER_MAX_INFLIGHT` (2),
`DISPATCH_PAUSE_FILE` (`<cwd>/data/dispatch_pause.json`), `AGENT_LOG_PATH`
(`~/.pm2/logs/nuraveda-voice-agent-out.log`), `AGENT_PM2_NAME`
(`nuraveda-voice-agent`), `WEDGE_JOIN_FAILURES` (2), `WEDGE_UNRESPONSIVE_COUNT` (2),
`WEDGE_WINDOW_MS` (300000), `WEDGE_MAX_RESTARTS_PER_HOUR` (3),
`WEDGE_RECOVERY_GRACE_MS` (90000), `WEDGE_LOG_TAIL` (`0` disables tailing).

Pause semantics: `kind=wedge` is cleared automatically after a healthy agent
restart (the agent is restarted only when no call is in flight, at most
`WEDGE_MAX_RESTARTS_PER_HOUR`, then it stays paused and the founder is
emailed). `kind=credits` and `kind=manual` are never cleared automatically:
to resume, top up, then delete the pause file deliberately.

Backend side (this PR): `POST /api/v1/founder/ai-call-status` with reason
`DISPATCH_FAILED` / `AGENT_NOT_JOINED` / `PRE_RING_FAILURE` /
`CREDITS_EXHAUSTED` releases the attempt (`call_intelligence.never_rang`);
`POST /api/v1/founder/system-alert` emails the founder (rate limited) via the
founder-only mail path in `founder_brief`. The worker's AI-call cycle is
capped by `call_intelligence.volume.dial_budget` (`AI_CALL_CYCLE_MAX_DIALS`
default 5, `AI_CALL_MAX_INFLIGHT` default 2) — wired into the live
`ai_call_cycle.run_cycle` (that module is not on `main` yet).
