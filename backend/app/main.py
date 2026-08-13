import os
from pathlib import Path

# Load .env FIRST — before any other imports that read env vars at module level
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api.endpoints import router as api_router
from app.api.founder_router import router as founder_router
from app.database.database import engine, Base, get_db
import redis

app = FastAPI(title="Purity Beans AI Operating System")

def _quarantine_unverifiable_addresses():
    """
    Quarantine every stored address that cannot actually receive mail.

    Runs at boot because the write that put them there is NOT in this codebase.
    82 addresses of the form contact@{company-name-without-spaces}.com appeared
    marked VALID and email_verified=1, with ZERO events recording the write -
    every in-app path logs one, so these were written straight into SQLite by
    something outside the application. Another tool edits this database.

    Since the writer cannot be fixed from here, the write is made harmless
    instead: on every start, any address the verifier rejects is cleared and
    tombstoned PURGED before a single send can be attempted. Whatever writes
    them, they never reach the send gate.
    """
    try:
        from datetime import datetime
        from app.database.database import SessionLocal
        from app.models.models import B2BLead, WorkflowEvent
        from app.services.email_verifier import verify_email

        db = SessionLocal()
        try:
            suspect = db.query(B2BLead).filter(
                B2BLead.email != "", B2BLead.email.isnot(None),
                B2BLead.email_verified == True).all()          # noqa: E712
            killed = 0
            for l in suspect:
                try:
                    r = verify_email(l.email.strip(), l.company or "", l.website or "")
                except Exception:
                    continue          # never let a DNS blip purge a good address
                if r.get("status") == "VALID":
                    continue
                old = l.email
                l.email = ""
                l.email_verified = False
                l.email_verification_status = "PURGED"
                db.add(WorkflowEvent(
                    lead_id=l.id, event_type="CONTACT_PURGED", actor="SYSTEM",
                    channel="boot_integrity",
                    payload={"removed_email": old,
                             "reason": f"failed verification at boot: {r.get('reason')}",
                             "note": "written outside the application - no event "
                                     "recorded the address being set"},
                    occurred_at=datetime.utcnow()))
                killed += 1
            if killed:
                db.commit()
                print(f"[boot-integrity] quarantined {killed} unverifiable address(es)")
        finally:
            db.close()
    except Exception as e:
        print(f"[boot-integrity] skipped: {e}")


