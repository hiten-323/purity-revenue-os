# Outreach Intelligence V1

Learn from **contactable leads**, apply experience to the **next similar lead**.
Email and AI call stay **independently eligible (BOTH)**. Policy/safety always outrank learning.

## Package

`backend/app/services/outreach_intelligence/`

| Module | Role |
|--------|------|
| models.py | OutreachEvent, OutreachExperienceAggregate, OutreachOutcomeCorrection, experiments |
| event_ledger.py | append-only `record_event`, `sync_from_existing`, emit hooks |
| outcomes.py | LEVEL 0..8, email classes, call commercial signals |
| experience_store.py | similar-lead retrieval, `build_lead_outreach_profile` |
| learning_loop.py | `on_outreach_event`, `recalculate_segment_stats`, corrections |
| report.py | dashboard JSON (delivery ≠ success) |
| boot.py | `register_outreach_intelligence(app, engine)` |

API: `/api/v1/outreach-intelligence/*` via `outreach_intelligence_router.py`

## Safety

- Does **not** enable SMART_OUTREACH_ENABLED / AUTO_OUTREACH_ENABLED / AI_CALLING_ENABLED
- WhatsApp stays off (AISENSY_ENABLED=0)
- Learning never returns eligibility/suppress keys
- `POST /recalculate` is dry: stats only
- Emit hooks are best-effort and never break send/call paths

## Schema

`Base.metadata.create_all` after importing models (project pattern). Call
`ensure_outreach_intelligence_schema(engine)` at startup if needed.

## Dry-run

1. Keep flags at 0
2. `POST /sync` then `POST /recalculate`
3. `GET /report` and `GET /lead/{id}/profile`

## Main.py wiring (required)

After `Base.metadata.create_all(bind=engine)` in `backend/app/main.py` startup, add:

```python
from app.services.outreach_intelligence.boot import register_outreach_intelligence
register_outreach_intelligence(app, engine)
```

Do **not** enable SMART_OUTREACH_ENABLED / AUTO_OUTREACH_ENABLED / AI_CALLING_ENABLED.
WhatsApp stays off (AISENSY_ENABLED=0).
