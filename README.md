# Purity Beans — Founder Revenue OS

A B2B sales operating system for Pure Pantry Provisions (premium instant coffee,
Abohar, Punjab). It discovers business buyers, resolves them into accounts,
governs how often each account may be contacted, classifies leads from evidence
and history, renders category-specific outreach, records replies and calls, and
decides the next action per account.

## Decision architecture

`decision_engine.evaluate_next_action()` is the sole permission authority.
Other services supply evidence or shape the already-authorised action; they do
not create a second send decision.

The intended lifecycle is:

```text
evidence -> classify -> strategy -> gates -> evaluate_next_action()
    -> send | wait | catalogue | cooldown | FOUNDER_REVIEW
    -> proof + intent + memory -> learn -> next
```

The repository default keeps automated outreach **OFF**. Enabling it is a
deliberate production rollout after the controlled-send gate. Learning stores
measured patterns but does not silently change the frozen outreach weights or
rate limits.

Catalogue is earned: a cold lead may be asked whether they want it, but the
catalogue link is sent only after a recorded catalogue request. It is never
attached to the universal cold-email signature.

WhatsApp API sending requires recorded explicit/opted-in consent and an
approved provider template path. `IMPLIED_B2B` is not treated as WhatsApp
opt-in. Cold WhatsApp remains a founder-controlled/manual path.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + SQLAlchemy, SQLite in WAL mode |
| Frontend | Next.js 15 (App Router) |
| Process management | pm2 (`ecosystem.config.js`) |
| Tunnel | cloudflared |

## Layout

```text
backend/
  app/api/endpoints.py       HTTP surface
  app/models/models.py       ORM models + before_insert enforcement
  app/services/              business logic
  run_server.py              uvicorn entrypoint
  worker.py                  isolated enrichment process
  smart_outreach_worker.py   isolated automated-outreach process
frontend/                    Next.js dashboard
ecosystem.config.js          pm2 process definitions; reads secrets from backend/.env
```

### Core services

| Module | Responsibility |
|---|---|
| `decision_engine.py` | Sole next-action permission authority |
| `smart_outreach.py` | Classification, message strategy, provider execution |
| `outreach_lifecycle.py` | Inbound memory, intent and measured lifecycle learning |
| `trust_promoter.py` | Contact trust states and `may_send()` |
| `account_graph.py` | Account resolution and company-level frequency governance |
| `sequence_engine.py` | Five-touch cadence; exits on reply |
| `reply_intelligence.py` | Sender and intent classification on inbound mail |
| `phone_intelligence.py` | Call queue, call logging and funnel analysis |
| `send_queue.py` | Founder/manual send approval path where required |
| `heartbeat.py` | Component liveness and queue health |

`models.py` contains enforcement chokepoints including draft admission, send
proof, and idempotency. Proven automated sends are mirrored into the
`WorkflowEvent` ledger so account frequency governance and cadence see the
same fact.

## Contact provenance

Phone data has three operational tiers:

- `FIRST_PARTY` — business/brand/founder supplied; authoritative and protected
  from search overwrite.
- `SEARCH` — directory/search candidate; may be enriched or replaced, but
  never earns `phone_verified` from listing alone.
- `UNSOURCED` — candidate only; confirmation is required.

The call sheet is a derived capture layer, not a second CRM. Generate it close
to the calling session; import confirmed call outcomes back into Revenue OS.
A confirmed founder call can establish decision-maker, supplier, consumption,
consent, next action and first-party phone evidence.

## Setup

Requires Python 3.11 and Node 18+.

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm install && npm run build
```

Create `backend/.env` — it is gitignored and must never be committed. See
`backend/.env.example` for the complete configuration surface.

`ecosystem.config.js` reads credentials from `backend/.env`; credentials are
not committed to the repository.

Then:

```bash
pm2 start ecosystem.config.js
```

Health check: `GET /api/v1/health`.

## Data

The lead database is **not** included in this repository. It holds contact
records for real businesses — names, email addresses and phone numbers — and
`*.db` is gitignored. The schema lives in `backend/app/models/`.

## Production safety gates

Before enabling automated outreach:

1. Verify credentials without printing secret values.
2. Confirm the authoritative PM2 cwd is `purity-revenue-os`.
3. Confirm enrichment cannot promote search data to verified contact data.
4. Confirm first-party phone provenance remains immutable.
5. Confirm account frequency governance sees both email and WhatsApp events.
6. Confirm `evaluate_next_action()` is the only permission authority.
7. Confirm the sequence engine is the sole cadence authority.
8. Confirm cold catalogue links are absent and catalogue delivery is request-driven.
9. Run the full unit/integrity gate.
10. Perform a controlled send and verify provider proof before unattended automation.

The legacy `purity_beans_ai\jules_session` tree is rollback infrastructure only
and is not a development or production target.
