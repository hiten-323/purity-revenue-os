# Purity Beans — Founder Revenue OS

A B2B sales operating system for Pure Pantry Provisions (premium instant coffee,
Abohar, Punjab). It discovers business buyers, resolves them into accounts,
governs how often each account may be contacted, drafts outreach for founder
approval, records replies and calls, and decides the next action per account.

Nothing in this system sends on its own. Every outbound communication requires
explicit founder approval.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + SQLAlchemy, SQLite in WAL mode |
| Frontend | Next.js 15 (App Router) |
| Process management | pm2 (`ecosystem.config.js`) |
| Tunnel | cloudflared |

## Layout

```
backend/
  app/api/endpoints.py       HTTP surface
  app/models/models.py       ORM models + before_insert enforcement
  app/services/              business logic (see below)
  run_server.py              uvicorn entrypoint (forces SelectorEventLoop)
  worker.py                  isolated enrichment process
frontend/                    Next.js dashboard
ecosystem.config.js          pm2 process definitions; reads secrets from backend/.env
```

### Core services

| Module | Responsibility |
|---|---|
| `trust_promoter.py` | Contact trust states and the single `may_send()` authority |
| `account_graph.py` | Account resolution by domain/brand root; company-level frequency cap |
| `sequence_engine.py` | Multi-touch cadence, exits on reply |
| `reply_intelligence.py` | Sender and intent classification on inbound mail |
| `phone_intelligence.py` | Call queue, call logging, funnel and bottleneck analysis |
| `send_queue.py` | Approval as a persisted event, with TTL and revocation |
| `heartbeat.py` | Component liveness and queue health |
| `scrapling_client.py` | Authenticated Streamable HTTP MCP client for Scrapling |
| `scrapling_harvester.py` | First-party website contact enrichment with provenance |

Three `before_insert` listeners in `models.py` act as enforcement chokepoints:
draft admission (no drafts for unreachable contacts), send proof (an
`EMAIL_SENT` row requires a provider message id), and idempotency (duplicate
sends are rejected rather than recorded).

## Scrapling Web Intelligence

Purity Revenue OS can optionally connect to a separately running D4Vinci
Scrapling MCP server. Scrapling is a **read-only web research/enrichment provider** here; it has no authority to send email, WhatsApp messages, or calls.

The worker uses Scrapling only when `SCRAPLING_ENABLED=1`. It processes a small
bounded batch of leads with websites and missing contact fields, records the
source as first-party website evidence, and leaves all existing trust and
outbound gates in control of sending.

### Install the server

```bash
pip install "scrapling[ai]"
scrapling install
```

Run authenticated Streamable HTTP locally:

```bash
set SCRAPLING_MCP_AUTH_TOKEN=replace-with-a-long-random-token
scrapling-mcp --http
```

PowerShell:

```powershell
$env:SCRAPLING_MCP_AUTH_TOKEN = "replace-with-the-same-token"
scrapling-mcp --http
```

Then in `backend/.env`:

```dotenv
SCRAPLING_ENABLED=1
SCRAPLING_MCP_URL=http://127.0.0.1:8000/mcp
SCRAPLING_MCP_AUTH_TOKEN=replace-with-the-same-token
SCRAPLING_CYCLE_HOURS=24
SCRAPLING_LIMIT=20
SCRAPLING_ALLOW_STEALTH=0
```

The server binds to localhost by default. If it is ever exposed over a
network, use TLS, authentication, and an allowed-host configuration. The
application does not expose the Scrapling MCP port through the FastAPI API.

### Routing policy

- Fast HTTP fetch first for normal sites.
- Dynamic browser fetch when the static response is insufficient.
- Stealth fetch only when `SCRAPLING_ALLOW_STEALTH=1`.
- Existing HTTP harvesters remain available as a fallback path.

This keeps Scrapling additive instead of making the revenue system dependent
on a single scraping provider.

## Setup

Requires Python 3.11 and Node 18+.

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm install && npm run build
```

Create `backend/.env` — it is gitignored and must never be committed:

```
ZOHO_APP_PASSWORD=
CEREBRAS_API_KEY=
SHOPIFY_TOKEN=
SHOPIFY_WEBHOOK_SECRET=
API_ADMIN_SECRET=
VAPI_WEBHOOK_SECRET=
GOOGLE_MAPS_API_KEY=
AISENSY_API_KEY=
WHATSAPP_WEBHOOK_SECRET=
```

ecosystem.config.js reads every secret from that file at load time and warns
loudly on any that are missing. It never contains credentials itself.

Then:

```bash
pm2 start ecosystem.config.js
```

All state-changing `/api/v1` routes require `X-API-Admin-Secret: <API_ADMIN_SECRET>`
or `Authorization: Bearer <API_ADMIN_SECRET>`. The service returns `503` until
the secret is configured; reads remain public. Provider webhooks use their
dedicated secrets and also fail closed when unset.

Health check: `GET /api/v1/health` (returns `503` when the database or required
Redis dependency is unavailable).

## Data

The lead database is **not** included in this repository. It holds contact
records for real businesses — names, email addresses and phone numbers
belonging to third parties — and `*.db` is gitignored. The schema lives in
`backend/app/models/`, so the tables are created on first run.

## Status

Working: account resolution and frequency governance, contact trust, draft
admission, send-proof enforcement, reply classification, approval queue, call
queue and funnel analysis, dashboard.

WhatsApp outbound remains gated on legitimate consent and provider readiness.
It requires an approved Meta template before business-initiated WhatsApp can
be used for contacts who have not opted in; until that exists, the AiSensy
transport stays idle for those cold-contact sends.

### Cross-channel account frequency cap

The account-level frequency cap is **channel-aware**. `account_graph.py`
counts both `EMAIL_SENT` and `WHATSAPP_SENT` as outreach events consuming the
same account cooldown slot. This prevents a WhatsApp touch from bypassing the
account-level cooldown and allowing another branch/contact at the same account
to receive cold outreach through email.

A dedicated account-cap test covers the cross-channel behavior. The cap is
still subject to the separate consent, trust, approval, and provider gates
before any outbound communication can occur.