@app.on_event("startup")
async def startup():
    # The trust sweep supersedes the earlier address-only quarantine: it also
    # detects rows changed outside the application and demotes them.
    try:
        from app.database.database import SessionLocal as _S
        from app.services.contact_trust import sweep as _sweep
        _db = _S()
        try:
            _r = _sweep(_db)
            if _r.get("demoted_to_purged") or _r.get("out_of_band_flagged"):
                print(f"[trust-sweep] {_r}")
        finally:
            _db.close()
    except Exception as _e:
        print(f"[trust-sweep] skipped: {_e}")
    import app.models.models
    # Register founder_actions table (model lives in the service module).
    import app.services.founder_actions  # noqa: F401
    Base.metadata.create_all(bind=engine)
    
    # Run dynamic SQLite migrations for call fields and call_history
    from app.database.database import SessionLocal
    db = SessionLocal()
    try:
        conn = db.connection().connection
        cursor = conn.cursor()
        
        # Get existing columns of b2b_leads
        cursor.execute("PRAGMA table_info(b2b_leads);")
        existing_cols = [col[1] for col in cursor.fetchall()]
        
        new_cols = {
            "call_status": "VARCHAR",
            "call_quality_score": "INTEGER DEFAULT 0",
            "call_summary": "TEXT",
            "call_transcript": "TEXT",
            "call_recording_url": "TEXT",
            "vapi_call_id": "VARCHAR",
            "human_answered": "BOOLEAN DEFAULT FALSE",
            "call_duration_seconds": "INTEGER DEFAULT 0",
            "call_attempts": "INTEGER DEFAULT 0",
            "last_call_date": "DATETIME",
            "do_not_call": "BOOLEAN DEFAULT FALSE",
            "dnc_reason": "VARCHAR",
            "dnc_override_by": "VARCHAR",
            "dnc_override_reason": "VARCHAR",
            "call_cost": "FLOAT DEFAULT 0.0",
            "call_provider": "VARCHAR",
            "call_minutes": "FLOAT DEFAULT 0.0",
            "current_brand": "VARCHAR",
            "current_supplier": "VARCHAR",
            "price_per_kg": "FLOAT",
            "competitor_strength": "INTEGER DEFAULT 0",
            "monthly_consumption": "VARCHAR",
            "decision_maker": "VARCHAR",
            "sample_requested": "BOOLEAN DEFAULT FALSE",
            "meeting_requested": "BOOLEAN DEFAULT FALSE",
            "budget_range": "VARCHAR",
            "objection_reason": "VARCHAR",
            "next_followup_date": "VARCHAR",
            "call_estimated_value": "FLOAT DEFAULT 0.0",
            "blended_realization_per_kg": "FLOAT DEFAULT 1400.0",
            "lead_tier": "VARCHAR",
            "company_normalized": "VARCHAR",
            "lead_owner": "VARCHAR DEFAULT 'Hiten'",
            "lead_locked_until": "DATETIME",
            "acquisition_source": "VARCHAR",
            "consent_status": "VARCHAR DEFAULT 'UNKNOWN'",
            "consent_source": "VARCHAR",
            "consent_timestamp": "DATETIME",
            "lead_temperature_score": "FLOAT DEFAULT 0.0",
            "lead_temperature_tier": "VARCHAR",
            "reality_score": "FLOAT DEFAULT 0.0",
            "reality_grade": "VARCHAR DEFAULT 'COLD'",
            "freight_cost_estimate": "FLOAT DEFAULT 0.0",
            "discount_given": "FLOAT DEFAULT 0.0",
            "sampling_cost_total": "FLOAT DEFAULT 0.0",
            "credit_period_days": "INTEGER DEFAULT 30",
            "actual_margin_net": "FLOAT DEFAULT 0.0",
            "sample_followup_status": "VARCHAR DEFAULT 'GREEN'",
            "days_to_cash": "INTEGER DEFAULT 0",
            "deal_health": "VARCHAR DEFAULT 'GREEN'",
            "call_outcome_last": "VARCHAR",
            "velocity_days_reply": "INTEGER DEFAULT 0",
            "phone_verified": "BOOLEAN DEFAULT FALSE",
            "phone_source": "VARCHAR",
            "contact_searched_at": "DATETIME",
            "maps_rating": "FLOAT",
            "maps_reviews_count": "INTEGER",
            "maps_types": "VARCHAR",
            "email_trust": "VARCHAR DEFAULT 'UNKNOWN'",
            "email_source": "VARCHAR",
            "email_collected_at": "DATETIME",
            "email_verified_at": "DATETIME",
            "phone_trust": "VARCHAR DEFAULT 'UNKNOWN'",
            "phone_collected_at": "DATETIME",
            "phone_verified_at": "DATETIME",
            "contact_fingerprint": "VARCHAR",
            "contact_status": "VARCHAR DEFAULT 'CONTACTABLE'",
            "contact_status_reason": "VARCHAR",
            "business_trust": "VARCHAR DEFAULT 'UNVERIFIED'",
            "contact_confidence": "INTEGER DEFAULT 0",
            "coffee_buying_score": "INTEGER DEFAULT 0",
            "coffee_buying_evidence": "VARCHAR",
            "place_id": "VARCHAR",
            "serves_breakfast": "BOOLEAN",
            "place_details_checked_at": "DATETIME",
            "business_status": "VARCHAR",
            "evidence_collected_at": "DATETIME",
            "intelligence_status": "VARCHAR DEFAULT 'NOT_STARTED'",
            "division_source": "VARCHAR",
            "division_confidence": "FLOAT DEFAULT 0.25",
            "division_verified": "BOOLEAN DEFAULT 0"
        }
        
        for col, col_type in new_cols.items():
            if col not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE b2b_leads ADD COLUMN {col} {col_type};")
                    print(f"Migration: Added column {col} to b2b_leads")
                except Exception as e:
                    print(f"Migration Error adding {col}: {e}")
                    
        # Check and migrate gov_tenders columns
        cursor.execute("PRAGMA table_info(gov_tenders);")
        existing_gov_cols = [col[1] for col in cursor.fetchall()]
        new_gov_cols = {
            "opportunity_score": "INTEGER DEFAULT 0",
            "win_probability": "INTEGER DEFAULT 0",
            "required_products": "VARCHAR",
            "suggested_pricing": "FLOAT DEFAULT 0.0",
            "expected_margin": "FLOAT DEFAULT 0.0",
            "risk_level": "VARCHAR",
            "proposal_text": "TEXT",
            "compliance_checklist": "TEXT",
            "missing_documents": "TEXT",
            "bid_strategy": "TEXT",
            "organization_id": "INTEGER",
            "contact_officer": "VARCHAR",
            "official_email": "VARCHAR",
            "official_phone": "VARCHAR",
            "contact_confidence": "INTEGER DEFAULT 0",
            "contact_verified": "BOOLEAN DEFAULT FALSE",
            "product_match_pct": "INTEGER DEFAULT 0",
            "bid_readiness_pct": "INTEGER DEFAULT 0",
            "win_confidence_pct": "INTEGER DEFAULT 0",
            "revenue_opportunity_score": "FLOAT DEFAULT 0.0",
            "emd_amount": "FLOAT DEFAULT 0.0",
            "msme_exemption": "BOOLEAN DEFAULT FALSE",
            "recommended_action": "VARCHAR",
            "ai_analysis": "JSON",
            "missing_documents_list": "JSON"
        }
        for col, col_type in new_gov_cols.items():
            if col not in existing_gov_cols:
                try:
                    cursor.execute(f"ALTER TABLE gov_tenders ADD COLUMN {col} {col_type};")
                    print(f"Migration: Added column {col} to gov_tenders")
                except Exception as e:
                    print(f"Migration Error adding {col} to gov_tenders: {e}")

        _freeze_migrations = {
            "organizations": {
                "annual_revenue": "FLOAT",
                "estimated_coffee_consumption_kg": "FLOAT",
                "branches": "INTEGER",
                "warehouse_count": "INTEGER",
                "current_supplier": "VARCHAR",
                "competitors": "JSON",
                "distribution_reach": "VARCHAR",
                "product_categories": "JSON",
                "expansion_plans": "VARCHAR",
                "buying_frequency": "VARCHAR",
                "average_order_size": "FLOAT",
                "payment_behaviour": "VARCHAR",
            },
            "revenue_opportunities": {
                "founder_hours": "FLOAT",
                "expected_roi": "FLOAT",
                "expected_collection": "FLOAT",
                "expected_collection_date": "DATETIME",
                "expected_close_date": "DATETIME",
                "risk_level": "VARCHAR",
                "commercial_stage": "VARCHAR",
            },
            "workflow_events": {
                "opportunity_id": "INTEGER",
            },
        }
        for table, cols in _freeze_migrations.items():
            try:
                cursor.execute(f"PRAGMA table_info({table});")
                have = [c[1] for c in cursor.fetchall()]
            except Exception:
                have = []
            for col, col_type in cols.items():
                if col not in have:
                    try:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};")
                        print(f"Migration: Added column {col} to {table}")
                    except Exception as e:
                        print(f"Migration Error adding {col} to {table}: {e}")

        conn.commit()

        cursor.execute("SELECT id, division, company FROM b2b_leads;")
        leads_to_migrate = cursor.fetchall()
        
        category_map = {
            "grocery": "retail_kirana",
            "kirana_store": "retail_kirana",
            "retail": "retail_kirana",
            "retail_chain": "grocery_chain",
            "hotel_canteen": "hotel",
            "wholesaler_agglo": "wholesaler",
            "gifting": "corporate_gifting",
            "corporate": "corporate_office",
            "tender": "institutional_buyer"
        }
        
        for lead_id, old_div, company_name in leads_to_migrate:
            old_div_lower = (old_div or "").lower().strip()
            if old_div_lower in (
                "distributor", "wholesaler", "modern_trade", "supermarket", "grocery_chain",
                "retail_kirana", "corporate_office", "office_pantry", "manufacturing",
                "facility_management", "hotel", "restaurant", "cafe", "hospital",
                "school", "college", "government", "corporate_gifting", "private_label",
                "exporter", "institutional_buyer", "needs_reclassification", "unknown"
            ):
                continue
            
            if old_div_lower in category_map:
                new_div = category_map[old_div_lower]
            elif old_div_lower in ("horeca", "education_mess"):
                new_div = "needs_reclassification"
            else:
                new_div = "unknown"
                
            cursor.execute(
                "UPDATE b2b_leads SET division = ?, industry = ? WHERE id = ?;",
                (new_div, new_div.replace("_", " ").title(), lead_id)
            )
        conn.commit()
        print("Migration: Upgraded B2B lead categories to V2 standard keys.")
    except Exception as e:
        print(f"Migration failed: {e}")
    finally:
        db.close()

    if os.getenv("AUTOWARM_IN_PROCESS", "0") == "1":
        try:
            from app.api.endpoints import start_auto_warm_worker
            start_auto_warm_worker()
            print("Auto-Warm Engine: started IN-PROCESS (not recommended)")
        except Exception as e:
            print(f"Auto-Warm Engine failed to start: {e}")
    else:
        print("Auto-Warm Engine: delegated to the separate purity-worker process")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000", "http://192.168.1.7:3000",
        "http://localhost:3001", "http://127.0.0.1:3001"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(founder_router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"status": "VP Sales AI is online"}

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    health_status = {
        "status": "healthy",
        "database": "disconnected",
        "redis": "disconnected"
    }
    
    # Check Database
    try:
        db.execute(text("SELECT 1"))
        health_status["database"] = "connected"
    except Exception as e:
        health_status["status"] = "unhealthy"

    # Check Redis
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.from_url(redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
        if r.ping():
            health_status["redis"] = "connected"
    except Exception as e:
        pass # Optional dependency for now if it fails
        
    return health_status
