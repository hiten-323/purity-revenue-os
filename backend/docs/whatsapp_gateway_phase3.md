# WhatsApp Gateway — Phase 3 Deployment Hardening

**Status:** Hardened for sandbox validation. **Not** connected to live Klaviyo flows.

## Secrets (backend/.env only — never commit)

```bash
ENVIRONMENT=production          # or omit for local dev
AISENSY_API_KEY=...             # server-side only; never in Klaviyo
KLAVIYO_WEBHOOK_SECRET=...      # mandatory when ENVIRONMENT=production
AISENSY_WEBHOOK_SECRET=...      # mandatory in production once AiSensy supports it
GATEWAY_ADMIN_SECRET=...        # protects GET /webhooks/gateway/ledger
WEBHOOK_MAX_SKEW_SECONDS=300    # optional; used only if provider sends timestamp
```

Supply secrets via `backend/.env` (gitignored). `ecosystem.config.js` already loads that file for PM2.

### Production fail-closed rules

| Condition | Behaviour |
|-----------|-----------|
| `ENVIRONMENT=production` and `KLAVIYO_WEBHOOK_SECRET` empty | **401** on Klaviyo + sandbox endpoints |
| `ENVIRONMENT=production` and `AISENSY_WEBHOOK_SECRET` empty | **401** on AiSensy status endpoint |
| Non-production + secret empty | Dev bypass with warning log |
| Shared secret header or HMAC matches | Allowed |

Headers accepted for shared secret:
- `X-Webhook-Secret`
- `X-Klaviyo-Webhook-Secret` / `X-AiSensy-Webhook-Secret`
- `Authorization: Bearer <secret>`

HMAC headers: `X-Webhook-Signature` or `X-Hub-Signature-256` (`sha256=<hex>` or bare hex of body).

## Endpoints

| Method | Path | Auth | Real send? |
|--------|------|------|------------|
| POST | `/api/v1/webhooks/klaviyo/whatsapp` | KLAVIYO_WEBHOOK_SECRET | Yes (if configured) |
| POST | `/api/v1/webhooks/aisensy/status` | AISENSY_WEBHOOK_SECRET | N/A |
| POST | `/api/v1/webhooks/sandbox/whatsapp` | KLAVIYO_WEBHOOK_SECRET | **Never** |
| GET | `/api/v1/webhooks/gateway/ledger` | GATEWAY_ADMIN_SECRET | N/A |

## Sandbox example

```bash
curl -X POST https://<host>/api/v1/webhooks/sandbox/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $KLAVIYO_WEBHOOK_SECRET" \
  -d '{
    "profile_id": "TEST_PROFILE",
    "phone": "919000000000",
    "lifecycle_stage": "CHECKOUT",
    "source_event_id": "sandbox-evt-001",
    "order_id": "TEST-ORDER",
    "user_name": "Test",
    "template_params": ["Test"],
    "whatsapp_marketing_consent": "SUBSCRIBED"
  }'
```

Use only a test number you control. Never paste a real customer phone into docs or git.

## Deployment checklist (existing PM2 + cloudflared stack)

1. Ensure `backend/purity_beans.db` (or `DATABASE_URL`) is on **persistent disk**, not ephemeral container storage.
2. Add the four secrets above to `backend/.env`.
3. Restart API: `pm2 restart purity-api`.
4. Confirm HTTPS via existing cloudflared tunnel.
5. Health: `GET /health`.
6. Do **not** point any live Klaviyo flow at these URLs until Phase 4+ approval.

Do **not** expose `/webhooks/gateway/ledger` without admin secret.

## Replay protection

| Provider | Signature | Timestamp | Event ID | Status |
|----------|-----------|-----------|----------|--------|
| Klaviyo custom webhook to our URL | UNKNOWN for this integration pattern | UNKNOWN | source_event_id (our field) | Partial (our idempotency key) |
| AiSensy status callback | UNKNOWN | UNKNOWN | UNKNOWN | Adaptive parser only |

If a provider later documents a timestamp header, set it; we already reject excessive skew when present.

## Draft-only Klaviyo test flow design (DO NOT CREATE YET)

Isolate completely from SeqaGf, RiT6Qz, U9rMWn, Shy4Vb.

Suggested structure (manual UI later):

1. Trigger: manual / segment of internal test profiles only.
2. Webhook action → `POST /api/v1/webhooks/sandbox/whatsapp` (sandbox first).
3. JSON body fields:
   - `profile_id`: `{{ person.klaviyo_id }}` or `{{ person.id }}`
   - `phone`: `{{ person.phone_number }}`
   - `lifecycle_stage`: `CHECKOUT` (literal for test)
   - `campaign_name`: test campaign name only
   - `source_event_id`: `{{ event.id }}` or unique test id
   - `order_id`: `{{ event.extra.OrderId|default:"" }}`
   - `template_params`: name etc.
   - `whatsapp_marketing_consent`: `{{ person.subscriptions.whatsapp.marketing.consent }}`
4. Status: **Draft** only. Never live until Phase 4.

## AiSensy callback discovery (manual)

Do not send real customer WhatsApp to discover schema.

1. AiSensy dashboard → Integrations → locate Webhook / Delivery callback URL.
2. Point temporarily to https://webhook.site (or similar).
3. Send **one** message to a number **you own** from a dedicated test API campaign.
4. Save full JSON body + headers.
5. Share that sample for parser finalisation.

## Consent matrix (proven in unit tests)

| Case | Expected |
|------|----------|
| SUBSCRIBED | allowed (sandbox_sent) |
| null / unknown | BLOCKED_CONSENT |
| UNSUBSCRIBED | BLOCKED_CONSENT |
| missing phone | BLOCKED_PHONE |
| invalid phone | BLOCKED_PHONE |
| duplicate event | duplicate |

## Tests

```bash
cd backend
python -m unittest tests.test_whatsapp_gateway tests.test_whatsapp_gateway_phase3 -v
python -m unittest tests.test_whatsapp_sender -v
```
