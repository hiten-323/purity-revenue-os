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
