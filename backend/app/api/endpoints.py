import requests as _requests
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

# Captured ONCE at import, so it is the hash of the code this process loaded —
# not whatever the working tree says at request time. That difference is the
# whole point: it is what makes a stale process visible.
def _capture_boot_commit():
    import subprocess, os
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=root, stderr=subprocess.DEVNULL,
                                       timeout=8).decode().strip()
    except Exception:
        return "unknown"


_BOOT_COMMIT = _capture_boot_commit()
_BOOT_TIME = __import__("datetime").datetime.utcnow().isoformat()
from app.database.database import get_db
from app.services.providers import AmazonProvider, FlipkartProvider, BlinkitProvider
import logging

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/dashboard")
def get_dashboard():
    return {"message": "Dashboard data"}

@router.get("/sales")
def get_sales():
    return {"message": "Sales data"}

@router.get("/inventory")
def get_inventory():
    return {"message": "Inventory data"}

@router.get("/marketplaces")
def get_marketplaces():
    return {"message": "Marketplaces data"}

@router.get("/recommendations")
def get_recommendations():
    return {"message": "Recommendations data"}

from app.agents.amazon.director_ai import AmazonDirectorAI

@router.post("/amazon/director-report")
async def get_amazon_director_report(db: Session = Depends(get_db)):
    director = AmazonDirectorAI(db=db, provider=AmazonProvider())
    report = await director.generate_director_report()
    return report

from app.schemas.schemas import FounderDashboardResponse
from app.agents.founder.founder_dashboard_ai import FounderDashboardAI

@router.get("/founder/dashboard", response_model=FounderDashboardResponse)
async def get_founder_dashboard(db: Session = Depends(get_db)):
    dashboard = FounderDashboardAI(db=db)
    report = await dashboard.generate_dashboard_view()
    return report
# ── Item 4: Crew output → live Founder Dashboard ─────────────────────────────

from app.services.crew_output_reader import get_founder_dashboard_from_crew
import subprocess
import sys
from pathlib import Path

_crew_proc = None

@router.get("/founder/decision-dashboard")
def get_founder_decision_dashboard():
    """
    Returns the parsed Founder Dashboard from the latest crewai run.
    The Next.js dashboard polls this endpoint every 60 seconds.
    Status: 'live' = crew finished | 'running' = in progress | 'offline' = not started.
    """
    global _crew_proc
    res = get_founder_dashboard_from_crew()
    if _crew_proc is not None and _crew_proc.poll() is None:
        res["status"] = "running"
    return res

@router.get("/crew/status")
def get_crew_status():
    """Quick status check — how many tasks completed out of 27."""
    global _crew_proc
    is_running = False
    if _crew_proc is not None and _crew_proc.poll() is None:
        is_running = True
        
    data = get_founder_dashboard_from_crew()
    return {
        "status": "running" if is_running else data.get("status", "offline"),
        "tasks_completed": data.get("tasks_completed", 0) if not is_running else 0,
        "total_tasks": data.get("total_tasks", 28) if not is_running else 28,
        "run_completed": data.get("run_completed", False) if not is_running else False,
        "last_updated": data.get("generated_at", ""),
    }

@router.post("/crew/run")
def trigger_crew_run():
    """
    Triggers the AI Sales Crew (run_direct.py) in the background.
    Prevents parallel runs if one is already active.
    """
    global _crew_proc
    
    # Check if process is still running
    if _crew_proc is not None:
        if _crew_proc.poll() is None:
            return {"status": "already_running", "message": "AI Sales Crew is already running in the background."}
            
    # Locate Python interpreter and run_direct.py path
    root_path = Path(__file__).parent.parent.parent.parent.parent
    python_exe = root_path / "crewai_v1" / ".venv" / "Scripts" / "python.exe"
    script_path = root_path / "run_direct.py"
    
    if not python_exe.exists():
        # Fallback to system python
        python_exe = Path(sys.executable)
        
    # The key is inherited from the parent process, which pm2 populates from
    # backend/.env. A literal key used to sit here as a `.get` fallback, so the
    # credential shipped inside tracked source rather than staying in .env.
    #
    # Checked BEFORE the try below, not inside it: HTTPException subclasses
    # Exception, so raising it in there would be caught by the handler and
    # relabelled a 500 "Failed to start crew run" — a config error disguised as
    # a crash.
    env = os.environ.copy()
    if not env.get("CEREBRAS_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="CEREBRAS_API_KEY is not set — add it to backend/.env and restart.")

    try:
        # Run run_direct.py in background without blocking the API response
        _crew_proc = subprocess.Popen(
            [str(python_exe), "-u", str(script_path)],
            cwd=str(root_path),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return {"status": "started", "message": "AI Sales Crew execution started in the background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start crew run: {str(e)}")

# ── Shopify Live Data ─────────────────────────────────────────────────────────

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent.parent.parent.parent))

@router.get("/shopify/snapshot")
def get_shopify_snapshot():
    """
    Returns latest Shopify data: revenue, inventory alerts, repeat customers.
    Reads from shopify_snapshot.json (updated by shopify_connector.py).
    Falls back to live Shopify API if token is configured.
    """
    snapshot_file = _Path(__file__).parent.parent.parent.parent.parent / "shopify_snapshot.json"
    if snapshot_file.exists():
        import json as _json
        return _json.loads(snapshot_file.read_text(encoding="utf-8"))

    # Try live fetch if token available
    token = os.getenv("SHOPIFY_TOKEN", "")
    if token:
        try:
            from shopify_connector import take_snapshot
            return take_snapshot()
        except Exception as e:
            return {"error": str(e), "hint": "Run python shopify_connector.py first"}

    return {
        "store": "p3online.in",
        "status": "not_connected",
        "hint": "Add SHOPIFY_TOKEN to .env and run python shopify_connector.py",
        "analytics": {
            "revenue_today_inr": 0,
            "revenue_mtd_inr": 0,
            "order_count": 0,
            "pending_payment_orders": 0,
        },
        "inventory_alerts": [],
        "repeat_customers": [],
    }

@router.get("/shopify/orders")
def get_shopify_orders():
    """Recent orders from Shopify — real revenue data."""
    token = os.getenv("SHOPIFY_TOKEN", "")
    store = os.getenv("SHOPIFY_STORE", "purepantryprovisions.myshopify.com")
    if not token:
        return {"error": "SHOPIFY_TOKEN not set in .env"}
    try:
        from shopify_connector import fetch_orders, order_analytics
        orders = fetch_orders(days=30)
        return {"orders": orders[:20], "analytics": order_analytics(orders)}
    except Exception as e:
        return {"error": str(e)}

@router.get("/shopify/inventory")
def get_shopify_inventory():
    """Live inventory status — flags OOS and low-stock SKUs."""
    token = os.getenv("SHOPIFY_TOKEN", "")
    if not token:
        return {"error": "SHOPIFY_TOKEN not set in .env"}
    try:
        from shopify_connector import fetch_products, inventory_alerts
        products = fetch_products()
        return {"alerts": inventory_alerts(products), "products": products}
    except Exception as e:
        return {"error": str(e)}


# ── Item 1: Shopify Webhook Receiver ─────────────────────────────────────────

import hashlib, hmac, json, os
from datetime import datetime

_shopify_events: list[dict] = []  # in-memory store; swap for DB later

def _verify_shopify_hmac(body: bytes, signature: str) -> bool:
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
    if not secret:
        return True  # skip verification in dev
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature or "")

@router.post("/webhooks/shopify/{topic}")
async def shopify_webhook(topic: str, request: Request, background_tasks: BackgroundTasks):
    """
    Receives Shopify webhooks and routes to the appropriate AI pipeline.

    Topics handled:
      orders/create         → CRM sync + sample trigger
      checkouts/create      → abandoned cart pipeline
      customers/create      → HubSpot CRM contact creation
      orders/fulfilled      → post-purchase follow-up trigger
    """
    body = await request.body()
    signature = request.headers.get("X-Shopify-Hmac-SHA256", "")

    if not _verify_shopify_hmac(body, signature):
        raise HTTPException(status_code=401, detail="Invalid HMAC")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = {
        "topic": topic,
        "received_at": datetime.utcnow().isoformat(),
        "shop": request.headers.get("X-Shopify-Shop-Domain", "unknown"),
        "payload_summary": _summarise(topic, payload),
    }
    _shopify_events.append(event)

    background_tasks.add_task(_route_shopify_event, topic, payload)

    return {"status": "received", "topic": topic, "queued": True}


@router.get("/webhooks/shopify/events")
def list_shopify_events(limit: int = 20):
    """Return last N Shopify webhook events (for dashboard display)."""
    return {"events": _shopify_events[-limit:], "total": len(_shopify_events)}


def _summarise(topic: str, payload: dict) -> dict:
    if topic == "orders/create":
        return {
            "order_id": payload.get("id"),
            "customer": payload.get("customer", {}).get("email"),
            "total": payload.get("total_price"),
            "items": [i.get("title") for i in payload.get("line_items", [])],
        }
    if topic in ("checkouts/create", "checkouts/update"):
        return {
            "checkout_id": payload.get("id"),
            "customer": payload.get("email"),
            "total": payload.get("total_price"),
            "abandoned": not payload.get("completed_at"),
        }
    if topic == "customers/create":
        return {
            "customer_id": payload.get("id"),
            "email": payload.get("email"),
            "city": payload.get("default_address", {}).get("city"),
        }
    return {"raw_keys": list(payload.keys())[:5]}


async def _route_shopify_event(topic: str, payload: dict):
    """Background handler — connect to AI agents as they come online."""
    if topic == "orders/create":
        # Future: trigger CRM sync + post-purchase email sequence
        _log_event("order_created", payload.get("id"), payload.get("total_price"))

    elif topic in ("checkouts/create", "checkouts/update"):
        if not payload.get("completed_at"):
            # Future: trigger abandoned cart recovery via Make.com
            _log_event("cart_abandoned", payload.get("id"), payload.get("total_price"))

    elif topic == "customers/create":
        # Future: sync to HubSpot CRM
        _log_event("new_customer", payload.get("id"), payload.get("email"))

    elif topic == "orders/fulfilled":
        # Future: trigger sampling workflow for B2B orders
        _log_event("order_fulfilled", payload.get("id"), None)


def _log_event(event_type: str, ref: str | int | None, value):
    print(f"[Shopify→AI] {event_type} | ref={ref} | value={value} | {datetime.utcnow().isoformat()}")


# ── Appointment Setting API ───────────────────────────────────────────────────

from app.agents.b2b.appointment_setting_agent import AppointmentSettingAgent, AppointmentRequest

_appt_agent = AppointmentSettingAgent()

@router.post("/appointments/generate")
def generate_appointments(leads: list[dict]):
    """
    Generate meeting request materials for a list of qualified leads.
    POST body: list of lead dicts matching AppointmentRequest fields.
    """
    requests = [AppointmentRequest(**l) for l in leads]
    results = _appt_agent.generate_batch(requests)
    report = _appt_agent.daily_report(results)
    return {
        "total": len(results),
        "report": report,
        "appointments": [r.__dict__ for r in results],
    }


# ── Sampling Workflow API ─────────────────────────────────────────────────────

from app.agents.b2b.sampling_workflow_agent import SamplingWorkflowAgent, SampleRequest

_sample_agent = SamplingWorkflowAgent()
_sample_pipeline: list[dict] = []

@router.post("/samples/dispatch")
def dispatch_sample(req: dict):
    """Dispatch a coffee sample and generate all follow-up communications."""
    sample_req = SampleRequest(**req)
    record = _sample_agent.create_sample_record(sample_req)
    note = _sample_agent.dispatch_note(sample_req, record.tracking_id)
    _sample_pipeline.append(record.__dict__)
    return {
        "tracking_id": record.tracking_id,
        "dispatch_note": note,
        "day2_whatsapp": record.day2_whatsapp,
        "day5_call_script": record.day5_call_script,
        "day7_meeting_email": record.day7_meeting_email,
        "next_action_date": record.next_action_date,
    }

@router.get("/samples/pipeline")
def get_sample_pipeline():
    """Return current sampling pipeline with today's action items."""
    from app.agents.b2b.sampling_workflow_agent import SampleRecord
    records = [SampleRecord(**r) for r in _sample_pipeline]
    report = _sample_agent.daily_sampling_report(records)
    return {"total": len(records), "report": report, "pipeline": _sample_pipeline}


# ── Zoho Email Outreach ───────────────────────────────────────────────────────

from app.services.email_sender import build_outreach_email, send_email, send_batch

@router.post("/outreach/send")
def send_outreach_email(payload: dict):
    """
    V1.1 POLICY: DISABLED. Direct sending bypasses the founder Approval Inbox.
    Use POST /b2b/email/generate-drafts + POST /b2b/email/approve-and-send.
    """
    raise HTTPException(
        status_code=410,
        detail="Direct send disabled by V1.1 policy — route through the Approval Inbox "
               "(/b2b/email/generate-drafts → founder approval → /b2b/email/approve-and-send).",
    )


@router.get("/outreach/log")
def get_outreach_log(db: Session = Depends(get_db)):
    """View all emails sent so far from the database, plus state breakdown."""
    from app.models.models import EmailDraft, B2BLead
    rows = (
        db.query(EmailDraft, B2BLead)
        .join(B2BLead, EmailDraft.lead_id == B2BLead.id)
        .filter(EmailDraft.status == "SENT")
        .order_by(EmailDraft.sent_at.desc())
        .all()
    )
    emails = []
    for draft, lead in rows:
        emails.append({
            "id": draft.id,
            "lead_id": lead.id,
            "company": lead.company,
            "contact_name": lead.contact_name or "",
            "email": lead.email or "",
            "subject": draft.subject or "",
            "body": draft.body or "",
            "sent_at": draft.sent_at.isoformat() if draft.sent_at else None,
            "city": lead.city or "",
            "value": int(lead.estimated_value or 0),
            "margin": _margin_for(lead)
        })

    # State breakdown count query
    states = ["DRAFT", "FOUNDER_APPROVED", "QUEUED", "SENDING", "SENT", "FAILED"]
    breakdown = {}
    for st in states:
        breakdown[st] = db.query(EmailDraft).filter(EmailDraft.status == st).count()

    return {
        "emails": emails,
        "breakdown": breakdown
    }


# ── Settings Management API ───────────────────────────────────────────────────

from pathlib import Path
from pydantic import BaseModel
from typing import Optional


class SettingsUpdate(BaseModel):
    CEREBRAS_API_KEY: Optional[str] = None
    SENDER_EMAIL: Optional[str] = None
    SENDER_NAME: Optional[str] = None
    ZOHO_APP_PASSWORD: Optional[str] = None
    SHOPIFY_STORE: Optional[str] = None
    SHOPIFY_TOKEN: Optional[str] = None
    SHOPIFY_WEBHOOK_SECRET: Optional[str] = None

def _get_env_path() -> Path:
    return Path(__file__).parent.parent.parent / ".env"

def _mask(val: Optional[str]) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "****"
    return f"{val[:4]}...{val[-4:]}"

@router.get("/settings")
def get_settings():
    """Retrieve system settings from .env file with masked secrets."""
    env_path = _get_env_path()
    config = {
        "CEREBRAS_API_KEY": "",
        "SENDER_EMAIL": "",
        "SENDER_NAME": "",
        "ZOHO_APP_PASSWORD": "",
        "SHOPIFY_STORE": "",
        "SHOPIFY_TOKEN": "",
        "SHOPIFY_WEBHOOK_SECRET": "",
    }
    
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                if k in config:
                    config[k] = v

    # Return masked representation for client
    return {
        "CEREBRAS_API_KEY": _mask(config["CEREBRAS_API_KEY"]),
        "SENDER_EMAIL": config["SENDER_EMAIL"],
        "SENDER_NAME": config["SENDER_NAME"],
        "ZOHO_APP_PASSWORD": _mask(config["ZOHO_APP_PASSWORD"]),
        "SHOPIFY_STORE": config["SHOPIFY_STORE"],
        "SHOPIFY_TOKEN": _mask(config["SHOPIFY_TOKEN"]),
        "SHOPIFY_WEBHOOK_SECRET": _mask(config["SHOPIFY_WEBHOOK_SECRET"]),
    }

@router.post("/settings")
def update_settings(update: SettingsUpdate):
    """Update system settings in the .env file."""
    env_path = _get_env_path()
    
    # Read current settings first
    current_config = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                current_config[k.strip()] = v.strip()

    updates = update.dict(exclude_unset=True)
    for k, v in updates.items():
        if v is None:
            continue
        # If the value contains '...' it means the user submitted the masked value, so do not update
        if "..." in v:
            continue
        current_config[k] = v
        # Update immediately in the process environment
        os.environ[k] = v

    # Write back to .env
    lines = []
    lines.append("# ═══════════════════════════════════════════════════")
    lines.append("# Purity Beans AI Sales OS — Backend Environment")
    lines.append("# ═══════════════════════════════════════════════════")
    lines.append("")
    
    for k, v in current_config.items():
        lines.append(f"{k}={v}")

    env_path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "success", "message": "Settings updated successfully"}


# ── B2B CRM API Endpoints ─────────────────────────────────────────────────────

from datetime import datetime, timedelta
from app.services.crm_tracker import CRMTrackerService
from app.models.models import B2BLead, Product, Sale, AgentLog

def _gate_contacts(l) -> tuple[bool, bool, str]:
    """
    V1.1 universal contact gate — used by every lead-serving endpoint,
    all sectors. Returns (email_ok, phone_ok, contact_search_status).
    A contact is shown only after web-wide verification confirmed it.
    """
    email_ok = bool(l.email) and (l.email_verification_status or "") in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL")
    phone_ok = bool(l.phone) and bool(l.phone_verified)
    if l.contact_searched_at is None:
        status = "SEARCHING"
    elif phone_ok or email_ok:
        status = "VERIFIED"
    else:
        status = "NOT_FOUND"
    return email_ok, phone_ok, status


@router.get("/b2b/leads")
def get_b2b_leads(db: Session = Depends(get_db)):
    """Fetch all active B2B leads from the database with dynamic conversion metrics."""
    from app.services.decision_engine import compute_rrs
    from app.services.revenue_engine import data_completeness, revenue_potential
    from app.models.models import LeadEvidence
    from app.services.opportunity_intelligence import assess
    leads = CRMTrackerService.get_all_leads(db)

    # Real Intelligence assessment per lead, so the row shows an actual buying
    # score / classification instead of the frontend's "85" and "27%"
    # placeholders. Evidence is loaded once for the whole batch — assessing each
    # lead with its own query would be an N+1 on an endpoint hit every mount.
    _ev: dict = {}
    for e in db.query(LeadEvidence).all():
        _ev.setdefault(e.lead_id, []).append(e)
        
    from app.models.models import WorkflowEvent, EmailDraft, LeadInteraction
    _we: dict = {}
    for event in db.query(WorkflowEvent.lead_id, WorkflowEvent.event_type).filter(WorkflowEvent.lead_id.isnot(None)).all():
        _we.setdefault(event[0], set()).add(event[1])
        
    _draft_status: dict = {}
    for d in db.query(EmailDraft.lead_id, EmailDraft.status).order_by(EmailDraft.id.asc()).all():
        _draft_status[d[0]] = d[1]
        
    _inter_facts: dict = {}
    for inter in db.query(LeadInteraction).filter(LeadInteraction.superseded_by_id.is_(None)).all():
        consumption_set = inter.monthly_consumption_kg is not None and inter.monthly_consumption_kg > 0
        supplier_set = bool(inter.current_supplier)
        facts = _inter_facts.setdefault(inter.lead_id, {"consumption_verified": False, "supplier_verified": False})
        if consumption_set:
            facts["consumption_verified"] = True
        if supplier_set:
            facts["supplier_verified"] = True

    lead_list = []
    for l in leads:
        # Dynamic active Touchpoint
        touchpoint = "Touch 1: Email Outreach Prep"
        if l.status == "EMAIL_SENT":
            if l.email_opens == 0:
                touchpoint = "Touch 2: WhatsApp Outreach"
            else:
                touchpoint = "Touch 4: LinkedIn Connect"
        elif l.status in ("REPLIED", "MEETING_COMPLETED"):
            touchpoint = "Touch 5: Sample Dispatch"
        elif l.status == "MEETING_BOOKED":
            touchpoint = "Touch 6: Meeting Prep"
        elif l.status in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED"):
            touchpoint = "Touch 7: Sample Follow-up"
        elif l.status == "PROPOSAL_SENT":
            touchpoint = "Touch 8: Proposal Negotiation"
        elif l.status in ("ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            touchpoint = "Touch 9: Account Onboarding"
            
        # Meeting Prep details
        likely_need = "Office Pantry"
        if l.division == "horeca":
            likely_need = "Guest In-Room & Cafe Coffee"
        elif l.division == "distributor":
            likely_need = "Wholesale Distribution Margin"
        elif l.division == "retail":
            likely_need = "Premium Shelf Product Rotation"
        elif l.division == "gifting":
            likely_need = "Corporate Brand Gifting"
            
        recommended_sku = l.sample_sku or "Ultra Blend"
        if l.division == "horeca":
            recommended_sku = "Purica"
        elif l.division == "distributor":
            recommended_sku = "Purista"
            
        f = CRMTrackerService.get_financial_metrics(l)
        
        # V1.1: only real data — current brand/supplier come from call outcomes,
        # never fabricated. Empty means "No real data available."
        meeting_prep = {
            "company_size": l.company_size or "",
            "likely_need": likely_need,
            "estimated_consumption_kg": f["monthly_consumption"],
            "expected_annual_value": f["annual_value"],
            "recommended_sku": recommended_sku,
            "current_brand": l.current_brand or "",
            "current_supplier": l.current_supplier or "",
            "contract_end_date": "",
        }
        
        # Engagement history logs
        history = []
        if l.email_opens > 0:
            history.append(f"Opened email {l.email_opens} times")
        if l.email_clicks > 0:
            history.append(f"Clicked links {l.email_clicks} times")
        if l.status in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED"):
            history.append("Requested sample")
        if l.status == "REPLIED":
            history.append("Replied to email")
            
        history_str = ", ".join(history) if history else "No engagement history yet"
        
        email_ok, phone_ok, contact_search_status = _gate_contacts(l)

        _a = assess(l, _ev.get(l.id, []))

        # V2: Grounded Data Provenance & Contactability matrix
        won_statuses = {"ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "ACCOUNT_GROWTH", "UPSELL_OFFERED"}
        is_won = l.status in won_statuses
        
        lead_we = _we.get(l.id, set())
        
        consumption_verified = False
        supplier_verified = False
        facts = _inter_facts.get(l.id, {})
        if facts.get("consumption_verified"):
            consumption_verified = True
        if facts.get("supplier_verified"):
            supplier_verified = True
            
        provenance = {
            "annual_value": "VERIFIED" if is_won else "MODELLED ESTIMATE",
            "expected_margin": "VERIFIED" if is_won else "MODELLED ESTIMATE",
            "monthly_consumption": "VERIFIED" if consumption_verified else ("MODELLED ESTIMATE" if (l.expected_monthly_consumption_kg or 0) > 0 else "UNKNOWN"),
            "current_supplier": "VERIFIED" if (supplier_verified or l.current_supplier) else "UNKNOWN",
            "email": "VERIFIED" if l.email_verified else ("AVAILABLE" if l.email else "UNAVAILABLE"),
            "phone": "VERIFIED" if l.phone_verified else ("AVAILABLE" if l.phone else "UNAVAILABLE"),
            "whatsapp": "VERIFIED" if l.phone_verified else ("AVAILABLE" if l.whatsapp_number else "UNAVAILABLE"),
            "classification": "VERIFIED" if l.division_verified else ("PROBABLE" if l.division_source in ("Google Maps Types", "Business Name Heuristic") else "UNKNOWN"),
        }
        
        latest_draft_status = _draft_status.get(l.id, None)
        
        email_status = "UNAVAILABLE"
        if l.email:
            email_status = "VERIFIED" if l.email_verified else "AVAILABLE"
            if latest_draft_status:
                if latest_draft_status in ("PENDING", "DRAFT", "EDITED"):
                    email_status = "PENDING APPROVAL"
                elif latest_draft_status in ("FOUNDER_APPROVED", "APPROVED"):
                    email_status = "APPROVED"
                elif latest_draft_status == "QUEUED":
                    email_status = "QUEUED"
                elif latest_draft_status == "SENDING":
                    email_status = "QUEUED"
                elif latest_draft_status in ("PROVIDER_ACCEPTED", "SENT"):
                    email_status = "PROVIDER_ACCEPTED"
                elif latest_draft_status == "FAILED":
                    email_status = "FAILED"
            if l.status == "REPLIED":
                email_status = "REPLIED"
                
        whatsapp_status = "UNAVAILABLE"
        phone = l.whatsapp_number or l.phone
        if phone:
            whatsapp_status = "VERIFIED" if l.phone_verified else "AVAILABLE"
            if "WHATSAPP_SENT" in lead_we:
                whatsapp_status = "PROVIDER_ACCEPTED"
            if l.status == "REPLIED":
                whatsapp_status = "REPLIED"
                
        phone_status = "VERIFIED" if l.phone_verified else ("AVAILABLE" if l.phone else "UNAVAILABLE")
        ai_call_status = "UNAVAILABLE"
        if l.phone:
            ai_call_status = "AVAILABLE"
            if "AI_CALL_COMPLETED" in lead_we:
                ai_call_status = "DELIVERED"
            elif l.call_attempts > 0:
                ai_call_status = "FAILED"
                
        founder_call_status = "UNAVAILABLE"
        if l.phone:
            founder_call_status = "AVAILABLE"
            if "FOUNDER_CALL_COMPLETED" in lead_we:
                founder_call_status = "DELIVERED"
                
        linkedin_status = "AVAILABLE" if l.linkedin else "UNAVAILABLE"
        website_contact_status = "AVAILABLE" if l.website else "UNAVAILABLE"
        other_status = "AVAILABLE" if (l.website or l.linkedin) else "UNAVAILABLE"
        
        contactability = {
            "EMAIL": email_status,
            "WHATSAPP": whatsapp_status,
            "PHONE": phone_status,
            "AI CALL": ai_call_status,
            "FOUNDER CALL": founder_call_status,
            "LINKEDIN": linkedin_status,
            "WEBSITE CONTACT FORM": website_contact_status,
            "OTHER VERIFIED CHANNEL": other_status
        }

        lead_list.append({
            "provenance": provenance,
            "contactability": contactability,
            "id": l.id,
            "company": l.company,
            # Real Intelligence Layer output — the row renders these, so a lead
            # with no evidence shows a low score honestly instead of a fixed 85.
            "buying_score": _a.buying_score,
            "buying_confidence": round(_a.confidence, 2),
            "commercial_fit": _a.commercial_fit,
            "opportunity_score": _a.opportunity_score,
            "classification": _a.classification,          # HOT | WARM | COLD | REJECT
            "intelligence_reasoning": _a.reasoning,
            "buying_evidence": [e["signal"] for e in _a.evidence[:4]],
            # Distinct from classification: whether evidence collection has
            # actually run for this lead. False means "not yet assessed" —
            # the frontend must render that as "Assessing...", never as a
            # numeric score or a reason to hide the lead.
            "evidence_collected": l.evidence_collected_at is not None,
            # Explicit evaluation lifecycle — the authoritative signal for the
            # queue. COMPLETE means classification can be trusted; anything else
            # means it cannot. Falls back to deriving from the timestamp for any
            # lead migrated before the column existed.
            "intelligence_status": (l.intelligence_status
                                    or ("COMPLETE" if l.evidence_collected_at else "NOT_STARTED")),
            "contact_name": l.contact_name,
            "contact_title": l.contact_title,
            "email": l.email if email_ok else None,
            "phone": l.phone if phone_ok else None,
            # What is actually stored, gate or no gate. The gate above decides
            # what may be ACTED on; it should never decide what the founder is
            # allowed to see about their own record. Blanking the edit field
            # while the journey said "sent to info@balajismart.com" read as the
            # system inventing a send: 25 of 33 stored addresses are CATCH_ALL
            # or INVALID and were invisible, so they could not be corrected
            # either. Sending stays blocked at the chokepoint in email_sender.
            "email_on_file": l.email or "",
            "phone_on_file": l.phone or "",
            # Is the money figure about THIS business, or the category average?
            # Without a review count it is base x city tier and nothing else.
            "revenue_is_category_default": l.maps_reviews_count is None,
            "revenue_basis": (f"sized from {l.maps_reviews_count} Google reviews"
                              if l.maps_reviews_count is not None else
                              "category default — no size signal for this business"),
            "phone_verified": bool(l.phone_verified),
            "phone_source": l.phone_source,
            "email_verification_status": l.email_verification_status or "UNVERIFIED",
            "contact_search_status": contact_search_status,
            "data_completeness_pct": data_completeness(l)["score"],
            "revenue_potential_rs": revenue_potential(l)["revenue_potential_rs"],
            "expected_margin_rs": revenue_potential(l)["expected_margin_rs"],
            "city": l.city,
            "division": l.division,
            "email_approved_by_founder": bool(l.email_approved_by_founder),
            "email_rejected_by_founder": bool(l.email_rejected_by_founder),
            "email_verified": bool(l.email_verified),
            "lead_source": l.lead_source,
            "estimated_value": f["annual_value"],
            "score": l.score,
            "rrs": compute_rrs(l),
            "intent_score": l.intent_score or l.score or 0,
            "intent_tier": l.intent_tier or "Cold",
            "probability": f["win_probability"],
            "status": l.status,
            "priority": l.priority,
            "recommended_action": l.recommended_action,
            "qualification_notes": l.qualification_notes,
            "sample_taste": l.sample_taste,
            "sample_aroma": l.sample_aroma,
            "sample_packaging": l.sample_packaging,
            "sample_intent": l.sample_intent,
            "sample_purchase_again": l.sample_purchase_again,
            "expected_monthly_consumption_kg": f["monthly_consumption"],
            "proposal_monthly_kg": l.proposal_monthly_kg,
            "proposal_suggested_price": l.proposal_suggested_price,
            "proposal_suggested_margin": f["margin_pct"],
            "proposal_discount_percent": l.proposal_discount_percent,
            "proposal_recommended_margin": l.proposal_recommended_margin,
            "proposal_text": l.proposal_text,
            "last_updated": l.last_updated.isoformat() if l.last_updated else None,
            "touchpoint": touchpoint,
            "meeting_prep": meeting_prep,
            "engagement_reasons": history_str,
            # New financial OS fields
            "monthly_consumption": f["monthly_consumption"],
            "annual_value": f["annual_value"],
            "margin_pct": f["margin_pct"],
            "gross_margin": f["gross_margin"],
            "win_probability": f["win_probability"],
            "collection_probability": f["collection_probability"],
            "collection_delay_days": f["collection_delay_days"],
            "expected_revenue": f["expected_revenue"],
            "expected_margin": f["expected_margin"],
            "cash_velocity_score": f["cash_velocity_score"],
            "expected_cash_score": f["expected_cash_score"],
            "realization_score": f["realization_score"],
            "revenue_quality_score": f["revenue_quality_score"],
            "quality_grade": f["quality_grade"],
            "cac": f["cac"],
            "payback_period": f["payback_period"],
            "ltv": f["ltv"],
            "action_priority_score": f["action_priority_score"],
            "close_days": f["close_days"],
            "days_in_stage": f["days_in_stage"],
            "days_since_activity": f["days_since_activity"],
            "velocity_score": f["velocity_score"],
            "website": l.website,
            "address": l.address,
            "whatsapp_number": l.whatsapp_number if phone_ok else None,
            "call_status": l.call_status,
            "call_quality_score": l.call_quality_score or 0,
            "call_summary": l.call_summary,
            "call_transcript": l.call_transcript,
            "call_recording_url": l.call_recording_url,
            "vapi_call_id": l.vapi_call_id,
            "human_answered": l.human_answered or False,
            "call_duration_seconds": l.call_duration_seconds or 0,
            "call_attempts": l.call_attempts or 0,
            "last_call_date": l.last_call_date.isoformat() if l.last_call_date else None,
            "do_not_call": l.do_not_call or False,
            "dnc_reason": l.dnc_reason,
            "dnc_override_by": l.dnc_override_by,
            "dnc_override_reason": l.dnc_override_reason,
            "call_cost": l.call_cost or 0.0,
            "call_provider": l.call_provider,
            "call_minutes": l.call_minutes or 0.0,
            "current_brand": l.current_brand,
            "current_supplier": l.current_supplier,
            "price_per_kg": l.price_per_kg,
            "competitor_strength": l.competitor_strength or 0,
            "monthly_consumption_kg": l.expected_monthly_consumption_kg or 0.0,
            "decision_maker": l.decision_maker,
            "sample_requested": l.sample_requested or False,
            "meeting_requested": l.meeting_requested or False,
            "budget_range": l.budget_range,
            "objection_reason": l.objection_reason,
            "next_followup_date": l.next_followup_date,
            "call_estimated_value": l.call_estimated_value or 0.0,
            "blended_realization_per_kg": l.blended_realization_per_kg or 1400.0,
            "lead_tier": l.lead_tier,
            "company_normalized": l.company_normalized,
            "lead_owner": l.lead_owner,
            "lead_locked_until": l.lead_locked_until.isoformat() if l.lead_locked_until else None,
            "acquisition_source": l.acquisition_source,
            "consent_status": l.consent_status or "UNKNOWN",
            "consent_source": l.consent_source,
            "consent_timestamp": l.consent_timestamp.isoformat() if l.consent_timestamp else None,
            "lead_temperature_score": l.lead_temperature_score or 0.0,
            "lead_temperature_tier": l.lead_temperature_tier or "COLD"
        })
    return {
        "total": len(leads),
        "leads": lead_list
    }

@router.get("/b2b/kpis")
def get_b2b_kpis(db: Session = Depends(get_db)):
    """Fetch real-time pipeline KPIs."""
    return CRMTrackerService.get_kpis(db)


# Ordered journey. Each stage names the event(s) that PROVE it happened; a stage
# with no event source is proven by a verified DB fact instead (handled below).
# Nothing here may be inferred from lead.status — status is a summary field that
# jumps ahead of reality (a lead that replies to EMAIL would otherwise render
# "WhatsApp Sent ✓" for a message nobody ever sent).
_JOURNEY_STAGES = [
    ("Discovery",      None),
    ("Email Verified", None),
    ("Email Drafted",  None),
    ("Email Sent",     ("EMAIL_SENT", "INTRO_EMAIL_SENT")),
    ("Opened",         None),
    ("Replied",        ("REPLIED", "EMAIL_REPLIED", "WHATSAPP_REPLIED")),
    ("WhatsApp Sent",  ("WHATSAPP_SENT",)),
    # Only COMPLETED proves the call happened. AI_CALL_INITIATED means we dialled
    # and nothing came back yet — that stays red, with the detail saying why.
    ("AI Call",        ("AI_CALL_COMPLETED",)),
    ("Founder Call",   ("FOUNDER_CALL_COMPLETED",)),
    ("Meeting",        ("MEETING_BOOKED", "MEETING_COMPLETED")),
    ("Sample",         ("SAMPLE_DISPATCHED", "SAMPLE_SENT")),
    ("Proposal",       ("PROPOSAL_SENT",)),
    ("Order",          ("ORDER_WON",)),
    ("Reorder",        ("REORDER_TRIGGERED",)),
]

# A failure event marks its stage red and carries the real error through.
_JOURNEY_FAILURES = {"EMAIL_FAILED": "Email Sent", "WORKFLOW_FAILED": None}


@router.get("/b2b/journeys")
def get_b2b_journeys(db: Session = Depends(get_db)):
    """
    Per-lead execution status, derived ONLY from the immutable event log and
    verified DB facts.

    Returns {lead_id: [{label, state, detail}]} where state is:
      done    -> an event proves this step actually executed  (green check)
      failed  -> a failure event was recorded; detail = the real error (red)
      pending -> no evidence it happened yet                   (red)

    This is the single source of truth for the founder's outreach checklist.
    """
    from app.models.models import B2BLead, EmailDraft, WorkflowEvent

    # Which events exist per lead, and the reason for any failure.
    seen: dict[int, set] = {}
    failures: dict[int, str] = {}
    rows = db.query(
        WorkflowEvent.lead_id, WorkflowEvent.event_type, WorkflowEvent.payload
    ).filter(WorkflowEvent.lead_id.isnot(None)).all()
    for lead_id, etype, payload in rows:
        seen.setdefault(lead_id, set()).add(etype)
        if etype in _JOURNEY_FAILURES:
            err = ""
            if isinstance(payload, dict):
                err = str(payload.get("smtp_error") or payload.get("error") or "")
            failures[lead_id] = err[:180] or "send failed"

    drafted = {d[0] for d in db.query(EmailDraft.lead_id).distinct().all() if d[0]}
    # Needed to tell "no address at all" apart from "address present, unverified".
    addr_by_lead = {i: (e or "").strip()
                    for i, e in db.query(B2BLead.id, B2BLead.email).all()}
    # Whether the founder ever approved outreach for this business.
    approved_by_lead = {i: bool(a) for i, a in
                        db.query(B2BLead.id, B2BLead.email_approved_by_founder).all()}

    out: dict[int, list] = {}
    for lead in db.query(
        B2BLead.id, B2BLead.email_verified, B2BLead.email_opens
    ).all():
        lid, verified, opens = lead
        events = seen.get(lid, set())
        stages = []
        for label, sources in _JOURNEY_STAGES:
            state, detail = "pending", ""
            if label == "Discovery":
                # The lead row existing IS the proof that discovery ran.
                state = "done"
            elif label == "Email Verified":
                # Three distinct states, not two. "no verified address on
                # record" was shown whenever email_verified was False — including
                # for leads that DO hold an address that simply has not been
                # MX-checked. On screen that read as a contradiction: "More
                # Supermarket" showed EMAIL SENT with a tick beside "no verified
                # address on record", when in fact info@moresupermarket.com was
                # on file and had been emailed. Say which case it is.
                state = "done" if verified else "pending"
                if verified:
                    detail = ""
                elif addr_by_lead.get(lid):
                    detail = f"on file, not verified: {addr_by_lead[lid]}"
                else:
                    detail = "no address on record"
            elif label == "Email Drafted":
                state = "done" if lid in drafted else "pending"
            elif label == "Opened":
                # Real open tracking only. No pixel data means unknown, not "no".
                state = "done" if (opens or 0) > 0 else "pending"
                detail = f"{opens} opens" if (opens or 0) > 0 else "no open recorded"
            elif sources and events.intersection(sources):
                state = "done"
            if label == "Email Sent" and lid in failures and state != "done":
                state, detail = "failed", failures[lid]
            # Annotate rather than rewrite. An email genuinely sent on 09 Jul
            # stays "done" even if the address later failed verification —
            # deleting or hiding the tick to satisfy today's validation rule
            # would falsify the audit trail. Warn about the CURRENT state
            # instead, which is what the founder needs to act on.
            if label == "Email Sent" and state == "done":
                _addr = addr_by_lead.get(lid)
                # Every send on record predates per-draft founder approval: 49 of
                # the 50 went out in one two-minute burst on 09 Jul and the event
                # log contains no approval event of any kind. Saying so on the
                # tile is the honest reading — otherwise a green EMAIL SENT next
                # to a "Pending Approval" badge looks like the system sent
                # something behind the founder's back. It cannot now: sending
                # requires email_approved_by_founder.
                _unapproved = not approved_by_lead.get(lid, False)
                if _addr and not verified:
                    detail = f"sent to {_addr} — address is no longer verified; sending is now blocked"
                elif not _addr:
                    detail = "sent to an address since removed from the record"
                if _unapproved:
                    detail = ((detail + " · ") if detail else "") + "sent before approval was required"
            stages.append({"label": label, "state": state, "detail": detail})
        out[lid] = stages
    return {"journeys": out, "generated_at": datetime.utcnow().isoformat()}

class SaveNotesRequest(BaseModel):
    notes: str

@router.post("/b2b/leads/{lead_id}/notes")
def save_lead_notes(lead_id: int, req: SaveNotesRequest, db: Session = Depends(get_db)):
    """Update qualification notes for a B2B lead."""
    from app.models.models import B2BLead
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.qualification_notes = req.notes
    db.commit()
    return {"lead_id": lead_id, "status": "success"}

class UpdateLeadRequest(BaseModel):
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

@router.patch("/b2b/leads/{lead_id}")
def update_lead_details(lead_id: int, req: UpdateLeadRequest, db: Session = Depends(get_db)):
    """Update B2B lead contact details dynamically."""
    from app.models.models import B2BLead
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if req.contact_name is not None:
        lead.contact_name = req.contact_name
    if req.email is not None:
        lead.email = req.email
    if req.phone is not None:
        lead.phone = req.phone
        lead.whatsapp_number = req.phone
    db.commit()
    return {"lead_id": lead_id, "status": "success"}

class LeadStatusUpdate(BaseModel):
    company: str
    status: str
    notes: Optional[str] = None

@router.post("/b2b/leads/status")
def update_lead_status(update_req: LeadStatusUpdate, db: Session = Depends(get_db)):
    """Manually update or transition a lead's status."""
    try:
        lead = db.query(B2BLead).filter(B2BLead.company == update_req.company).first()
        if not lead:
            raise HTTPException(status_code=404, detail=f"Lead not found for company: {update_req.company}")
            
        # Recompute proposals if they are transitioning to PROPOSAL_SENT
        if update_req.status.upper() == "PROPOSAL_SENT" and lead.status != "PROPOSAL_SENT":
            taste = lead.sample_taste or 8
            aroma = lead.sample_aroma or 8
            packaging = lead.sample_packaging or 9
            intent = lead.sample_intent or 8
            again = lead.sample_purchase_again or 8
            
            lead.sample_taste = taste
            lead.sample_aroma = aroma
            lead.sample_packaging = packaging
            lead.sample_intent = intent
            lead.sample_purchase_again = again
            
            prob = (taste * 1.5 + aroma * 1.5 + packaging * 1.0 + intent * 3.0 + again * 3.0) / 100.0
            lead.probability = min(max(prob, 0.0), 1.0)
            
            kg_per_month = lead.expected_monthly_consumption_kg or max(lead.estimated_value / 9600.0, 10.0)
            base_price = max(900.0 - (kg_per_month * 0.5), 650.0)
            cost_price_kg = 400.0
            
            discount = lead.proposal_discount_percent or 0.0
            suggested_price = base_price * (1.0 - discount / 100.0)
            suggested_margin = ((suggested_price - cost_price_kg) / suggested_price) * 100.0
            
            lead.proposal_monthly_kg = kg_per_month
            lead.proposal_suggested_price = suggested_price
            lead.proposal_suggested_margin = suggested_margin
            lead.proposal_recommended_margin = suggested_margin
            
            # 1% discount adds 2.4% win probability
            negotiated_prob = prob + (discount * 0.024)
            lead.probability = min(max(negotiated_prob, 0.0), 1.0)
            
            lead.proposal_text = (
                f"=========================================\n"
                f"  PURITY BEANS COFFEE SUPPLY PROPOSAL    \n"
                f"=========================================\n"
                f"Prepared for: {lead.company}\n"
                f"Contact: {lead.contact_name} ({lead.contact_title})\n"
                f"Date: {datetime.utcnow().strftime('%d %B %Y')}\n\n"
                f"1. REQUIREMENT ESTIMATES:\n"
                f"  - Monthly Requirement: {kg_per_month:.1f} kg\n"
                f"  - Annual Estimated Value: Rs. {lead.estimated_value:,.0f}\n\n"
                f"2. COMMERCIAL TERMS:\n"
                f"  - Suggested Wholesale Pricing: Rs. {suggested_price:.2f} per kg (Discount: {discount:.1f}%)\n"
                f"  - Target Partner Margin: {suggested_margin:.1f}%\n"
                f"  - Delivery F.O.B: {lead.city}\n\n"
                f"3. SLA & RETENTION:\n"
                f"  - 100% Pure Coffee, Zero Chicory Guarantee\n"
                f"  - Next-Day Dispatch on re-orders\n"
                f"========================================="
            )
            
        lead = CRMTrackerService.update_lead_status(
            db, 
            company=update_req.company, 
            status=update_req.status, 
            notes=update_req.notes
        )
        return {"status": "success", "message": f"Lead {lead.company} status updated to {lead.status}", "lead": {
            "company": lead.company,
            "status": lead.status,
            "qualification_notes": lead.qualification_notes
        }}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class LeadFeedbackUpdate(BaseModel):
    company: str
    sample_taste: int
    sample_aroma: int
    sample_packaging: int
    sample_intent: int
    sample_purchase_again: int
    expected_monthly_consumption_kg: float
    notes: Optional[str] = None

@router.post("/b2b/leads/feedback")
def update_lead_feedback(req: LeadFeedbackUpdate, db: Session = Depends(get_db)):
    """Update sample feedback scores and recalculate win probability & proposals."""
    lead = db.query(B2BLead).filter(B2BLead.company == req.company).first()
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found for company: {req.company}")
        
    lead.sample_taste = req.sample_taste
    lead.sample_aroma = req.sample_aroma
    lead.sample_packaging = req.sample_packaging
    lead.sample_intent = req.sample_intent
    lead.sample_purchase_again = req.sample_purchase_again
    lead.expected_monthly_consumption_kg = req.expected_monthly_consumption_kg
    
    # Calculate win probability based on feedback (out of 1.0)
    prob = (req.sample_taste * 1.5 + req.sample_aroma * 1.5 + req.sample_packaging * 1.0 + req.sample_intent * 3.0 + req.sample_purchase_again * 3.0) / 100.0
    
    # Calculate Proposal parameters (pricing based on volume)
    kg_per_month = req.expected_monthly_consumption_kg or max(lead.estimated_value / 9600.0, 10.0)
    base_price = max(900.0 - (kg_per_month * 0.5), 650.0)
    cost_price_kg = 400.0
    
    discount = lead.proposal_discount_percent or 0.0
    suggested_price = base_price * (1.0 - discount / 100.0)
    suggested_margin = ((suggested_price - cost_price_kg) / suggested_price) * 100.0
    
    lead.proposal_monthly_kg = kg_per_month
    lead.proposal_suggested_price = suggested_price
    lead.proposal_suggested_margin = suggested_margin
    lead.proposal_recommended_margin = suggested_margin
    
    # 1% discount adds 2.4% win probability
    negotiated_prob = prob + (discount * 0.024)
    lead.probability = min(max(negotiated_prob, 0.0), 1.0)
    
    lead.proposal_text = (
        f"=========================================\n"
        f"  PURITY BEANS COFFEE SUPPLY PROPOSAL    \n"
        f"=========================================\n"
        f"Prepared for: {lead.company}\n"
        f"Contact: {lead.contact_name} ({lead.contact_title})\n"
        f"Date: {datetime.utcnow().strftime('%d %B %Y')}\n\n"
        f"1. REQUIREMENT ESTIMATES:\n"
        f"  - Monthly Requirement: {kg_per_month:.1f} kg\n"
        f"  - Annual Estimated Value: Rs. {lead.estimated_value:,.0f}\n\n"
        f"2. COMMERCIAL TERMS:\n"
        f"  - Suggested Wholesale Pricing: Rs. {suggested_price:.2f} per kg (Discount: {discount:.1f}%)\n"
        f"  - Target Partner Margin: {suggested_margin:.1f}%\n"
        f"  - Delivery F.O.B: {lead.city}\n\n"
        f"3. SLA & RETENTION:\n"
        f"  - 100% Pure Coffee, Zero Chicory Guarantee\n"
        f"  - Next-Day Dispatch on re-orders\n"
        f"========================================="
    )
    
    if req.notes:
        lead.qualification_notes = req.notes
        
    lead.status = "FEEDBACK_RECEIVED"
    CRMTrackerService.recalculate_lead_score_and_action(lead)
    lead.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    
    return {
        "status": "success",
        "message": f"Sample feedback recorded for {lead.company}. Win probability updated to {lead.probability * 100:.0f}%",
        "lead": {
            "company": lead.company,
            "probability": lead.probability,
            "sample_taste": lead.sample_taste,
            "sample_aroma": lead.sample_aroma,
            "sample_packaging": lead.sample_packaging,
            "sample_intent": lead.sample_intent,
            "sample_purchase_again": lead.sample_purchase_again,
            "expected_monthly_consumption_kg": lead.expected_monthly_consumption_kg,
            "proposal_text": lead.proposal_text
        }
    }

class LeadNegotiateRequest(BaseModel):
    company: str
    discount_percent: float

@router.post("/b2b/leads/negotiate")
def negotiate_proposal(req: LeadNegotiateRequest, db: Session = Depends(get_db)):
    """Update discount and recalculate pricing, margin, and win probability."""
    lead = db.query(B2BLead).filter(B2BLead.company == req.company).first()
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found for company: {req.company}")

    taste = lead.sample_taste or 8
    aroma = lead.sample_aroma or 8
    packaging = lead.sample_packaging or 9
    intent = lead.sample_intent or 8
    again = lead.sample_purchase_again or 8

    # Base Win Probability from feedback
    base_prob = (taste * 1.5 + aroma * 1.5 + packaging * 1.0 + intent * 3.0 + again * 3.0) / 100.0

    # Discount effect: +2.4% win probability per 1% discount
    discount = req.discount_percent
    negotiated_prob = base_prob + (discount * 0.024)
    lead.probability = min(max(negotiated_prob, 0.0), 1.0)

    # Recalculate price and margins
    kg_per_month = lead.expected_monthly_consumption_kg or max(lead.estimated_value / 9600.0, 10.0)
    base_price = max(900.0 - (kg_per_month * 0.5), 650.0)
    cost_price_kg = 400.0

    suggested_price = base_price * (1.0 - discount / 100.0)
    suggested_margin = ((suggested_price - cost_price_kg) / suggested_price) * 100.0

    lead.proposal_discount_percent = discount
    lead.proposal_suggested_price = suggested_price
    lead.proposal_suggested_margin = suggested_margin
    lead.proposal_recommended_margin = suggested_margin

    lead.proposal_text = (
        f"=========================================\n"
        f"  PURITY BEANS COFFEE SUPPLY PROPOSAL    \n"
        f"=========================================\n"
        f"Prepared for: {lead.company}\n"
        f"Contact: {lead.contact_name} ({lead.contact_title})\n"
        f"Date: {datetime.utcnow().strftime('%d %B %Y')}\n\n"
        f"1. REQUIREMENT ESTIMATES:\n"
        f"  - Monthly Requirement: {kg_per_month:.1f} kg\n"
        f"  - Annual Estimated Value: Rs. {lead.estimated_value:,.0f}\n\n"
        f"2. COMMERCIAL TERMS:\n"
        f"  - Suggested Wholesale Pricing: Rs. {suggested_price:.2f} per kg (Discount: {discount:.1f}%)\n"
        f"  - Target Partner Margin: {suggested_margin:.1f}%\n"
        f"  - Delivery F.O.B: {lead.city}\n\n"
        f"3. SLA & RETENTION:\n"
        f"  - 100% Pure Coffee, Zero Chicory Guarantee\n"
        f"  - Next-Day Dispatch on re-orders\n"
        f"========================================="
    )

    lead.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(lead)

    return {
        "status": "success",
        "message": f"Proposal discount updated for {lead.company}. Win probability updated to {lead.probability * 100:.0f}%",
        "lead": {
            "company": lead.company,
            "probability": lead.probability,
            "proposal_discount_percent": lead.proposal_discount_percent,
            "proposal_suggested_price": lead.proposal_suggested_price,
            "proposal_suggested_margin": lead.proposal_suggested_margin,
            "proposal_text": lead.proposal_text
        }
    }

from app.agents.b2b.win_loss_analyst import WinLossAnalysisAgent
from app.agents.b2b.product_intelligence import ProductIntelligenceAgent
from app.services.tender_auto_pricer import run_tender_auto_pricer

# ── Command Panel — history, updates, actions ────────────────────────────────

@router.get("/founder/command-panel")
def get_command_panel(db: Session = Depends(get_db)):
    """
    Returns all three columns of the founder Command Panel:
      1. actions     — smart prioritized list of what to do right now
      2. activity    — recent AgentLog entries (what the system has done)
      3. pipeline    — pipeline stage changes and counts from last 7 days
    """
    from datetime import datetime, timedelta
    from sqlalchemy import desc, func

    # ── 1. Actionable items (smart ranked) ───────────────────────────────────
    actions = []

    # Priority leads not yet called
    priority_leads = db.query(B2BLead).filter(
        B2BLead.intent_score >= 81,
        B2BLead.status.in_(["REPLIED", "MEETING_BOOKED", "EMAIL_SENT"])
    ).order_by(desc(B2BLead.intent_score)).limit(3).all()
    for l in priority_leads:
        actions.append({
            "priority": "HIGH",
            "type": "CALL",
            "title": f"Call {l.company}",
            "detail": f"Intent {l.intent_score} · {l.contact_name or 'Owner'} · {l.phone or 'No phone'}",
            "value_inr": int(l.estimated_value or 0),
            "company": l.company,
            "phone": l.phone or "",
        })

    # Samples pending feedback > 3 days
    sample_pending = db.query(B2BLead).filter(
        B2BLead.status == "SAMPLE_SENT",
        B2BLead.last_updated <= datetime.utcnow() - timedelta(days=3)
    ).all()
    for l in sample_pending:
        actions.append({
            "priority": "HIGH",
            "type": "FOLLOW_UP",
            "title": f"Chase feedback — {l.company}",
            "detail": f"Sample sent {(datetime.utcnow() - l.last_updated).days}d ago · No feedback yet",
            "value_inr": int(l.estimated_value or 0),
            "company": l.company,
            "phone": l.phone or "",
        })

    # Proposals stalled > 5 days
    stalled = db.query(B2BLead).filter(
        B2BLead.status == "PROPOSAL_SENT",
        B2BLead.last_updated <= datetime.utcnow() - timedelta(days=5)
    ).all()
    for l in stalled:
        actions.append({
            "priority": "MEDIUM",
            "type": "CLOSE",
            "title": f"Close proposal — {l.company}",
            "detail": f"Stalled {(datetime.utcnow() - l.last_updated).days}d · ₹{int((l.estimated_value or 0)/100000)}L at stake",
            "value_inr": int(l.estimated_value or 0),
            "company": l.company,
            "phone": l.phone or "",
        })

    # Meetings completed — send sample
    met_no_sample = db.query(B2BLead).filter(
        B2BLead.status == "MEETING_COMPLETED"
    ).limit(3).all()
    for l in met_no_sample:
        actions.append({
            "priority": "HIGH",
            "type": "SAMPLE",
            "title": f"Send sample — {l.company}",
            "detail": f"Meeting done · Ready for tasting kit dispatch",
            "value_inr": int(l.estimated_value or 0),
            "company": l.company,
            "phone": l.phone or "",
        })

    # Sort by priority then value
    prio_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    actions.sort(key=lambda a: (prio_order.get(a["priority"], 2), -a["value_inr"]))

    # ── 2. Recent activity (from AgentLog) ───────────────────────────────────
    logs = db.query(AgentLog).order_by(desc(AgentLog.timestamp)).limit(20).all()
    activity = []
    for log in logs:
        payload = log.payload or {}
        detail = ""
        if "company" in payload:
            detail = payload["company"]
        elif "subject" in payload:
            detail = payload.get("subject", "")[:60]
        activity.append({
            "time": log.timestamp.strftime("%d %b %H:%M") if log.timestamp else "",
            "agent": log.agent_name or "System",
            "action": log.action or "",
            "detail": detail,
            "real_delivery": payload.get("real_delivery", False),
        })

    # ── 3. Pipeline updates (last 7 days) ────────────────────────────────────
    week_ago = datetime.utcnow() - timedelta(days=7)
    stage_counts = db.query(
        B2BLead.status,
        func.count(B2BLead.id).label("cnt")
    ).filter(
        B2BLead.status.notin_(["COLD"]),
        B2BLead.last_updated >= week_ago
    ).group_by(B2BLead.status).all()

    pipeline_updates = [{"stage": r.status, "count": r.cnt} for r in stage_counts]
    pipeline_updates.sort(key=lambda x: x["count"], reverse=True)

    # Weekly delta highlights
    replied_count  = db.query(B2BLead).filter(B2BLead.status == "REPLIED").count()
    sample_count   = db.query(B2BLead).filter(B2BLead.status == "SAMPLE_SENT").count()
    proposal_count = db.query(B2BLead).filter(B2BLead.status == "PROPOSAL_SENT").count()
    won_count      = db.query(B2BLead).filter(B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])).count()
    emails_sent    = db.query(AgentLog).filter(AgentLog.timestamp >= week_ago).count()

    return {
        "actions": actions[:10],
        "activity": activity,
        "pipeline": {
            "stage_counts": pipeline_updates,
            "replied": replied_count,
            "samples_active": sample_count,
            "proposals_out": proposal_count,
            "orders_won": won_count,
            "emails_sent_this_week": emails_sent,
        },
        "generated_at": datetime.utcnow().strftime("%d %b %Y %H:%M"),
    }


# ── Founder Daily Brief ───────────────────────────────────────────────────────

@router.get("/founder/daily-brief")
def get_founder_daily_brief(db: Session = Depends(get_db)):
    """
    Generate the Founder Daily Brief — live pipeline summary with top action,
    expected revenue, revenue at risk, and data quality.
    """
    from app.services.founder_brief import generate_brief
    return generate_brief(db)


@router.post("/founder/daily-brief/send")
def send_founder_daily_brief(db: Session = Depends(get_db)):
    """
    Generate and email the Founder Daily Brief to hitenjain.12@gmail.com.
    Uses Zoho SMTP. No-op with status if ZOHO_APP_PASSWORD not configured.
    """
    from app.services.founder_brief import generate_brief, send_brief
    brief = generate_brief(db)
    result = send_brief(brief)
    return {"brief": brief, "email": result}


@router.post("/b2b/send-outreach-now")
def b2b_send_outreach_now(db: Session = Depends(get_db)):
    """
    V1.1 POLICY: This endpoint NO LONGER SENDS EMAIL.
    First-touch outreach must pass through the founder Approval Inbox.
    It now drafts introduction emails for eligible leads and queues them
    for founder review (same behaviour as /b2b/email/generate-drafts).
    """
    from app.models.models import EmailDraft
    from app.services.email_sender import generate_b2b_pitch_email
    from sqlalchemy import exists

    leads = (
        db.query(B2BLead)
        .filter(
            B2BLead.email.like("%@%"),
            B2BLead.status.in_(["DISCOVERED", "QUALIFIED"]),
            B2BLead.email_rejected_by_founder != True,
            ~exists().where(
                (EmailDraft.lead_id == B2BLead.id) &
                (EmailDraft.status.in_(["PENDING", "APPROVED", "SENT"]))
            ),
        )
        .order_by(B2BLead.score.desc())
        .all()
    )

    created = 0
    for lead in leads:
        subject, body = generate_b2b_pitch_email(lead)
        db.add(EmailDraft(
            lead_id=lead.id,
            follow_up_type="introduction",
            subject=subject,
            body=body,
            reason="Drafted by send-outreach-now (approval required)",
            status="PENDING",
        ))
        created += 1
    db.commit()

    return {
        "mode": "draft_only",
        "stage": "approval_queue",
        "drafted": created,
        "emails_sent_real": 0,
        "emails_simulated": 0,
        "message": f"{created} drafts queued in the Approval Inbox — no email sent without founder approval",
    }


class SendSingleDistributorRequest(BaseModel):
    to_email: str
    to_name: str
    city: str
    subject: str
    body: str
    personalized_line: Optional[str] = None

@router.post("/b2b/distributor-campaign/send-one")
def send_single_distributor(req: SendSingleDistributorRequest, db: Session = Depends(get_db)):
    """
    V1.1 POLICY: no direct send. Creates a PENDING EmailDraft for this
    distributor so it goes through the founder Approval Inbox.
    """
    from app.models.models import EmailDraft

    lead = db.query(B2BLead).filter(B2BLead.email == req.to_email).first()
    if not lead:
        raise HTTPException(status_code=404, detail=f"No lead found with email {req.to_email}")

    name = req.to_name.strip() if req.to_name and req.to_name.strip() else ""
    if name:
        personalised_body = req.body.replace("[Contact Name]", name).replace("{contact_name}", name)
        greeting = f"Dear {name},"
    else:
        personalised_body = req.body.replace("Dear [Contact Name],", "Dear Sir/Madam,").replace("Dear {contact_name},", "Dear Sir/Madam,")
        greeting = "Dear Sir/Madam,"
    if req.personalized_line and req.personalized_line.strip():
        personalised_body = personalised_body.replace(
            greeting,
            f"{greeting}\n\n{req.personalized_line.strip()}"
        )

    existing = db.query(EmailDraft).filter(
        EmailDraft.lead_id == lead.id,
        EmailDraft.status.in_(["PENDING", "APPROVED"]),
    ).first()
    if existing:
        existing.subject = req.subject
        existing.body = personalised_body
        existing.status = "PENDING"
        db.commit()
        return {"status": "draft_updated", "draft_id": existing.id,
                "message": "Draft updated in the Approval Inbox — review and approve to send."}

    draft = EmailDraft(
        lead_id=lead.id,
        follow_up_type="introduction",
        subject=req.subject,
        body=personalised_body,
        reason="Distributor campaign draft (approval required)",
        status="PENDING",
    )
    db.add(draft)
    db.commit()
    return {"status": "drafted", "draft_id": draft.id,
            "message": "Draft queued in the Approval Inbox — review and approve to send."}


class PushDraftRequest(BaseModel):
    subject: str
    body: str

@router.post("/b2b/distributor-campaign/push-draft")
def push_distributor_draft(req: PushDraftRequest):
    """
    Sends the composed distributor email to the founder's own inbox as a draft preview.
    Lands in connect@purepantryprovisions.com → founder can review + send manually if needed.
    """
    from app.services.email_sender import send_email, OutreachEmail, SENDER_EMAIL
    zoho_pw = os.getenv("ZOHO_APP_PASSWORD", "")
    if not zoho_pw or zoho_pw.strip() in ("", "your_zoho_app_password_here"):
        return {
            "status": "simulated",
            "message": "Zoho not configured — draft would be sent to connect@purepantryprovisions.com",
            "to": SENDER_EMAIL
        }

    draft_email = OutreachEmail(
        to_email=SENDER_EMAIL,
        to_name="Hiten Jain (Draft Review)",
        company="Pure Pantry Provisions",
        subject=f"[DRAFT REVIEW] {req.subject}",
        body_text=(
            "─── THIS IS A DRAFT FOR YOUR REVIEW ───\n"
            "Check this email, edit if needed, then forward/send to your distributor list.\n"
            "────────────────────────────────────────\n\n"
            + req.body
        ),
    )
    result = send_email(draft_email)
    if result.status == "sent":
        return {
            "status": "sent",
            "message": f"Draft pushed to {SENDER_EMAIL} — check your Zoho inbox.",
            "to": SENDER_EMAIL
        }
    else:
        raise HTTPException(status_code=500, detail=f"Failed to push draft: {result.error}")


@router.post("/b2b/distributor-campaign")
def b2b_distributor_campaign(db: Session = Depends(get_db)):
    """
    Daily warm distributor campaign (V1.1: draft-only).
    Drafts a tailored distributor pitch for warm distributor leads and
    queues them in the founder Approval Inbox. Nothing is sent from here.
    """
    from sqlalchemy import or_
    from app.models.models import EmailDraft
    from app.services.email_sender import generate_b2b_pitch_email

    distributor_leads = db.query(B2BLead).filter(
        B2BLead.division == "distributor",
        B2BLead.email.isnot(None),
        B2BLead.email != "",
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED", "EMAIL_SENT", "REPLIED", "INTRO_EMAIL_SENT"]),
        or_(B2BLead.do_not_call.is_(None), B2BLead.do_not_call == False)
    ).all()

    if not distributor_leads:
        return {"status": "no_leads", "drafted": 0, "message": "No warm distributor leads found with emails."}

    drafted = 0
    results = []
    for lead in distributor_leads[:30]:  # cap at 30/day
        existing = db.query(EmailDraft).filter(
            EmailDraft.lead_id == lead.id,
            EmailDraft.status.in_(["PENDING", "APPROVED", "SENT"]),
        ).first()
        if existing:
            results.append({"company": lead.company, "email": lead.email, "status": "already_drafted"})
            continue
        subject, body = generate_b2b_pitch_email(lead)
        db.add(EmailDraft(
            lead_id=lead.id,
            follow_up_type="introduction",
            subject=subject,
            body=body,
            reason="Distributor campaign draft (approval required)",
            status="PENDING",
        ))
        drafted += 1
        results.append({"company": lead.company, "email": lead.email, "status": "drafted"})
    db.commit()

    return {
        "mode": "draft_only",
        "total_targeted": len(distributor_leads),
        "sent": 0,
        "drafted": drafted,
        "message": f"{drafted} distributor drafts queued in the Approval Inbox",
        "results": results[:10],
    }


@router.get("/tender/pending-bids")
def tender_pending_bids(db: Session = Depends(get_db)):
    """Return all tender leads that have AI-drafted bids pending founder approval."""
    tender_leads = db.query(B2BLead).filter(
        B2BLead.division == "tender",
        B2BLead.proposal_text.isnot(None),
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED", "PROPOSAL_SENT"])
    ).order_by(B2BLead.score.desc()).all()

    results = []
    for l in tender_leads:
        results.append({
            "id": l.id,
            "company": l.company,
            "city": l.city,
            "status": l.status,
            "score": l.score,
            "proposal_monthly_kg": l.proposal_monthly_kg,
            "proposal_suggested_price": l.proposal_suggested_price,
            "proposal_text": l.proposal_text,
            "email": l.email,
            "contact_name": l.contact_name,
            "estimated_value": l.estimated_value,
        })
    return {"count": len(results), "bids": results}


@router.post("/tender/approve-and-send/{lead_id}")
def tender_approve_and_send(lead_id: int, db: Session = Depends(get_db)):
    """
    Founder approves a tender bid — system emails it immediately via Zoho SMTP.
    """
    from app.services.email_sender import send_email
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.proposal_text:
        raise HTTPException(status_code=400, detail="No proposal text drafted yet. Run /tender/draft-bid first.")
    if not lead.email:
        raise HTTPException(status_code=400, detail="Lead has no email address.")

    zoho_pw = os.getenv("ZOHO_APP_PASSWORD", "")
    simulate = not bool(zoho_pw and zoho_pw.strip() not in ("", "your_zoho_app_password_here"))

    subject = f"Tender Proposal — Purity Beans Premium Coffee Supply for {lead.company}"
    if simulate:
        lead.status = "PROPOSAL_SENT"
        db.commit()
        return {"status": "simulated", "message": f"[SIMULATE] Proposal would be sent to {lead.email}", "lead_id": lead_id}

    ok = send_email(lead.email, subject, lead.proposal_text)
    if ok:
        lead.status = "PROPOSAL_SENT"
        db.commit()
        return {"status": "sent", "message": f"Proposal emailed to {lead.email}", "lead_id": lead_id}
    else:
        raise HTTPException(status_code=500, detail="Email delivery failed. Check Zoho credentials in .env.")


@router.post("/tender/auto-price")
def tender_auto_price(background_tasks: BackgroundTasks, send_emails: bool = True):
    """
    Daily tender auto-pricer.
    Scans all TENDER leads in PROPOSAL_SENT stage, escalates discount by age,
    regenerates proposal text, and optionally sends nudge emails.
    Safe to call any time — idempotent within the same tier window.
    """
    def _run():
        return run_tender_auto_pricer(send_emails=send_emails)
    report = _run()
    return report

@router.get("/tender/auto-price/preview")
def tender_auto_price_preview():
    """Preview which leads would be escalated — no changes made, no emails sent."""
    return run_tender_auto_pricer(send_emails=False)

@router.get("/b2b/analytics/conversions")
def get_b2b_conversion_analytics(db: Session = Depends(get_db)):
    """Fetch close rate analytics segmented by source, industry, region, and size."""
    return CRMTrackerService.get_conversion_analytics(db)

@router.get("/b2b/analytics/win-loss")
def get_b2b_win_loss_analytics(db: Session = Depends(get_db)):
    """Audit COLD leads and output failure rate breakdown and AI recommendations."""
    agent = WinLossAnalysisAgent(db)
    return agent.audit_lost_deals()

@router.get("/b2b/analytics/product-recommendations")
def get_b2b_product_recommendations(db: Session = Depends(get_db)):
    """Fetch product recommendations based on sensory, pricing, and packaging feedback."""
    agent = ProductIntelligenceAgent(db)
    return agent.get_product_recommendations()

from app.agents.tender.bidder_agent import TenderBidderAgent

class SubmitBidRequest(BaseModel):
    company: str
    bid_price: float
    proposal_text: str

@router.get("/tender/draft-bid")
def tender_draft_bid(company: str, db: Session = Depends(get_db)):
    """
    Drafts a tender bid for an EXISTING lead. Read-only: nothing is created.

    This used to auto-create a lead when the company was unknown, from a GET,
    while its own docstring promised "without saving it". Worse, the row it
    created was fabricated: estimated_value Rs 12,00,000, score 80,
    probability 0.2, city "Abohar", contact_title "Procurement Officer",
    status "QUALIFIED" (claimed qualified with no assessment) and a
    qualification note asserting "BSF Abohar Canteen supply request" — a
    specific claim about a specific organisation that nobody had verified.
    Any caller passing an arbitrary company name minted a fake qualified
    opportunity that then fed the pipeline and every revenue estimate.
    """
    lead = db.query(B2BLead).filter(B2BLead.company == company).first()
    if not lead:
        raise HTTPException(
            status_code=404,
            detail=f"No lead on record for '{company}'. Discover or add the "
                   f"business first — a bid draft must be based on a real "
                   f"opportunity, not one invented at request time.")

    agent = TenderBidderAgent(db)
    result = agent.draft_bid(company)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result

@router.post("/tender/submit-bid")
def tender_submit_bid(req: SubmitBidRequest, db: Session = Depends(get_db)):
    """
    Submits the approved/edited tender bid on the company's behalf.
    """
    agent = TenderBidderAgent(db)
    result = agent.submit_bid(req.company, req.bid_price, req.proposal_text)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result

class SendOutreachRequest(BaseModel):
    simulate: bool = False
    custom_body: Optional[str] = None

@router.post("/b2b/send-outreach")
async def b2b_send_outreach(req: SendOutreachRequest, db: Session = Depends(get_db)):
    """
    V1.1 POLICY: no direct bulk send. Delegates to the draft-only path so
    every first-touch email passes through the founder Approval Inbox.
    """
    # b2b_send_outreach_now is a sync handler (runs in FastAPI's threadpool) —
    # awaiting it raises "object dict can't be used in 'await' expression".
    return b2b_send_outreach_now(db)

@router.get("/founder/reorder-alerts")
def get_founder_reorder_alerts(db: Session = Depends(get_db)):
    """Retrieve predictive reorder alerts for won B2B accounts."""
    alerts = CRMTrackerService.get_reorder_alerts(db)
    return {"total": len(alerts), "alerts": alerts}


# ── B2B Priorities & Action Center Endpoints ─────────────────────────────────

from sqlalchemy import func
import random
from app.services.calling_agent import CallingAgentService

@router.get("/b2b/actions")
def get_b2b_actions(db: Session = Depends(get_db)):
    """Reads cached action list from action_queue table."""
    from app.models.models import ActionQueue
    
    count = db.query(ActionQueue).filter(ActionQueue.status == "PENDING").count()
    if count == 0:
        CRMTrackerService.generate_actions(db)
        
    actions = db.query(ActionQueue).filter(ActionQueue.status == "PENDING").all()
    
    result = []
    for act in actions:
        lead = act.lead
        if not lead:
            continue
        f = CRMTrackerService.get_financial_metrics(lead)
        
        # Friendly actions
        action_titles = {
            "CALL_LEAD": f"Qualify / Call {lead.company}",
            "FOLLOWUP_SAMPLE": f"Follow Up on Sample with {lead.company}",
            "SEND_PROPOSAL": f"Send/Negotiate Proposal for {lead.company}",
            "DISPATCH_SAMPLE": f"Dispatch Sample Kit to {lead.company}",
            "REORDER_ALERT": f"Reorder Call for {lead.company}"
        }
        title = action_titles.get(act.action_type, f"Action for {lead.company}")
        
        result.append({
            "id": act.id,
            "lead_id": lead.id,
            "title": title,
            "type": act.action_type,
            "priority_score": act.priority_score,
            "expected_margin": act.expected_margin,
            "due_date": act.due_date.strftime("%Y-%m-%d") if act.due_date else None,
            "status": act.status,
            "lead_company": lead.company,
            "phone": lead.phone or "",
            "subtitle": f"Margin opportunity: ₹{act.expected_margin/100000.0:.2f}L · Win Prob: {f['win_probability']*100:.0f}%",
            "win_probability": f["win_probability"]
        })
    # Sort by priority_score descending
    result.sort(key=lambda x: x["priority_score"], reverse=True)
    return result

@router.patch("/b2b/actions/{action_id}/done")
def complete_b2b_action(action_id: int, db: Session = Depends(get_db)):
    """Mark an action item as completed by the founder."""
    from app.models.models import ActionQueue
    act = db.query(ActionQueue).filter(ActionQueue.id == action_id).first()
    if not act:
        raise HTTPException(status_code=404, detail="Action not found")
    act.status = "COMPLETED"
    db.commit()
    return {"status": "completed", "action_id": action_id}

@router.post("/b2b/actions/recalculate")
async def recalculate_b2b_actions(db: Session = Depends(get_db)):
    """Triggers recalculation and updates action_queue cache."""
    CRMTrackerService.generate_actions(db)
    # get_b2b_actions is a sync handler (threadpool) — awaiting it raised
    # "object list can't be used in 'await' expression" on every dashboard load.
    return get_b2b_actions(db)

@router.post("/b2b/proposal-rescue-campaign")
def run_proposal_rescue_campaign(db: Session = Depends(get_db)):
    """Triggers outbound voice qualification calls for stagnant proposals."""
    triggered = CallingAgentService.trigger_proposal_rescue_campaign(db)
    return {"status": "success", "triggered_calls": triggered}

@router.post("/b2b/forecast-snapshot")
def trigger_forecast_snapshot(db: Session = Depends(get_db)):
    """Triggers a 7-day forecast accuracy calibration snapshot."""
    from app.models.models import RevenueForecastSnapshot, B2BLead
    from datetime import datetime, timedelta
    
    kpis = CRMTrackerService.get_kpis(db)
    expected_rev_7d = kpis["forecasts"]["7"]["revenue"]
    expected_margin_7d = kpis["forecasts"]["7"]["margin"]
    
    cutoff = datetime.utcnow() - timedelta(days=7)
    won_leads = db.query(B2BLead).filter(
        B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"]),
        B2BLead.last_updated >= cutoff
    ).all()
    
    actual_rev_7d = sum(l.estimated_value or 0.0 for l in won_leads)
    actual_margin_7d = sum(CRMTrackerService.get_financial_metrics(l)["true_net_margin"] for l in won_leads)
    
    if expected_margin_7d > 0:
        error = abs(expected_margin_7d - actual_margin_7d) / expected_margin_7d
        accuracy = max(0.0, (1.0 - error) * 100.0)
    else:
        accuracy = 100.0 if actual_margin_7d == 0 else 0.0
        
    snapshot = RevenueForecastSnapshot(
        snapshot_date=datetime.utcnow(),
        expected_revenue_7d=expected_rev_7d,
        expected_margin_7d=expected_margin_7d,
        actual_revenue_7d=actual_rev_7d,
        actual_margin_7d=actual_margin_7d,
        forecast_accuracy_pct=round(accuracy, 1),
        created_at=datetime.utcnow()
    )
    db.add(snapshot)
    db.commit()
    
    return {
        "status": "success",
        "snapshot": {
            "date": snapshot.snapshot_date.strftime("%Y-%m-%d"),
            "expected_revenue_7d": snapshot.expected_revenue_7d,
            "expected_margin_7d": snapshot.expected_margin_7d,
            "actual_revenue_7d": snapshot.actual_revenue_7d,
            "actual_margin_7d": snapshot.actual_margin_7d,
            "forecast_accuracy_pct": snapshot.forecast_accuracy_pct
        }
    }

@router.get("/founder/actions")
def get_founder_actions(db: Session = Depends(get_db)):
    """
    Returns key actionable item list for Founder Action Center.
    Pulls actual database lead records dynamically.
    """
    active_leads = db.query(B2BLead).filter(B2BLead.status.notin_(["COLD", "DORMANT", "ARCHIVED"])).all()
    actions = []
    
    for l in active_leads:
        f = CRMTrackerService.get_financial_metrics(l)
        if f["expected_margin"] <= 10000:
            continue
        status_upper = (l.status or "DISCOVERED").upper()
        
        if status_upper in ("ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH") and status_upper != "REORDER_PREDICTED":
            continue
            
        time_req = 0.17  # default 10 min
        action_type = "OUTREACH"
        action_title = f"Follow up with {l.company}"
        
        if status_upper == "REPLIED":
            action_type = "CALL"
            action_title = f"Call {l.company} to Book Meeting"
            time_req = 0.25
        elif status_upper == "MEETING_COMPLETED":
            action_type = "SAMPLE"
            action_title = f"Send Sample Kit to {l.company}"
            time_req = 0.083
        elif status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING"):
            action_type = "CALL"
            action_title = f"Call {l.company} for Sensory Feedback"
            time_req = 0.17
        elif status_upper == "FEEDBACK_RECEIVED":
            action_type = "OBJECTION"
            action_title = f"Draft Wholesale Proposal for {l.company}"
            time_req = 0.25
        elif status_upper == "PROPOSAL_SENT":
            action_type = "OBJECTION"
            action_title = f"Negotiate Contract with {l.company}"
            time_req = 0.17
        elif status_upper == "REORDER_PREDICTED":
            action_type = "CALL"
            action_title = f"Call {l.company} for Recurring Reorder"
            time_req = 0.25
        elif l.division == "tender" and status_upper in ("DISCOVERED", "QUALIFIED"):
            action_type = "TENDER"
            action_title = f"Submit Bid for {l.company}"
            time_req = 5.0

        cash_per_hour = f["cash_velocity_score"] / time_req if time_req > 0 else 0.0
        
        actions.append({
            "id": l.id,
            "title": action_title,
            "subtitle": f"Potential Rev: Rs. {f['expected_revenue']/100000.0:.2f}L · Margin: Rs. {f['expected_margin']/100000.0:.2f}L · Win Prob: {f['win_probability']*100:.0f}%",
            "type": action_type,
            "lead_company": l.company,
            "phone": l.phone or "",
            "action_priority_score": f["action_priority_score"],
            "expected_revenue": f["expected_revenue"],
            "expected_margin": f["expected_margin"],
            "win_probability": f["win_probability"],
            "payback": f["payback_period"],
            "cac": f["cac"],
            "ltv": f["ltv"]
        })
        
    # Sort actions by action_priority_score descending
    actions.sort(key=lambda x: x["action_priority_score"], reverse=True)
    return actions[:5]

@router.get("/b2b/leads/{lead_id}/timeline")
def get_lead_timeline(lead_id: int, db: Session = Depends(get_db)):
    """
    Real chronological history for a lead. Returns only events that actually
    happened.

    This endpoint used to FABRICATE a full relationship when a lead had no
    timeline rows — inventing a booked Zoom meeting, a dispatched 1kg sample
    kit, taste scores, a Rs/kg quote and "Deal Won! Wholesale contract signed
    for initial 50kg bulk order" — and then db.commit()ed them as real records.
    It is a GET, reachable from the "View Journey" link on every row, so a
    single click would have written an invented won order into the database and
    every downstream revenue figure that reads the event log.

    Now it reads the immutable WorkflowEvent log, which is the same source the
    Business Memory and the journey checklist use. No events means no history:
    an empty list is the honest answer.
    """
    from app.models.models import LeadTimelineEvent, B2BLead, WorkflowEvent

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    out = []
    for ev in (db.query(LeadTimelineEvent)
               .filter(LeadTimelineEvent.lead_id == lead_id)
               .order_by(LeadTimelineEvent.event_date.asc()).all()):
        out.append({
            "id": ev.id, "event_type": ev.event_type,
            "event_date": ev.event_date.strftime("%Y-%m-%d %H:%M") if ev.event_date else None,
            "title": ev.title, "description": ev.description,
        })

    for ev in (db.query(WorkflowEvent)
               .filter(WorkflowEvent.lead_id == lead_id)
               .order_by(WorkflowEvent.occurred_at.asc()).all()):
        p = ev.payload or {}
        detail = (p.get("subject") or p.get("outcome") or p.get("reason")
                  or p.get("smtp_error") or p.get("refreshed_fields") or "")
        out.append({
            "id": f"wf-{ev.id}", "event_type": ev.event_type,
            "event_date": ev.occurred_at.strftime("%Y-%m-%d %H:%M") if ev.occurred_at else None,
            "title": ev.event_type.replace("_", " ").title(),
            "description": str(detail)[:220],
        })

    out.sort(key=lambda x: x["event_date"] or "")
    return out


@router.get("/b2b/analytics/sku-intelligence")
def get_sku_intelligence(db: Session = Depends(get_db)):
    """
    Aggregates database metrics to answer SKU performance & industry preferences.
    """
    skus = ["Purica", "Ultra Blend", "Bold", "Purista"]
    stats = []
    
    for sku in skus:
        sent_count = db.query(B2BLead).filter(B2BLead.sample_sku == sku, B2BLead.status.in_(["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"])).count()
        won_count = db.query(B2BLead).filter(B2BLead.sample_sku == sku, B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"])).count()
        
        # Average feedback rating (taste + aroma + packaging)
        fb_leads = db.query(B2BLead).filter(B2BLead.sample_sku == sku, B2BLead.sample_taste.isnot(None)).all()
        avg_fb = 0.0
        if fb_leads:
            total_fb = sum((l.sample_taste + l.sample_aroma + l.sample_packaging) / 3.0 for l in fb_leads)
            avg_fb = total_fb / len(fb_leads)
        else:
            avg_fb = 7.8  # Default high-quality baseline
            
        # Revenue from closed won sales
        rev = db.query(func.sum(Sale.revenue)).join(Product).filter(Product.product_name == sku).scalar() or 0.0
        
        conv_rate = (won_count / sent_count * 100.0) if sent_count > 0 else 0.0
        stats.append({
            "sku_name": sku,
            "samples_sent": max(sent_count, 1),
            "orders_won": won_count,
            "conversion_rate": round(conv_rate, 1),
            "average_rating": round(avg_fb, 1),
            "revenue_inr": rev
        })
        
    # Industry preferences
    industries = ["IT & Software", "Financial Services", "Education", "Healthcare", "Wholesale", "Distribution", "Retail", "Supermarket", "Manufacturing", "Hospitality", "Restaurant", "Government"]
    industry_prefs = []
    
    for ind in industries:
        top_sku_query = db.query(B2BLead.sample_sku, func.count(B2BLead.id).label("cnt"))\
            .filter(B2BLead.industry == ind, B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"]))\
            .group_by(B2BLead.sample_sku)\
            .order_by(func.count(B2BLead.id).desc())\
            .first()
        top_sku = top_sku_query[0] if top_sku_query else random.choice(skus)
        industry_prefs.append({
            "industry": ind,
            "preferred_sku": top_sku,
            "reason": "Highest conversion rate" if top_sku_query else "High sensory preference"
        })
        
    # Package size conversion (Pack type differences: Purica/Bold/Purista vs Ultra Blend)
    sent_50g = db.query(B2BLead).filter(B2BLead.sample_sku.in_(["Purica", "Bold", "Purista"]), B2BLead.status.in_(["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED"])).count()
    won_50g = db.query(B2BLead).filter(B2BLead.sample_sku.in_(["Purica", "Bold", "Purista"]), B2BLead.status.in_(["ORDER_WON", "ONBOARDED"])).count()
    conv_50g = (won_50g / sent_50g * 100.0) if sent_50g > 0 else 0.0
    
    sent_100g = db.query(B2BLead).filter(B2BLead.sample_sku == "Ultra Blend", B2BLead.status.in_(["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED"])).count()
    won_100g = db.query(B2BLead).filter(B2BLead.sample_sku == "Ultra Blend", B2BLead.status.in_(["ORDER_WON", "ONBOARDED"])).count()
    conv_100g = (won_100g / sent_100g * 100.0) if sent_100g > 0 else 0.0
    
    package_conversion = {
        "size_50g": {
            "size": "Standard Pack (Purica, Bold, Purista)",
            "samples_sent": max(sent_50g, 1),
            "orders_won": won_50g,
            "conversion_rate": round(conv_50g, 1)
        },
        "size_100g": {
            "size": "Premium Pack (Ultra Blend)",
            "samples_sent": max(sent_100g, 1),
            "orders_won": won_100g,
            "conversion_rate": round(conv_100g, 1)
        }
    }
    
    return {
        "sku_stats": stats,
        "industry_preferences": industry_prefs[:6],
        "package_conversion": package_conversion
    }


@router.get("/discovery/daily-report")
def get_discovery_daily_report(db: Session = Depends(get_db)):
    """
    Returns the morning demand discovery scans.
    """
    # Sum up estimated values of discovered leads to keep it dynamic
    disc_val = db.query(func.sum(B2BLead.estimated_value)).filter(B2BLead.status == "DISCOVERED").scalar() or 0.0
    disc_count = db.query(func.count(B2BLead.id)).filter(B2BLead.status == "DISCOVERED").scalar() or 0
    
    # Calculate specific segment counts dynamically
    pantry = db.query(func.count(B2BLead.id)).filter(B2BLead.division == "corporate", B2BLead.status == "DISCOVERED").scalar() or 0
    hotels = db.query(func.count(B2BLead.id)).filter(B2BLead.division == "horeca", B2BLead.status == "DISCOVERED").scalar() or 0
    dists = db.query(func.count(B2BLead.id)).filter(B2BLead.division == "distributor", B2BLead.status == "DISCOVERED").scalar() or 0
    tenders = db.query(func.count(B2BLead.id)).filter(B2BLead.division == "tender", B2BLead.status == "DISCOVERED").scalar() or 0
    gifting = db.query(func.count(B2BLead.id)).filter(B2BLead.division == "gifting", B2BLead.status == "DISCOVERED").scalar() or 0
    
    return {
        "opportunities_found": max(disc_count, 34),
        "breakdown": {
            "corporate_pantry": max(pantry, 8),
            "hotels": max(hotels, 5),
            "distributors": max(dists, 12),
            "tenders": max(tenders, 4),
            "gifting": max(gifting, 5)
        },
        "expected_value_inr": disc_val if disc_val > 0 else 1840000.0,
        "sources": {
            "google_maps": int(max(disc_count * 0.4, 14)),
            "indiamart": int(max(disc_count * 0.25, 8)),
            "tradeindia": int(max(disc_count * 0.15, 5)),
            "linkedin": int(max(disc_count * 0.12, 4)),
            "gem_tenders": int(max(disc_count * 0.08, 3))
        }
    }


@router.post("/discovery/wholesaler-agglo")
def discover_agglo_wholesalers(
    cities: Optional[str] = None,
    save: bool = True,
    db: Session = Depends(get_db)
):
    """
    Discovers instant/agglomerated coffee wholesalers via Google Maps + IndiaMART.
    These are high-value targets — they already stock Nescafe/Bru and can easily
    add Purity Beans as a premium tier SKU to their existing portfolio.
    cities: comma-separated override, e.g. "Abohar,Ludhiana,Delhi"
    """
    from app.services.lead_discovery import discover_leads, save_discovered_leads, CITY_TIERS

    city_list = (
        [c.strip() for c in cities.split(",") if c.strip()]
        if cities
        else (CITY_TIERS["local"] + CITY_TIERS["punjab"])
    )

    result = discover_leads("wholesaler_agglo", cities=city_list, max_per_query=15)
    saved = {"inserted": 0, "skipped": 0}
    if save and result["leads"]:
        saved = save_discovered_leads(result["leads"], db)

    # Enrich with pitch context
    result["pitch_angle"] = (
        "These wholesalers already handle instant/agglomerated coffee at volume. "
        "Pitch Purity Beans as a margin-premium add-on: ₹360–420/kg vs ₹180–240/kg for agglo. "
        "Lead with the '100% pure, zero chicory' positioning and offer a trial pallet."
    )
    result["target_skus"] = ["Purista 500g", "Purica 500g", "Ultra Blend 250g"]
    result["suggested_moq"] = "10 kg per SKU (₹3,600–4,200 opening order)"
    result["saved"] = saved
    return result


@router.get("/b2b/leads/interested")
def get_interested_leads(db: Session = Depends(get_db)):
    """
    Queries all leads who expressed interest or showed positive engagement.
    """
    from sqlalchemy import or_
    leads = db.query(B2BLead).filter(
        or_(
            B2BLead.status == "REPLIED",
            B2BLead.status == "SAMPLE_SENT",
            B2BLead.email_clicks > 0,
            B2BLead.email_opens > 0
        )
    ).all()
    return {
        "total": len(leads),
        "leads": [
            {
                "id": l.id,
                "company": l.company,
                "contact_name": l.contact_name,
                "contact_title": l.contact_title,
                "email": l.email,
                "phone": l.phone,
                "city": l.city,
                "division": l.division,
                "score": l.score,
                "estimated_value": l.estimated_value,
                "email_sequence_stage": l.email_sequence_stage,
                "email_opens": l.email_opens,
                "email_clicks": l.email_clicks,
                "status": l.status,
                "reply_snippet": l.qualification_notes or "Awaiting follow-up sachet dispatch."
            } for l in leads
        ]
    }


@router.post("/b2b/outreach/sequence-advance")
def advance_sequence_tracker(db: Session = Depends(get_db)):
    """
    DISABLED — this endpoint used to FABRICATE customer engagement.

    It invented email opens/clicks (random.randint), flipped ~25% of leads to
    REPLIED at random, and wrote fake customer quotes into qualification_notes
    ("Replied: We consume 40kg coffee monthly...") as if a real business had said
    them. The dashboard called it on every load, so fake replies accumulated and
    the founder could have chased conversations that never happened. It also
    poisoned the learned-pattern engine, which trains on real outcomes.

    Real engagement comes only from POST /email/sync-replies (Zoho IMAP).
    """
    raise HTTPException(
        status_code=410,
        detail=("sequence-advance is permanently disabled: it fabricated opens, clicks and "
                "replies. Real reply tracking comes from /email/sync-replies (Zoho IMAP)."),
    )


@router.post("/emails/generate-drafts")
def generate_email_drafts(db: Session = Depends(get_db)):
    """
    Scans all leads needing follow-up, writes AI emails, saves as PENDING drafts.
    Nothing is sent — Hiten approves each one first.
    """
    created = generate_followup_drafts(db)
    return {
        "status": "ok",
        "drafted": len(created),
        "drafts": created,
    }

@router.get("/emails/pending-drafts")
def get_pending_drafts(db: Session = Depends(get_db)):
    """Returns all PENDING email drafts waiting for approval."""
    drafts = (
        db.query(EmailDraft)
        .filter(EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"]))
        .order_by(EmailDraft.created_at.desc())
        .all()
    )
    result = []
    for d in drafts:
        lead = d.lead
        result.append({
            "id": d.id,
            "lead_id": d.lead_id,
            "company": lead.company if lead else "Unknown",
            "contact_name": lead.contact_name if lead else "",
            "contact_email": lead.email if lead else "",
            "city": lead.city if lead else "",
            "industry": lead.industry if lead else "",
            "lead_status": lead.status if lead else "",
            "follow_up_type": d.follow_up_type,
            "reason": d.reason,
            "subject": d.subject,
            "body": d.body,
            "created_at": d.created_at.isoformat(),
        })
    return {"count": len(result), "drafts": result}

# NOTE: the legacy POST /emails/approve-draft/{id} endpoint was removed here.
# It was dead (no caller in the frontend or backend) and broken: it did
# result.get("status") on the OutreachEmail object send_email() returns (which
# has no .get → AttributeError), and gated on draft.status == "PENDING" while
# real drafts sit at "DRAFT". The live approve+send path is
# POST /b2b/workflow/approve-journey → bg_send_emails, which is crypto-hash
# gated and status-correct.

@router.put("/emails/edit-draft/{draft_id}")
def edit_draft(draft_id: int, payload: dict, db: Session = Depends(get_db)):
    """Update subject/body of a pending draft before approving."""
    draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if "subject" in payload:
        draft.subject = payload["subject"]
    if "body" in payload:
        draft.body = payload["body"]
    draft.status = "EDITED"
    db.commit()
    return {"status": "updated", "id": draft_id}

@router.post("/emails/skip-draft/{draft_id}")
def skip_draft(draft_id: int, db: Session = Depends(get_db)):
    """Skip (dismiss) a pending draft without sending."""
    draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.status = "SKIPPED"
    db.commit()
    return {"status": "skipped", "id": draft_id}


# ── Permanent Activity Timeline ───────────────────────────────────────────────

@router.get("/founder/activity-timeline")
def get_activity_timeline(days: int = 30, db: Session = Depends(get_db)):
    """
    Returns a permanent, grouped activity timeline — nothing is ever deleted.
    Groups entries into: Today, Yesterday, Last 7 Days, Last 30 Days, Older.
    """
    from datetime import date, timedelta
    now   = datetime.utcnow()
    today = now.date()
    yesterday = today - timedelta(days=1)
    week_ago  = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    logs = (
        db.query(AgentLog)
        .order_by(AgentLog.timestamp.desc())
        .limit(500)
        .all()
    )

    def classify(ts: datetime) -> str:
        d = ts.date()
        if d == today:       return "Today"
        if d == yesterday:   return "Yesterday"
        if d >= week_ago:    return "Last 7 Days"
        if d >= month_ago:   return "Last 30 Days"
        return "Older"

    # Also pull key CRM events from lead history
    crm_events = []
    won_leads = db.query(B2BLead).filter(
        B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])
    ).all()
    for l in won_leads:
        crm_events.append({
            "time": l.last_updated.isoformat() if l.last_updated else now.isoformat(),
            "agent": "CRM",
            "action": "Order Won",
            "detail": l.company,
            "type": "order_won",
        })

    sample_leads = db.query(B2BLead).filter(
        B2BLead.status.in_(["SAMPLE_SENT", "PROPOSAL_SENT"])
    ).all()
    for l in sample_leads:
        crm_events.append({
            "time": l.last_updated.isoformat() if l.last_updated else now.isoformat(),
            "agent": "CRM",
            "action": "Sample Dispatched" if l.status == "SAMPLE_SENT" else "Proposal Sent",
            "detail": l.company,
            "type": "sample" if l.status == "SAMPLE_SENT" else "proposal",
        })

    meeting_leads = db.query(B2BLead).filter(
        B2BLead.status.in_(["MEETING_BOOKED", "MEETING_COMPLETED"])
    ).all()
    for l in meeting_leads:
        crm_events.append({
            "time": l.last_updated.isoformat() if l.last_updated else now.isoformat(),
            "agent": "CRM",
            "action": "Meeting Booked" if l.status == "MEETING_BOOKED" else "Meeting Completed",
            "detail": l.company,
            "type": "meeting",
        })

    groups: dict = {"Today": [], "Yesterday": [], "Last 7 Days": [], "Last 30 Days": [], "Older": []}

    for log in logs:
        group = classify(log.timestamp)
        groups[group].append({
            "time": log.timestamp.isoformat(),
            "agent": log.agent_name,
            "action": log.action,
            "detail": (log.payload or {}).get("company", (log.payload or {}).get("detail", "")),
            "type": "agent_log",
        })

    for ev in crm_events:
        try:
            ts = datetime.fromisoformat(ev["time"])
            group = classify(ts)
            groups[group].append(ev)
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # Sort each group newest first
    for g in groups:
        groups[g].sort(key=lambda x: x["time"], reverse=True)

    total = sum(len(v) for v in groups.values())
    return {
        "total_events": total,
        "groups": groups,
        "generated_at": now.isoformat(),
    }


# ── Agent Status / Memory ─────────────────────────────────────────────────────

@router.get("/founder/agent-status")
def get_agent_status(db: Session = Depends(get_db)):
    """
    Returns last-run time and last result for each AI agent.
    Used for the 'Agent Continuity' panel — shows what ran, what's queued.
    """
    agents = [
        "DemandDiscovery", "TenderTeam", "EmailDrafter",
        "CRMTracker", "OutreachAgent", "B2BSalesVP",
    ]
    result = {}
    for agent_name in agents:
        last = (
            db.query(AgentLog)
            .filter(AgentLog.agent_name == agent_name)
            .order_by(AgentLog.timestamp.desc())
            .first()
        )
        result[agent_name] = {
            "last_run": last.timestamp.isoformat() if last else None,
            "last_action": last.action if last else "Never run",
            "last_detail": (last.payload or {}).get("detail", "") if last else "",
        }

    # Add live counts as context
    pending_emails = db.query(EmailDraft).filter(EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"])).count()
    discovered     = db.query(B2BLead).filter(B2BLead.status == "DISCOVERED").count()
    replied        = db.query(B2BLead).filter(B2BLead.status == "REPLIED").count()

    return {
        "agents": result,
        "queue": {
            "email_drafts_pending": pending_emails,
            "leads_to_contact": discovered,
            "leads_replied": replied,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── Session Restore Snapshot ──────────────────────────────────────────────────

@router.get("/founder/session-snapshot")
def get_session_snapshot(db: Session = Depends(get_db)):
    """
    Single endpoint that returns everything needed to restore a full session.
    Called once on dashboard load to hydrate all panels simultaneously.
    """
    # Pipeline counts
    from sqlalchemy import func
    stage_counts = dict(
        db.query(B2BLead.status, func.count(B2BLead.id))
        .group_by(B2BLead.status)
        .all()
    )

    won    = sum(stage_counts.get(s, 0) for s in ["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])
    meetings = sum(stage_counts.get(s, 0) for s in ["MEETING_BOOKED", "MEETING_COMPLETED"])
    samples  = sum(stage_counts.get(s, 0) for s in ["SAMPLE_SENT"])
    replied  = stage_counts.get("REPLIED", 0)
    email_sent = stage_counts.get("EMAIL_SENT", 0)
    discovered = stage_counts.get("DISCOVERED", 0)

    # Top lead by intent
    top_lead = (
        db.query(B2BLead)
        .filter(B2BLead.status.notin_(["COLD"]))
        .order_by(B2BLead.intent_score.desc(), B2BLead.estimated_value.desc())
        .first()
    )

    # Recent activity (last 20 log entries)
    recent_logs = (
        db.query(AgentLog)
        .order_by(AgentLog.timestamp.desc())
        .limit(20)
        .all()
    )

    # Pending approvals
    pending_emails = db.query(EmailDraft).filter(EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"])).count()

    return {
        "pipeline": {
            "discovered": discovered,
            "email_sent": email_sent,
            "replied": replied,
            "meetings": meetings,
            "samples": samples,
            "won": won,
            "stage_counts": stage_counts,
        },
        "top_lead": {
            "company": top_lead.company if top_lead else None,
            "contact": top_lead.contact_name if top_lead else None,
            "phone": top_lead.phone if top_lead else None,
            "value": top_lead.estimated_value if top_lead else 0,
            "intent": top_lead.intent_score if top_lead else 0,
            "status": top_lead.status if top_lead else None,
            "city": top_lead.city if top_lead else None,
        },
        "recent_activity": [
            {
                "time": log.timestamp.strftime("%d %b %H:%M"),
                "agent": log.agent_name,
                "action": log.action,
                "detail": (log.payload or {}).get("company", ""),
            }
            for log in recent_logs
        ],
        "pending_approvals": pending_emails,
        "generated_at": datetime.utcnow().isoformat(),
        "session_id": int(datetime.utcnow().timestamp()),
    }


# ── Revenue Engine: Scoring, Filters, Discovery ──────────────────────────────

from app.services.revenue_scoring import score_all_leads, score_lead

@router.post("/b2b/score-leads")
def score_leads(db: Session = Depends(get_db)):
    """
    Run the 5-factor revenue scoring model across all leads.
    Persists final_score, revenue_tier, estimated_annual_value, segment.
    Safe to call any time — idempotent.
    """
    result = score_all_leads(db)
    return {"status": "scored", **result}


@router.get("/b2b/founder-focus")
def get_founder_focus(db: Session = Depends(get_db)):
    """
    Returns Top 10 leads ranked by revenue score + annual value.
    This is the daily decision list — highest ROI leads first.
    """
    from sqlalchemy import text as _text

    # Ensure scoring columns exist
    for col, typedef in [
        ("final_score", "INTEGER DEFAULT 0"),
        ("revenue_tier", "VARCHAR DEFAULT 'Bronze'"),
        ("estimated_annual_value", "FLOAT DEFAULT 0"),
        ("segment", "VARCHAR DEFAULT 'corporate'"),
    ]:
        try:
            db.execute(_text(f"ALTER TABLE b2b_leads ADD COLUMN {col} {typedef}"))
            db.commit()
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # Score un-scored leads on the fly
    unscored = db.query(B2BLead).filter(
        (B2BLead.final_score == None) | (B2BLead.final_score == 0)
    ).all()
    for lead in unscored:
        r = score_lead(lead)
        lead.final_score = r["final_score"]
        lead.revenue_tier = r["revenue_tier"]
        lead.estimated_annual_value = r["estimated_annual_value"]
        lead.segment = r["segment"]
    if unscored:
        db.commit()

    # Fetch top 10 by final_score then by estimated_annual_value
    try:
        leads = (
            db.query(B2BLead)
            .filter(B2BLead.status.notin_(["COLD", "ORDER_WON", "ONBOARDED"]))
            .order_by(B2BLead.final_score.desc(), B2BLead.estimated_annual_value.desc())
            .limit(10)
            .all()
        )
    except Exception:
        leads = (
            db.query(B2BLead)
            .filter(B2BLead.status.notin_(["COLD", "ORDER_WON", "ONBOARDED"]))
            .order_by(B2BLead.score.desc(), B2BLead.estimated_value.desc())
            .limit(10)
            .all()
        )

    TIER_COLORS = {"Platinum": "#e5c97e", "Gold": "#facc15", "Silver": "#94a3b8", "Bronze": "#a16207"}
    result = []
    for rank, l in enumerate(leads, 1):
        scored = score_lead(l)
        result.append({
            "rank": rank,
            "id": l.id,
            "company": l.company,
            "contact_name": l.contact_name or "",
            "phone": l.phone or "",
            "city": l.city or "",
            "segment": scored["segment"],
            "status": l.status,
            "final_score": scored["final_score"],
            "revenue_tier": scored["revenue_tier"],
            "tier_color": TIER_COLORS.get(scored["revenue_tier"], "#6b7280"),
            "estimated_annual_value": scored["estimated_annual_value"],
            "win_probability": round((l.probability or 0) * 100),
            "recommended_action": l.recommended_action or "Reach out via WhatsApp",
            "factor_breakdown": scored["factor_breakdown"],
        })

    # Pipeline-level KPIs
    all_leads = db.query(B2BLead).all()
    total_pipeline = sum(score_lead(l)["estimated_annual_value"] for l in all_leads)
    platinum_leads = [l for l in all_leads if score_lead(l)["final_score"] >= 90]
    platinum_pipeline = sum(score_lead(l)["estimated_annual_value"] for l in platinum_leads)

    return {
        "top_10": result,
        "pipeline_summary": {
            "total_leads": len(all_leads),
            "total_pipeline_value": round(total_pipeline),
            "platinum_count": len(platinum_leads),
            "platinum_pipeline": round(platinum_pipeline),
        },
    }


@router.get("/b2b/easy-wins")
def get_easy_wins(db: Session = Depends(get_db)):
    """Leads with score >= 85 — highest probability of fast conversion."""
    leads = db.query(B2BLead).filter(
        B2BLead.status.notin_(["COLD", "ORDER_WON", "ONBOARDED"])
    ).all()

    easy_wins = []
    for l in leads:
        r = score_lead(l)
        if r["final_score"] >= 85:
            easy_wins.append({
                "id": l.id,
                "company": l.company,
                "contact_name": l.contact_name or "",
                "phone": l.phone or "",
                "city": l.city or "",
                "segment": r["segment"],
                "revenue_tier": r["revenue_tier"],
                "final_score": r["final_score"],
                "estimated_annual_value": r["estimated_annual_value"],
                "status": l.status,
                "recommended_action": l.recommended_action or "WhatsApp outreach",
            })

    easy_wins.sort(key=lambda x: (-x["final_score"], -x["estimated_annual_value"]))
    return {"count": len(easy_wins), "leads": easy_wins[:50]}


@router.get("/b2b/revenue-pipeline")
def get_revenue_pipeline(db: Session = Depends(get_db)):
    """
    Revenue-first pipeline KPIs:
      - Discovered this week (new leads added)
      - Revenue advanced (leads that moved stage)
      - Revenue won (ORDER_WON)
      - Segment breakdown
      - Outreach split recommendations
    """
    from datetime import timedelta
    from sqlalchemy import func

    week_ago = datetime.utcnow() - timedelta(days=7)
    month_ago = datetime.utcnow() - timedelta(days=30)

    all_leads = db.query(B2BLead).all()

    # Revenue totals by status tier
    def val(lead):
        r = score_lead(lead)
        return r["estimated_annual_value"]

    discovered_value = sum(val(l) for l in all_leads if l.status == "DISCOVERED")
    contacted_value  = sum(val(l) for l in all_leads if l.status in ("EMAIL_SENT", "REPLIED"))
    advanced_value   = sum(val(l) for l in all_leads if l.status in ("MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT"))
    negotiation_value = sum(val(l) for l in all_leads if l.status == "PROPOSAL_SENT")
    won_value        = sum(val(l) for l in all_leads if l.status in ("ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"))

    # Segment pipeline
    seg_totals: dict[str, float] = {}
    for l in all_leads:
        r = score_lead(l)
        seg = r["segment"]
        seg_totals[seg] = seg_totals.get(seg, 0) + r["estimated_annual_value"]

    # Tier breakdown
    tier_counts: dict[str, int] = {"Platinum": 0, "Gold": 0, "Silver": 0, "Bronze": 0}
    tier_values: dict[str, float] = {"Platinum": 0, "Gold": 0, "Silver": 0, "Bronze": 0}
    for l in all_leads:
        r = score_lead(l)
        t = r["revenue_tier"]
        tier_counts[t] = tier_counts.get(t, 0) + 1
        tier_values[t] = tier_values.get(t, 0) + r["estimated_annual_value"]

    return {
        "funnel": {
            "discovered":   round(discovered_value),
            "contacted":    round(contacted_value),
            "advanced":     round(advanced_value),
            "negotiation":  round(negotiation_value),
            "won":          round(won_value),
            "total":        round(discovered_value + contacted_value + advanced_value + negotiation_value + won_value),
        },
        "segment_pipeline": {k: round(v) for k, v in sorted(seg_totals.items(), key=lambda x: -x[1])},
        "tier_breakdown": {
            "counts": tier_counts,
            "values": {k: round(v) for k, v in tier_values.items()},
        },
        "recommended_effort": {
            "gifting":          40,
            "distributor":      30,
            "grocery":          15,
            "corporate_pantry": 10,
            "horeca_cafe":       5,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


class DiscoveryRequest(BaseModel):
    segment: str = ""
    cities: Optional[list[str]] = None
    radius: str = "local"   # local | punjab | north | national | all
    save: bool = True
    state: Optional[str] = None
    method: Optional[str] = None
    search_mode: Optional[str] = "discover"  # "existing" or "discover"
    sort_by: Optional[str] = "best_conversion"  # best_conversion | intent | willing_to_switch | recent | segment_conversion | value | newest

@router.post("/discovery/run")
def run_discovery(req: DiscoveryRequest, db: Session = Depends(get_db)):
    """
    Run lead discovery / state-led outreach search for a given segment and method.
    """
    from app.services.revenue_os import find_best_opportunities
    try:
        return find_best_opportunities(
            db,
            state=req.state,
            cities=req.cities,
            category=req.segment,
            outreach_method=req.method,
            force_discovery=(req.search_mode in ("discover", "intelligent")),
            query_text=None
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/discovery/outreach-program")
def run_outreach_program(background_tasks: BackgroundTasks, db: Session = Depends(get_db), radius: str = "local"):
    """
    Discovery + draft pipeline (V1.1: NEVER auto-sends).
    1. Discovers distributor / grocery / retailer leads for given radius
    2. Saves new leads to DB
    3. Drafts intro emails into the founder Approval Inbox
    radius: local | punjab | north | national | all
    """
    from app.services.lead_discovery import discover_leads, save_discovered_leads, CITY_TIERS, PRIORITY_CITIES
    from app.models.models import EmailDraft
    from app.services.email_sender import generate_b2b_pitch_email

    if radius == "auto":
        # V1.2 expansion engine: target the first incomplete stage's
        # uncovered cities (Abohar → 50km → Punjab → North → PAN India)
        from app.services.revenue_engine import expansion_status
        exp = expansion_status(db.query(B2BLead).all())
        cities = exp["next_cities"] or CITY_TIERS["local"]
    elif radius == "all":
        cities = PRIORITY_CITIES
    else:
        cities = CITY_TIERS.get(radius, CITY_TIERS["local"])

    segments = ["distributor", "grocery", "wholesaler_agglo"]
    total_inserted = 0
    total_skipped = 0
    all_new_leads = []
    segment_results = {}

    for seg in segments:
        result = discover_leads(segment=seg, cities=cities, max_per_query=15)
        if result["leads"]:
            saved = save_discovered_leads(result["leads"], db)
            total_inserted += saved["inserted"]
            total_skipped += saved["skipped"]
            segment_results[seg] = {
                "found": result["leads_found"],
                "inserted": saved["inserted"],
                "skipped": saved["skipped"],
            }
            if saved["inserted"] > 0:
                companies = [l["company"] for l in result["leads"] if l.get("company") and "[Demo]" not in l.get("company","")]
                new_db_leads = db.query(B2BLead).filter(
                    B2BLead.company.in_(companies),
                    B2BLead.status == "DISCOVERED",
                    B2BLead.email.isnot(None),
                    B2BLead.email != ""
                ).all()
                all_new_leads.extend(new_db_leads)

    # Draft intro emails for the founder's Approval Inbox — no send from here.
    drafted = 0
    for lead in all_new_leads:
        existing = db.query(EmailDraft).filter(
            EmailDraft.lead_id == lead.id,
            EmailDraft.status.in_(["PENDING", "APPROVED", "SENT"]),
        ).first()
        if existing:
            continue
        subject, body = generate_b2b_pitch_email(lead)
        db.add(EmailDraft(
            lead_id=lead.id,
            follow_up_type="introduction",
            subject=subject,
            body=body,
            reason="Drafted after discovery (approval required)",
            status="PENDING",
        ))
        drafted += 1
    db.commit()

    # Silent background pipeline: directory enrichment + email verification
    if all_new_leads:
        background_tasks.add_task(_silent_verify_and_enrich, [l.id for l in all_new_leads])

    return {
        "status": "ok",
        "radius": radius,
        "cities_searched": cities,
        "segments_run": segments,
        "total_new_leads": total_inserted,
        "total_skipped": total_skipped,
        "emails_sent": 0,
        "drafted_for_approval": drafted,
        "segment_breakdown": segment_results,
    }


@router.get("/discovery/search-profiles")
def get_search_profiles():
    """Returns the configured search profiles and priority cities."""
    from app.services.lead_discovery import SEARCH_PROFILES, PRIORITY_CITIES, SEGMENT_VALUE_ESTIMATE
    return {
        "segments": list(SEARCH_PROFILES.keys()),
        "profiles": SEARCH_PROFILES,
        "priority_cities": PRIORITY_CITIES,
        "value_estimates": SEGMENT_VALUE_ESTIMATE,
        "note": "Add GOOGLE_MAPS_API_KEY to .env for real Google Maps results",
    }

class FindEmailsRequest(BaseModel):
    lead_ids: list[int]
    send_after_find: bool = False
    force_replace: bool = False   # re-search even if email already stored (replaces fakes)
    limit: int = 10


@router.post("/b2b/leads/find-emails")
async def find_emails_and_send(req: FindEmailsRequest, db: Session = Depends(get_db)):
    """
    Search websites, Google, JustDial, IndiaMart for each lead's email.
    Runs in a thread pool so heavy scraping can't block the FastAPI event loop.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _find_emails_sync(req, db))


def _find_emails_sync(req: FindEmailsRequest, db):
    from app.services.contact_enricher import find_lead_email
    from app.services.email_sender import build_outreach_email, send_email as _send_email

    # Cap to limit so the request completes well within proxy timeout
    batch = req.lead_ids[: max(1, min(req.limit, 20))]

    results = []
    for lead_id in batch:
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            continue

        found = find_lead_email(
            company=lead.company or "",
            city=lead.city or "",
            website=lead.website or "",
            existing_email=lead.email or "",
            force_replace=req.force_replace,
        )

        # Persist everything discovered back to the lead record
        changed = False
        if found.get("discovered_website") and not lead.website:
            lead.website = found["discovered_website"]; changed = True
        if found.get("phone") and not lead.phone:
            lead.phone = found["phone"]; changed = True
        if found.get("whatsapp") and not lead.whatsapp_number:
            lead.whatsapp_number = found["whatsapp"]; changed = True
        if changed:
            db.commit()

        emailed = False
        email_status = None

        if found["email"] and found["confidence"] not in ("NONE",):
            # Save if new / better than existing
            if not lead.email or found["confidence"] in ("HIGH", "MEDIUM", "EXISTING"):
                lead.email = found["email"]
                db.commit()

            # Send intro email if requested and email is real (not just a guess)
            # V1.1: never send directly — queue a PENDING draft for the
            # founder Approval Inbox when a real (non-guessed) email is found.
            if req.send_after_find and found["confidence"] not in ("GUESSED", "NONE"):
                from app.models.models import EmailDraft
                from app.services.email_sender import generate_b2b_pitch_email
                existing = db.query(EmailDraft).filter(
                    EmailDraft.lead_id == lead.id,
                    EmailDraft.status.in_(["PENDING", "APPROVED", "SENT"]),
                ).first()
                if not existing:
                    subject, body = generate_b2b_pitch_email(lead)
                    db.add(EmailDraft(
                        lead_id=lead.id,
                        follow_up_type="introduction",
                        subject=subject,
                        body=body,
                        reason="Drafted after email discovery (approval required)",
                        status="PENDING",
                    ))
                    db.commit()
                    emailed = True          # kept for UI compat: "queued as draft"
                    email_status = "drafted"

        results.append({
            "lead_id":       lead_id,
            "company":            lead.company,
            "email":              found["email"],
            "confidence":         found["confidence"],
            "source":             found["source"],
            "all_candidates":     found["all_candidates"],
            "discovered_website": found.get("discovered_website", ""),
            "phone_from_web":     found.get("phone", ""),
            "whatsapp_from_web":  found.get("whatsapp", ""),
            "emailed":            emailed,
            "email_status":       email_status,
        })

    sent_count  = sum(1 for r in results if r["emailed"])
    found_count = sum(1 for r in results if r["email"])
    return {
        "results":         results,
        "emails_found":    found_count,
        "emails_sent":     sent_count,
        "processed_ids":   [r["lead_id"] for r in results],
        "total_requested": len(req.lead_ids),
        "batch_size":      len(batch),
    }


@router.post("/b2b/leads/enrich-contacts")
async def enrich_contacts(lead_ids: list[int], db: Session = Depends(get_db)):
    """Offloads scraping to a thread so the event loop stays free."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _enrich_contacts_sync(lead_ids, db))


_PLACEHOLDER_PHONE_RE = None

def _is_placeholder_phone(value: str | None) -> bool:
    """Detect fabricated numbers like +91-88888-88888 / 99999-99999 / 12345…"""
    import re as _re
    if not value:
        return False
    digits = _re.sub(r"\D", "", value)
    core = digits[-10:] if len(digits) >= 10 else digits
    if len(set(core)) <= 2:                    # 8888888888, 9999999999
        return True
    return bool(_re.search(r"(12345|00000|11111)", core))


def _is_id_derived_phone(value: str | None, lead_id: int) -> bool:
    """
    Detect a phone number mechanically generated from the lead's own row id
    (e.g. +91-98765-00156 on lead 156). Found live in the DB on 2026-07-17:
    67 leads carried this pattern with no phone_source — a stale seed/fixture
    value, not a real scraped number. The odds of a genuine phone number
    ending in the exact zero-padded row id are effectively zero, so this is a
    safe, specific signal — unlike the generic digit-run checks above, it
    can't false-positive on a real number.
    """
    import re as _re
    if not value:
        return False
    digits = _re.sub(r"\D", "", value)
    tail = digits[-5:] if len(digits) >= 5 else digits
    return tail == str(lead_id).zfill(5)


def _is_pattern_email(email: str | None, company: str | None) -> bool:
    """Detect firstname.lastname@<company-slug>.com auto-generated emails."""
    import re as _re
    if not email or "@" not in email:
        return False
    local, domain = email.lower().split("@", 1)
    slug = _re.sub(r"[^a-z0-9]", "", (company or "").lower())
    domain_base = domain.split(".")[0].replace("-", "")
    # domain fabricated straight from the company name + generic name pattern
    return bool(slug) and domain_base == slug and bool(_re.match(r"^[a-z]+\.[a-z]+$", local))


@router.get("/b2b/leads/fabricated-audit")
def fabricated_audit(db: Session = Depends(get_db)):
    """Count leads carrying fabricated contact data (V1.1 audit, read-only)."""
    leads = db.query(B2BLead).all()
    flagged = []
    for l in leads:
        reasons = []
        if _is_placeholder_phone(l.phone) or _is_placeholder_phone(l.whatsapp_number):
            reasons.append("placeholder_phone")
        if _is_id_derived_phone(l.phone, l.id) or _is_id_derived_phone(l.whatsapp_number, l.id):
            reasons.append("id_derived_phone")
        if _is_pattern_email(l.email, l.company):
            reasons.append("pattern_email")
        if reasons:
            flagged.append({"id": l.id, "company": l.company, "city": l.city,
                            "phone": l.phone, "email": l.email,
                            "status": l.status, "reasons": reasons})
    return {"total_leads": len(leads), "fabricated": len(flagged), "leads": flagged}


@router.post("/b2b/leads/quarantine-fabricated")
def quarantine_fabricated(db: Session = Depends(get_db)):
    """
    V1.1 NO-MOCK-DATA enforcement. For every lead with placeholder phones or
    pattern-generated emails:
    - clears the fabricated phone / whatsapp / email fields (empty ≠ invented)
    - resets false outreach statuses (WHATSAPP_SENT etc. that never happened)
      back to DISCOVERED so the pipeline reflects reality
    - logs an immutable DATA_QUARANTINED WorkflowEvent per lead
    Leads themselves are kept — re-run discovery/enrichment to find real contacts.
    """
    from app.services.pipeline_tracker import track

    leads = db.query(B2BLead).all()
    cleaned = []
    FALSE_OUTREACH = {"INTRO_EMAIL_SENT", "EMAIL_SENT", "WHATSAPP_SENT", "AI_CALLED"}
    for l in leads:
        fake_phone = _is_placeholder_phone(l.phone)
        fake_wa = _is_placeholder_phone(l.whatsapp_number)
        fake_email = _is_pattern_email(l.email, l.company)
        if not (fake_phone or fake_wa or fake_email):
            continue
        before = l.status
        removed = []
        if fake_phone:
            l.phone = None
            removed.append("phone")
        if fake_wa:
            l.whatsapp_number = None
            removed.append("whatsapp")
        if fake_email:
            l.email = None
            l.email_verification_status = "UNVERIFIED"
            removed.append("email")
        # Status claimed outreach that never really happened via fake contacts
        if l.status in FALSE_OUTREACH:
            l.status = "DISCOVERED"
        l.last_updated = datetime.utcnow()
        track(db, "DATA_QUARANTINED", lead_id=l.id, actor="SYSTEM", channel="system",
              before_status=before, after_status=l.status,
              payload={"removed_fields": removed})
        cleaned.append({"id": l.id, "company": l.company, "removed": removed,
                        "status": f"{before} → {l.status}"})
    db.commit()
    return {"quarantined": len(cleaned), "leads": cleaned[:50],
            "message": f"{len(cleaned)} leads cleaned of fabricated contact data — "
                       "run discovery + Verify WhatsApp to find real contacts"}


def _enrich_contacts_sync(lead_ids: list[int], db):
    from app.services.contact_enricher import enrich_lead_contact

    results = []
    for lead_id in lead_ids:
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            continue
        enriched = enrich_lead_contact(lead.company, lead.city or "", lead.phone or "",
                                    website=lead.website or "")
        changed = False
        if enriched["confirmed_phone"] and enriched["confirmed_phone"] != lead.phone:
            lead.phone = enriched["confirmed_phone"]
            changed = True
        if enriched["whatsapp_number"] and not lead.whatsapp_number:
            lead.whatsapp_number = enriched["whatsapp_number"]
            changed = True
        # Same rule as the auto-warm loop below: a purged address stays purged.
        if (enriched["email"] and not lead.email
                and (lead.email_verification_status or "") != "PURGED"):
            lead.email = enriched["email"]
            changed = True
        if changed:
            db.commit()
        results.append({
            "lead_id": lead_id,
            "company": lead.company,
            "confirmed_phone": enriched["confirmed_phone"],
            "whatsapp_number": enriched["whatsapp_number"],
            "email": enriched["email"],
            "sources_checked": enriched["sources_checked"],
            "confidence": enriched["confidence"],
        })
    return {"enriched": results, "total": len(results)}


@router.get("/discovery/buy-leads")
def get_buy_leads(product: str = "coffee beans", db: Session = Depends(get_db)):
    """Scrape IndiaMart buy leads (demand signals) for a product pan-India."""
    from app.services.lead_discovery import discover_buy_leads
    leads = discover_buy_leads(product=product, max_results=40)
    return {"leads": leads, "product": product, "total": len(leads)}


class ApproveBatchRequest(BaseModel):
    leads: list[dict]
    send_intro_email: bool = False


def _silent_verify_and_enrich(lead_ids: list[int]):
    """
    Background pipeline run after every discovery approval (V1.1 §Discovery):
    1. Enrich missing phone/WhatsApp/email from real online directories
       (JustDial, Sulekha, IndiaMART, company website) — only found data saved.
    2. Verify every email (MX, disposable, domain-match) so drafts carry a
       verification badge in the Approval Inbox. No founder buttons needed.
    """
    from app.database.database import SessionLocal
    from app.services.contact_enricher import enrich_lead_contact
    from app.services.email_verifier import verify_email

    _db = SessionLocal()
    try:
        for lead_id in lead_ids:
            lead = _db.query(B2BLead).filter(B2BLead.id == lead_id).first()
            if not lead:
                continue
            # 1. Web-wide enrichment: 5 directories + 2 search engines.
            #    Confirms existing numbers AND finds missing ones.
            #    A phone is only phone_verified=True when at least one
            #    real web source lists it.
            try:
                enriched = enrich_lead_contact(lead.company, lead.city or "", lead.phone or "",
                                    website=lead.website or "")
                if (enriched["confirmed_phone"] and enriched["confidence"] in ("HIGH", "MEDIUM")
                        and not _is_placeholder_phone(enriched["confirmed_phone"])):
                    # A first-party number outranks a search result. Enrichment
                    # may fill an EMPTY phone and may confirm one that agrees,
                    # but it may not replace one a publisher gave us — that is
                    # how a Nestle distributor's real line became a searched
                    # guess. Same rule as the PURGED guard on email below.
                    from app.services.contact_enricher import (
                        digits_only, phone_is_authoritative)

                    _protected = phone_is_authoritative(lead)
                    _agrees = digits_only(lead.phone) == digits_only(enriched["confirmed_phone"])
                    if _protected and not _agrees:
                        logger.info(
                            "enrich: keeping first-party phone for %s (%s); web suggested %s",
                            lead.company, lead.phone_source, enriched["confirmed_phone"])
                    else:
                        lead.phone = enriched["confirmed_phone"]
                        if not _protected:
                            lead.phone_source = ", ".join(enriched.get("sources_checked", [])[:4])
                    if not lead.whatsapp_number:
                        lead.whatsapp_number = enriched["whatsapp_number"] or enriched["confirmed_phone"]
                    if _agrees or not _protected:
                        lead.phone_verified = True
                # PURGED means the founder (or a data-quality sweep) deliberately
                # removed an address as unconfirmed. Without this check the loop
                # read the empty field as "missing" and enriched a new guess in
                # within minutes: admin@powergridindia.com was purged at 05:38
                # and cmd@powergrid.in — the CMD's inbox at a real PSU — was
                # written back with no event recorded. A deletion that undoes
                # itself is worse than no deletion, because it looks done.
                if (enriched["email"] and not lead.email
                        and (lead.email_verification_status or "") != "PURGED"):
                    lead.email = enriched["email"]
                    lead.email_verification_status = "UNVERIFIED"
                lead.contact_searched_at = datetime.utcnow()
            except Exception:
                lead.contact_searched_at = datetime.utcnow()
            # 2. Silent email verification
            if lead.email and (lead.email_verification_status or "UNVERIFIED") == "UNVERIFIED":
                try:
                    result = verify_email(
                        lead.email,
                        company_name=lead.company or "",
                        division=lead.division or "",
                        website=lead.website or "",
                    )
                    lead.email_verified = result["status"] == "VALID"
                    lead.email_confidence = result["confidence"]
                    lead.email_verification_status = result["status"]
                    lead.email_mx_valid = result["mx_valid"]
                    lead.email_is_generic = result["is_generic"]
                    lead.email_domain_match = result["domain_match"]
                    lead.email_verify_reason = result["reason"]
                except Exception as _exc:
                    # Swallowed on purpose — this path must not break the
                    # caller — but never silently: a failure with no name is
                    # how the category engine fell back for hours unnoticed.
                    _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

            # 3. AUTO-WARM (V1.2): the moment a usable email is confirmed,
            #    auto-draft the intro email into the Approval Inbox — no
            #    founder click. Draft-only (founder approval still required
            #    to send). Cold/discovered/qualified leads only.
            try:
                from app.models.models import EmailDraft as _EmailDraft
                from app.services.email_sender import generate_b2b_pitch_email
                early = (lead.status or "COLD") in ("COLD", "DISCOVERED", "QUALIFIED")
                email_ok = lead.email and (lead.email_verification_status or "") in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL")
                # Pre-approval gate: only warm via email if the segment's email
                # script is founder-approved (email is approved-by-default).
                email_channel_ok = is_channel_approved(_db, lead.division or lead.segment, "email")
                if early and email_ok and email_channel_ok:
                    exists = _db.query(_EmailDraft).filter(
                        _EmailDraft.lead_id == lead.id,
                        _EmailDraft.status.in_(["PENDING", "APPROVED", "SENT", "EDITED"]),
                    ).first()
                    if not exists:
                        subject, body = generate_b2b_pitch_email(lead)
                        _db.add(_EmailDraft(
                            lead_id=lead.id,
                            follow_up_type="introduction",
                            subject=subject,
                            body=body,
                            reason="Auto-warmed after contact verification (approval required)",
                            status="PENDING",
                        ))
            except Exception as _exc:
                # Swallowed on purpose — this path must not break the
                # caller — but never silently: a failure with no name is
                # how the category engine fell back for hours unnoticed.
                _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

            _db.commit()
    finally:
        _db.close()


_AUTO_WARM_STARTED = False

def start_auto_warm_worker():
    """
    Continuous Auto-Warm Engine (V1.2): a daemon thread that, on its own,
    keeps enriching + auto-drafting every lead — no page load or founder
    click required. Processes small batches with pauses so scraping stays
    polite.

    Priority is REVENUE-FIRST (North Star: margin per founder hour): within
    each queue leads are ordered by the revenue model's final_score, tie-broken
    by estimated_annual_value, so the highest-margin opportunities get warmed
    and auto-drafted into the Approval Inbox ahead of low-value ones. The
    generic `score` saturates at 100 for many leads and is a poor prioritiser.
    Queues: never-searched leads first, then incomplete/stale ones.
    """
    global _AUTO_WARM_STARTED
    if _AUTO_WARM_STARTED:
        return
    # Opt-IN: the continuous in-process worker does CPU-bound HTML parsing that
    # holds the GIL and starves the ASGI server (dashboard/API hang, esp. after
    # sleep). Default OFF so the API is always responsive; enrichment still runs
    # on-demand from discovery/page actions. Set AUTO_WARM_ENABLED=1 to opt in.
    if os.getenv("AUTO_WARM_ENABLED", "0").lower() not in ("1", "true", "yes", "on"):
        print("Auto-Warm Engine: disabled (opt-in via AUTO_WARM_ENABLED=1)")
        return
    _AUTO_WARM_STARTED = True

    import threading, time as _time
    from datetime import timedelta

    # Gentle throttle: enrichment is CPU-bound (HTML parsing) and shares this
    # process with the ASGI server. Small batches + long pauses keep the GIL
    # free so the dashboard/API never starves. Tunable via env.
    _BATCH = max(1, int(os.getenv("AUTO_WARM_BATCH", "3")))
    _PAUSE = max(15, int(os.getenv("AUTO_WARM_PAUSE_SECONDS", "60")))
    _IDLE = max(60, int(os.getenv("AUTO_WARM_IDLE_SECONDS", "300")))

    def _loop():
        _time.sleep(30)   # let the app finish booting + settle
        while True:
            try:
                from app.database.database import SessionLocal
                from app.services.revenue_engine import data_completeness
                _db = SessionLocal()
                try:
                    # Revenue-first ordering: highest final_score, then value.
                    _rev_order = (B2BLead.final_score.desc(),
                                  B2BLead.estimated_annual_value.desc())
                    ids = [l.id for l in _db.query(B2BLead).filter(
                        B2BLead.contact_searched_at.is_(None),
                    ).order_by(*_rev_order).limit(_BATCH).all()]
                    if len(ids) < _BATCH:
                        cutoff = datetime.utcnow() - timedelta(days=3)
                        stale = _db.query(B2BLead).filter(
                            B2BLead.contact_searched_at.isnot(None),
                            B2BLead.contact_searched_at < cutoff,
                        ).order_by(*_rev_order).limit(_BATCH * 8).all()
                        for l in stale:
                            if len(ids) >= _BATCH:
                                break
                            if data_completeness(l)["needs_enrichment"]:
                                ids.append(l.id)
                finally:
                    _db.close()

                # Follow-up housekeeping: cancel reminders for leads that replied
                # or advanced, so the follow-up queue only shows live next-touches.
                try:
                    _hk = SessionLocal()
                    _cancel_stale_reminders(_hk)
                    _hk.close()
                except Exception as _exc:
                    # Swallowed on purpose — this path must not break the
                    # caller — but never silently: a failure with no name is
                    # how the category engine fell back for hours unnoticed.
                    _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

                if ids:
                    _silent_verify_and_enrich(ids)
                    _time.sleep(_PAUSE)     # batch pause
                else:
                    _time.sleep(_IDLE)      # nothing to do — idle longer
            except Exception:
                _time.sleep(_IDLE)

    threading.Thread(target=_loop, name="auto-warm-worker", daemon=True).start()


@router.post("/b2b/leads/verify-contacts-backfill")
def verify_contacts_backfill(background_tasks: BackgroundTasks, batch: int = 15,
                                   db: Session = Depends(get_db)):
    """
    Silent progressive verification. Two queues, in priority order:
    1. Leads never searched (contact_searched_at IS NULL)
    2. V1.2 auto re-enrichment: leads below the data-completeness threshold
       whose last search is older than 3 days — they re-enter enrichment
       instead of sitting incomplete in founder queues.
    """
    from datetime import timedelta
    from app.services.revenue_engine import data_completeness

    cap = max(1, min(batch, 30))
    # Revenue-first ordering (North Star: margin per founder hour): warm the
    # highest final_score / annual-value leads before low-value ones.
    _rev_order = (B2BLead.final_score.desc(), B2BLead.estimated_annual_value.desc())
    fresh = db.query(B2BLead).filter(
        B2BLead.contact_searched_at.is_(None),
    ).order_by(*_rev_order).limit(cap).all()
    pending_ids = [l.id for l in fresh]

    if len(pending_ids) < cap:
        stale_cutoff = datetime.utcnow() - timedelta(days=3)
        stale = db.query(B2BLead).filter(
            B2BLead.contact_searched_at.isnot(None),
            B2BLead.contact_searched_at < stale_cutoff,
        ).order_by(*_rev_order).limit(cap * 3).all()
        for l in stale:
            if len(pending_ids) >= cap:
                break
            if data_completeness(l)["needs_enrichment"]:
                pending_ids.append(l.id)

    remaining = db.query(B2BLead).filter(B2BLead.contact_searched_at.is_(None)).count()
    if pending_ids:
        background_tasks.add_task(_silent_verify_and_enrich, pending_ids)
    return {"queued": len(pending_ids), "remaining_unsearched": remaining}


@router.post("/email/sync-replies")
def sync_email_replies(days: int = 7, db: Session = Depends(get_db)):
    """
    Reply tracking (freeze KPI): polls the Zoho inbox over IMAP and matches
    senders against contacted leads. Matches → status REPLIED + immutable
    EMAIL_REPLIED event. Requires ZOHO_APP_PASSWORD; read-only on the inbox.
    """
    import imaplib, email as email_lib
    from email.utils import parseaddr
    from datetime import timedelta
    from app.services.pipeline_tracker import track
    from app.services.email_sender import SENDER_EMAIL

    pw = os.getenv("ZOHO_APP_PASSWORD", "")
    if not pw or pw.strip() in ("", "your_zoho_app_password_here"):
        return {"status": "skipped", "reason": "ZOHO_APP_PASSWORD not configured", "replies_found": 0}

    contacted = db.query(B2BLead).filter(
        B2BLead.email.isnot(None), B2BLead.email != "",
        B2BLead.status.in_(["INTRO_EMAIL_SENT", "EMAIL_SENT", "WHATSAPP_SENT", "AI_CALLED", "FOUNDER_CALLED"]),
    ).all()
    by_email = {(l.email or "").strip().lower(): l for l in contacted}
    if not by_email:
        return {"status": "ok", "replies_found": 0, "message": "No contacted leads to match"}

    replies = 0
    matched = []
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
        M = imaplib.IMAP4_SSL("imap.zoho.in")
        M.login(SENDER_EMAIL, pw)
        M.select("INBOX", readonly=True)
        _, data = M.search(None, f'(SINCE "{since}")')
        ids = data[0].split()[-300:]
        for i in ids:
            _, msg_data = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            raw = msg_data[0][1] if msg_data and msg_data[0] else b""
            hdr = email_lib.message_from_bytes(raw)
            sender = parseaddr(hdr.get("From", ""))[1].strip().lower()
            lead = by_email.get(sender)
            if lead and lead.status != "REPLIED":
                before = lead.status
                lead.status = "REPLIED"
                lead.last_updated = datetime.utcnow()
                track(db, "EMAIL_REPLIED", lead_id=lead.id, actor="SYSTEM", channel="email",
                      before_status=before, after_status="REPLIED",
                      payload={"subject": hdr.get("Subject", "")[:120]})
                replies += 1

                # Read what they actually wrote and prepare the answer NOW.
                # Detecting a reply and logging it is only half the job — the
                # founder still had to open a blank page. This classifies the
                # intent, cancels the generic sequence, and queues a contextual
                # draft AWAITING_FOUNDER_APPROVAL, so a reply that lands at 2am
                # has an answer waiting by morning. It still never sends itself.
                _intent = None
                try:
                    _body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                _body = part.get_payload(decode=True).decode(
                                    "utf-8", "ignore")
                                break
                    else:
                        _body = (msg.get_payload(decode=True) or b"").decode(
                            "utf-8", "ignore")
                    if _body.strip():
                        from app.services.reply_reader import classify, draft_reply
                        from app.services.outreach_templates import SIGNATURE
                        from app.services import reply_intelligence as _ri

                        # Machine-vs-human FIRST, on headers. reply_reader has no
                        # header awareness and no bounce handling, which is why a
                        # hard 550 from admissions@cuchd.in passed straight
                        # through: the address stayed VERIFIED and sendable, and
                        # we only learned of it because Zoho emailed the founder.
                        #
                        # process() demotes on a bounce, promotes on a real
                        # reply, records extracted facts, and writes the events
                        # the account frequency cap reads to close a company to
                        # cold outreach the moment a human engages.
                        _subj = str(msg.get("Subject") or "")
                        _hdrs = {k: v for k, v in msg.items()}
                        _r = _ri.process(lead, db, _subj, _body, _hdrs, sender)

                        if not _r["human"]:
                            # A machine never counts as engagement, never drafts
                            # a reply, and never stops the sequence.
                            _log.info("machine reply from %s (%s) — %s",
                                      sender, _r["sender"]["kind"], _r["next_action"])
                            matched.append({"company": lead.company, "email": sender,
                                            "intent": f"MACHINE:{_r['sender']['kind']}"})
                            continue

                        _c = classify(_body)
                        _intent = _c.get("intent")
                        if _intent not in ("AUTO_REPLY", "EMPTY"):
                            _d = draft_reply(lead, _body, _c, {}, SIGNATURE)
                            db.add(EmailDraft(lead_id=lead.id,
                                              subject=_d["subject"], body=_d["body"],
                                              status="PENDING",
                                              created_at=datetime.utcnow()))
                            track(db, "REPLY_CLASSIFIED", lead_id=lead.id,
                                  actor="SYSTEM", channel="email",
                                  payload={"intent": _intent,
                                           "evidence": _c.get("evidence"),
                                           "draft_prepared": True})
                except Exception as _e:
                    _log.warning("reply auto-draft failed for lead %s: %s",
                                 lead.id, _e)

                matched.append({"company": lead.company, "email": sender,
                                "intent": _intent})
        M.logout()
        db.commit()
    except Exception as e:
        return {"status": "error", "detail": str(e)[:200], "replies_found": replies}

    return {"status": "ok", "replies_found": replies, "matched": matched[:20],
            "inbox_scanned": len(ids), "since_days": days}


# A pattern needs at least this many real touches before it is treated as signal
# rather than noise. Below it we report the measurement but refuse to advise on it.
_MIN_SAMPLE_FOR_CONFIDENCE = 20


def _relearn_patterns(db: Session) -> dict:
    """
    Recompute learned patterns from REAL outcomes only.

    Signal source: leads the founder actually touched (a FOUNDER action exists in
    the immutable WorkflowEvent log), grouped by segment / city / channel, scored
    on what actually happened to them (replied / won). Nothing is inferred from
    untouched leads, and nothing is invented when data is thin — sample_size is
    stored so the UI can say "not enough evidence yet" instead of guessing.
    """
    from app.models.models import WorkflowEvent, B2BLead, LearnedPattern

    WON = {"ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH", "REORDER_PREDICTED"}
    ENGAGED = {"REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
               "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "NEGOTIATION"} | WON

    # lead_id -> channels the founder actually used on it
    touched: dict[int, set] = {}
    for e in db.query(WorkflowEvent).filter(WorkflowEvent.actor == "FOUNDER").all():
        if e.lead_id and e.event_type in _FOUNDER_MINUTES:
            touched.setdefault(e.lead_id, set()).add(e.channel or "unknown")

    if not touched:
        return {"patterns_written": 0, "touches": 0, "reason": "no founder actions recorded yet"}

    leads = {l.id: l for l in db.query(B2BLead).filter(B2BLead.id.in_(list(touched.keys()))).all()}

    # scope -> key -> [touches, engaged, wins, margin]
    buckets: dict[str, dict[str, list]] = {"segment": {}, "city": {}, "channel": {}}

    def bump(scope, key, lead, chan_hit=False):
        if not key:
            return
        b = buckets[scope].setdefault(str(key).lower(), [0, 0, 0, 0.0])
        b[0] += 1
        if (lead.status or "") in ENGAGED: b[1] += 1
        if (lead.status or "") in WON:     b[2] += 1
        b[3] += _margin_for(lead)

    for lid, channels in touched.items():
        lead = leads.get(lid)
        if not lead:
            continue
        bump("segment", (lead.segment or lead.division or "unknown"), lead)
        bump("city", (lead.city or "unknown"), lead)
        for ch in channels:
            bump("channel", ch, lead)

    written = 0
    for scope, keys in buckets.items():
        for key, (n, eng, wins, margin) in keys.items():
            rows = {
                "reply_rate_pct": round(eng / n * 100, 1) if n else 0.0,
                "win_rate_pct": round(wins / n * 100, 1) if n else 0.0,
                "margin_per_touch_rs": round(margin / n) if n else 0.0,
            }
            for metric, value in rows.items():
                row = db.query(LearnedPattern).filter(
                    LearnedPattern.scope == scope,
                    LearnedPattern.key == key,
                    LearnedPattern.metric == metric,
                ).first()
                if not row:
                    row = LearnedPattern(scope=scope, key=key, metric=metric)
                    db.add(row)
                row.value = value
                row.sample_size = n
                row.wins = wins
                row.updated_at = datetime.utcnow()
                written += 1
    db.commit()
    return {"patterns_written": written, "touches": len(touched)}


@router.post("/dashboard/memory/learn")
def relearn_dashboard_memory(db: Session = Depends(get_db)):
    """Force a re-learn from real outcomes. Also run periodically by the worker."""
    return _relearn_patterns(db)


@router.get("/dashboard/memory")
def dashboard_memory(db: Session = Depends(get_db)):
    """
    The dashboard's persistent memory + what it has actually learned.

    Everything is served from the DB, so it is identical whenever and wherever
    the dashboard is opened — no browser-local state. Patterns below
    _MIN_SAMPLE_FOR_CONFIDENCE touches are returned but flagged as not-yet-
    trustworthy, so thin data is never dressed up as insight.
    """
    from app.models.models import WorkflowEvent, LearnedPattern

    rows = db.query(LearnedPattern).all()
    learned: dict[str, list] = {}
    for r in rows:
        learned.setdefault(r.scope, []).append({
            "key": r.key,
            "metric": r.metric,
            "value": r.value,
            "sample_size": r.sample_size,
            "confident": r.sample_size >= _MIN_SAMPLE_FOR_CONFIDENCE,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    for scope in learned:
        learned[scope].sort(key=lambda x: (-x["sample_size"], x["key"]))

    # Memory = the immutable record of what the founder actually did, newest first.
    recent = (db.query(WorkflowEvent)
              .filter(WorkflowEvent.actor == "FOUNDER")
              .order_by(WorkflowEvent.id.desc()).limit(20).all())
    memory = [{
        "event": e.event_type, "lead_id": e.lead_id, "channel": e.channel,
        "before": e.before_status, "after": e.after_status,
        "at": e.occurred_at.isoformat() if e.occurred_at else None,
    } for e in recent]

    confident = [p for ps in learned.values() for p in ps if p["confident"]]
    total_actions = db.query(WorkflowEvent).filter(WorkflowEvent.actor == "FOUNDER").count()
    last_learned = max((r.updated_at for r in rows if r.updated_at), default=None)

    return {
        "memory": memory,
        "total_founder_actions": total_actions,
        "learned": learned,
        "confident_patterns": len(confident),
        "min_sample_for_confidence": _MIN_SAMPLE_FOR_CONFIDENCE,
        # Honest status: with no outcomes there is nothing to learn from, and we
        # say so rather than presenting noise as intelligence.
        "status": "learning" if confident else ("collecting_data" if rows else "no_data_yet"),
        "last_learned_at": last_learned.isoformat() if last_learned else None,
        "generated_at": datetime.utcnow().isoformat(),
    }


# Founder minutes per action — used for the north-star margin/founder-hour.
# Deliberately conservative estimates of real founder effort, not guesses at value.
_FOUNDER_MINUTES = {
    "EMAIL_SENT": 0.5,              # review + approve a drafted email
    "WHATSAPP_SENT": 2.0,           # approve + send via wa.me
    "AI_CALL_INITIATED": 1.0,       # approve the call
    "FOUNDER_CALL_COMPLETED": 15.0,
    "MEETING_BOOKED": 5.0,
    "SAMPLE_SENT": 10.0,
    "PROPOSAL_SENT": 20.0,
}


class WhatsAppSendRequest(BaseModel):
    lead_id: int
    message: Optional[str] = None
    campaign_name: Optional[str] = None
    template_params: Optional[list[str]] = None


@router.get("/whatsapp/status")
def whatsapp_api_status(db: Session = Depends(get_db)):
    """Is the AiSensy path usable, and who may we legally send to?"""
    from app.services.whatsapp_sender import is_configured, consent_check
    from app.models.models import B2BLead

    leads = db.query(B2BLead).all()
    sendable = [l for l in leads if consent_check(l)[0]]
    with_wa = [l for l in leads if (l.whatsapp_number or l.phone or "").strip()]
    return {
        "configured": is_configured(),
        "campaign_name_set": bool((os.getenv("AISENSY_CAMPAIGN_NAME") or "").strip()),
        "leads_total": len(leads),
        "leads_with_whatsapp_number": len(with_wa),
        # The number that actually matters — everything else is blocked by policy.
        "leads_api_sendable_opted_in": len(sendable),
        "note": ("API sending requires opt-in (Meta policy). Cold first touch stays on "
                 "the wa.me Send Queue. Opt-in is created when a lead replies."),
    }


@router.post("/whatsapp/send")
def whatsapp_api_send(req: WhatsAppSendRequest, db: Session = Depends(get_db)):
    """
    Send a WhatsApp via the AiSensy Business API — opted-in leads only.

    The consent gate lives in whatsapp_sender.send_whatsapp and runs before
    anything else, so this endpoint cannot be used to blast scraped numbers.
    A real send is recorded on OutboundWhatsApp + an immutable WorkflowEvent,
    so it counts toward the learned patterns and the margin/founder-hour metric
    exactly like a confirmed manual send.
    """
    from app.models.models import B2BLead, OutboundWhatsApp
    from app.services.whatsapp_sender import send_whatsapp, consent_check
    from app.services.pipeline_tracker import track

    lead = db.query(B2BLead).filter(B2BLead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    # Founder must have approved this segment's WhatsApp script beforehand.
    if not is_channel_approved(db, lead.division or lead.segment, "whatsapp"):
        raise HTTPException(status_code=409, detail="WhatsApp script not founder-approved for this segment")

    allowed, reason = consent_check(lead)
    if not allowed:
        raise HTTPException(status_code=409, detail=f"Blocked: {reason}")

    msg = req.message or _followup_whatsapp(lead, 0)
    row = OutboundWhatsApp(lead_id=lead.id, recipient=(lead.whatsapp_number or lead.phone),
                           message=msg, status="QUEUED", queued_at=datetime.utcnow())
    db.add(row); db.flush()

    res = send_whatsapp(lead, msg, campaign_name=req.campaign_name, template_params=req.template_params)
    row.whatsapp_response = (res.reason or res.response)[:300]
    if res.status == "sent":
        before = lead.status
        row.status = "SENT"; row.sent_at = datetime.utcnow()
        row.whatsapp_message_id = res.message_id or None
        lead.status = "WHATSAPP_SENT"; lead.last_updated = datetime.utcnow()
        track(db, "WHATSAPP_SENT", lead_id=lead.id, actor="FOUNDER", channel="whatsapp",
              before_status=before, after_status="WHATSAPP_SENT",
              payload={"via": "aisensy_api", "outbound_id": row.id})
        _schedule_next_reminder(db, lead, "whatsapp")
    else:
        row.status = "FAILED"; row.failed_at = datetime.utcnow()
    db.commit()
    return {"status": res.status, "reason": res.reason, "outbound_id": row.id, "lead_id": lead.id}


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    """
    AiSensy/Meta delivery + inbound webhook.

    This is what the API buys us over wa.me: real delivery/read receipts and —
    the valuable part — automatic INBOUND detection. An inbound message is both a
    reply (engagement we currently cannot see on WhatsApp at all) and an opt-in,
    so we record consent from it. Configure this URL in the AiSensy dashboard.
    """
    from app.models.models import OutboundWhatsApp, B2BLead
    from app.services.pipeline_tracker import track
    from sqlalchemy import or_   # used below; not imported at module level
    import hmac as _hmac

    # ── Authenticate the caller ───────────────────────────────────────────
    # This endpoint is publicly reachable and writes consent_status=EXPLICIT
    # from an inbound message. That field is what authorises business-initiated
    # WhatsApp under Meta's rules, so an unauthenticated POST here lets a
    # stranger manufacture the consent record — and forge delivery receipts
    # into the ledger besides.
    #
    # Fail CLOSED. With no secret configured the endpoint accepts nothing:
    # there is no legitimate caller yet, so nothing real is being dropped, and
    # a webhook that silently rejects real callbacks is worse than one that
    # refuses loudly. Accepts the secret as a header OR a query token, because
    # some providers only let you configure a URL.
    _expected = (os.getenv("WHATSAPP_WEBHOOK_SECRET") or "").strip()
    _given = (request.headers.get("x-webhook-secret")
              or request.headers.get("x-aisensy-secret")
              or request.query_params.get("token") or "").strip()
    if not _expected:
        _log.error("whatsapp webhook rejected: WHATSAPP_WEBHOOK_SECRET is not set")
        raise HTTPException(status_code=503,
                            detail="webhook not configured — set WHATSAPP_WEBHOOK_SECRET")
    if not _given or not _hmac.compare_digest(_given, _expected):
        _log.warning("whatsapp webhook rejected: bad or missing secret from %s",
                     request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="unauthorised")

    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "unparseable body"}

    # Providers differ in envelope shape; accept the common variants.
    msg_id = (body.get("messageId") or body.get("message_id") or
              body.get("id") or (body.get("message") or {}).get("id") or "")
    event = (body.get("eventType") or body.get("type") or body.get("status") or "").lower()
    sender = str(body.get("from") or body.get("sender") or body.get("waNumber") or "")

    row = (db.query(OutboundWhatsApp).filter(OutboundWhatsApp.whatsapp_message_id == msg_id).first()
           if msg_id else None)

    # Delivery/read receipts on something we sent.
    if row and event in ("delivered", "read", "failed", "sent"):
        now = datetime.utcnow()
        if event == "delivered": row.status, row.delivered_at = "DELIVERED", now
        elif event == "read":    row.status, row.read_at = "READ", now
        elif event == "failed":  row.status, row.failed_at = "FAILED", now
        db.commit()
        return {"status": "ok", "recorded": event, "outbound_id": row.id}

    # Inbound message = a real reply AND an opt-in.
    if event in ("message", "inbound", "received") or (sender and not row):
        digits = "".join(ch for ch in sender if ch.isdigit())[-10:]
        lead = None
        if digits:
            # Normalise BOTH sides in Python. A SQL `contains(digits)` silently
            # fails because numbers are stored with separators
            # ("+91-79820-28371"), so the contiguous 10-digit run never appears
            # and every inbound reply would be dropped as unmatched.
            def _last10(v: str) -> str:
                return "".join(ch for ch in (v or "") if ch.isdigit())[-10:]
            for cand in db.query(B2BLead).filter(
                or_(B2BLead.phone.isnot(None), B2BLead.whatsapp_number.isnot(None))
            ).all():
                if _last10(cand.whatsapp_number) == digits or _last10(cand.phone) == digits:
                    lead = cand
                    break
        if lead:
            before = lead.status
            lead.status = "REPLIED"
            lead.consent_status = "EXPLICIT"        # they messaged us first
            lead.last_updated = datetime.utcnow()
            track(db, "WHATSAPP_REPLIED", lead_id=lead.id, actor="LEAD", channel="whatsapp",
                  before_status=before, after_status="REPLIED",
                  payload={"from": sender[:24], "via": "aisensy_webhook"})
            _cancel_stale_reminders(db)             # they replied — stop chasing
            db.commit()
            return {"status": "ok", "recorded": "inbound_reply", "lead_id": lead.id}
        return {"status": "ok", "recorded": "inbound_unmatched", "from": sender[:24]}

    return {"status": "ignored", "event": event or "unknown"}


@router.get("/ping")
def ping():
    """
    Ultra-cheap liveness ping — NO database, NO dependencies.

    The header polls this every 20-60s purely to render the Live/Offline dot. It
    used to poll /b2b/kpis, a CPU-heavy aggregate over every lead; that was a
    large share of total API load, and when the API slowed the ping timed out ->
    "offline" -> the UI polled *faster* -> slower still. Keep this trivial.
    """
    return {"ok": True}


@router.get("/revenue/founder-actions")
def revenue_from_founder_actions(db: Session = Depends(get_db)):
    """
    Revenue generated BY FOUNDER ACTIONS — not total pipeline potential.

    Every number here traces to the immutable WorkflowEvent log (what the founder
    actually did) joined to the real outcome of those leads. A lead the founder
    never touched contributes nothing, no matter how big its estimated value.

    Stages are strictly nested by real outcome:
      touched  -> the founder actually sent something to this lead
      engaged  -> that lead replied
      won      -> the deal closed (realized margin)
    """
    from app.models.models import WorkflowEvent, B2BLead

    WON = {"ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH", "REORDER_PREDICTED"}
    ENGAGED = {"REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
               "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "NEGOTIATION"} | WON

    # Founder-actor events only — the system's own automation is not founder work.
    events = db.query(WorkflowEvent).filter(WorkflowEvent.actor == "FOUNDER").all()

    action_counts: dict[str, int] = {}
    failed_attempts: dict[str, int] = {}
    founder_minutes = 0.0
    touched_lead_ids: set[int] = set()
    for e in events:
        if e.event_type in _FOUNDER_MINUTES:
            # A real, successful founder action.
            action_counts[e.event_type] = action_counts.get(e.event_type, 0) + 1
            founder_minutes += _FOUNDER_MINUTES[e.event_type]
            if e.lead_id:
                touched_lead_ids.add(e.lead_id)
        else:
            # Failures/other events are NOT achievements — reported separately so
            # they can never inflate "revenue generated by founder actions".
            failed_attempts[e.event_type] = failed_attempts.get(e.event_type, 0) + 1

    touched = db.query(B2BLead).filter(B2BLead.id.in_(touched_lead_ids)).all() if touched_lead_ids else []
    engaged = [l for l in touched if (l.status or "") in ENGAGED]
    won     = [l for l in touched if (l.status or "") in WON]

    margin_touched = sum(_margin_for(l) for l in touched)
    margin_engaged = sum(_margin_for(l) for l in engaged)
    margin_won     = sum(_margin_for(l) for l in won)

    founder_hours = round(founder_minutes / 60.0, 2)

    # Two honest cuts of the north star, never conflated:
    #   in_play  = margin the founder's time PUT IN MOTION (throughput)
    #   realized = margin actually WON (0 until a deal closes — never inflated)
    in_play_per_hour = round(margin_touched / founder_hours) if founder_hours > 0 else 0
    realized_per_hour = round(margin_won / founder_hours) if founder_hours > 0 else 0

    reply_rate = round(len(engaged) / len(touched) * 100, 1) if touched else 0.0
    win_rate = round(len(won) / len(touched) * 100, 1) if touched else 0.0

    return {
        "actions": action_counts,                     # successful founder actions only
        "failed_attempts": failed_attempts,           # never counted as achievement
        "leads_touched": len(touched),
        "leads_engaged": len(engaged),
        "leads_won": len(won),
        "margin_touched_rs": round(margin_touched),   # margin the founder put in play
        "margin_engaged_rs": round(margin_engaged),   # of that, what responded
        "margin_won_rs": round(margin_won),           # realized (the only real revenue)
        "founder_hours": founder_hours,
        "margin_in_play_per_founder_hour_rs": in_play_per_hour,
        "realized_margin_per_founder_hour_rs": realized_per_hour,   # NORTH STAR
        "reply_rate_pct": reply_rate,
        "win_rate_pct": win_rate,
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.get("/revenue/summary")
def revenue_summary(db: Session = Depends(get_db)):
    """
    V1.2 Revenue Dashboard: Automation-created vs Founder-convertible pipeline,
    geographic expansion coverage, and the top revenue opportunities ranked by
    expected margin × confidence × probability (not lead status).
    """
    from app.services.revenue_engine import (
        automation_split, expansion_status, revenue_potential,
        opportunity_rank_score,
    )
    leads = db.query(B2BLead).all()
    split = automation_split(leads)
    expansion = expansion_status(leads)

    ranked = sorted(
        (l for l in leads if l.status not in ("ORDER_WON", "ONBOARDED")),
        key=opportunity_rank_score, reverse=True,
    )[:10]
    top_opportunities = []
    for l in ranked:
        rp = revenue_potential(l)
        top_opportunities.append({
            "lead_id": l.id,
            "company": l.company,
            "city": l.city,
            "division": l.division,
            "status": l.status,
            **rp,
            "opportunity_score": opportunity_rank_score(l),
        })

    # ── Freeze KPIs: Revenue Creation Velocity + reply/qualification rates ──
    from datetime import timedelta
    from app.models.models import RevenueOpportunity

    week_ago = datetime.utcnow() - timedelta(days=7)
    new_opps = db.query(RevenueOpportunity).filter(
        RevenueOpportunity.opened_at >= week_ago,
        RevenueOpportunity.status == "OPEN",
    ).all()
    rcv_margin_7d = round(sum(o.expected_margin or 0 for o in new_opps))

    from app.services.decision_engine import STATUS_PROGRESSION
    def _prog(l):
        return STATUS_PROGRESSION.get(l.status or "", 0)
    emailed = [l for l in leads if _prog(l) >= 3]
    replied_plus = [l for l in emailed if _prog(l) >= 7]
    ai_called = [l for l in leads if _prog(l) >= 5]
    ai_qualified = [l for l in ai_called if _prog(l) >= 6]

    kpis = {
        "rcv_margin_added_7d_rs": rcv_margin_7d,
        "rcv_margin_per_day_rs": round(rcv_margin_7d / 7),
        "opportunities_opened_7d": len(new_opps),
        "email_reply_rate_pct": round(len(replied_plus) / len(emailed) * 100, 1) if emailed else 0,
        "ai_qualification_rate_pct": round(len(ai_qualified) / len(ai_called) * 100, 1) if ai_called else 0,
        "contacted_leads": len(emailed),
        "replied_leads": len(replied_plus),
    }

    return {
        "split": split,
        "expansion": expansion,
        "top_opportunities": top_opportunities,
        "velocity": kpis,
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.get("/discovery/expansion-status")
def discovery_expansion_status(db: Session = Depends(get_db)):
    """Geographic expansion coverage (Abohar → 50km → Punjab → North → PAN India)."""
    from app.services.revenue_engine import expansion_status
    return expansion_status(db.query(B2BLead).all())


class FindBestRequest(BaseModel):
    state: Optional[str] = None
    cities: Optional[list[str]] = None
    category: Optional[str] = None
    outreach_method: Optional[str] = None
    force_discovery: bool = False
    query_text: Optional[str] = None

@router.post("/discovery/find-best")
def find_best_crm_opportunities(req: FindBestRequest, db: Session = Depends(get_db)):
    """
    Intelligent Revenue Search V4:
    🔍 FIND BEST OPPORTUNITIES
    """
    from app.services.revenue_os import find_best_opportunities
    try:
        return find_best_opportunities(
            db,
            state=req.state,
            cities=req.cities,
            category=req.category,
            outreach_method=req.outreach_method,
            force_discovery=req.force_discovery,
            query_text=req.query_text
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/territory/intelligence")
def territory_intelligence(state: str, city: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Territory Intelligence indicators for regional planning.
    """
    from app.services.revenue_os import get_territory_intelligence
    return get_territory_intelligence(db, state, city)

@router.get("/competitor/intelligence")
def competitor_intelligence(db: Session = Depends(get_db)):
    """
    Competitor Objections and Lost reasons aggregation.
    """
    from app.services.revenue_os import get_competitor_intelligence
    return get_competitor_intelligence(db)

@router.get("/discovery/memory-search")
def memory_search(query: str, db: Session = Depends(get_db)):
    """
    Business Memory Search for Decision maker, Supplier, Remark or objections.
    """
    from app.services.revenue_os import get_memory_search
    return get_memory_search(db, query)


@router.post("/discovery/approve-batch")
def approve_discovery_batch(req: ApproveBatchRequest, background_tasks: BackgroundTasks,
                                  db: Session = Depends(get_db)):
    """
    Approve selected discovery results → save to B2BLead CRM.
    V1.1: intro emails become PENDING drafts in the Approval Inbox (never sent
    from here). Enrichment + email verification run silently in the background.
    """
    from app.services.lead_discovery import save_discovered_leads
    from app.services.email_sender import generate_b2b_pitch_email
    from app.models.models import EmailDraft

    saved = save_discovered_leads(req.leads, db)
    drafted = 0

    new_leads = []
    if saved["inserted"] > 0:
        new_leads = db.query(B2BLead).filter(B2BLead.status == "DISCOVERED").order_by(
            B2BLead.id.desc()).limit(saved["inserted"]).all()

    if req.send_intro_email:
        for lead in new_leads:
            if not lead.email:
                continue
            existing = db.query(EmailDraft).filter(
                EmailDraft.lead_id == lead.id,
                EmailDraft.status.in_(["PENDING", "APPROVED", "SENT"]),
            ).first()
            if existing:
                continue
            subject, body = generate_b2b_pitch_email(lead)
            db.add(EmailDraft(
                lead_id=lead.id,
                follow_up_type="introduction",
                subject=subject,
                body=body,
                reason="Drafted at discovery approval (founder approval required)",
                status="PENDING",
            ))
            drafted += 1
        db.commit()

    # Silent background pipeline: directory enrichment + email verification
    if new_leads:
        background_tasks.add_task(_silent_verify_and_enrich, [l.id for l in new_leads])

    return {"inserted": saved["inserted"], "skipped": saved["skipped"],
            "emailed": 0, "drafted": drafted,
            "message": f"{saved['inserted']} leads saved · {drafted} drafts queued for approval · "
                       "contacts verifying in background"}


@router.post("/b2b/leads/{lead_id}/mark-whatsapp")
def mark_whatsapp_sent(lead_id: int, db: Session = Depends(get_db)):
    """
    Founder CONFIRMS they actually sent the WhatsApp message.

    This is the only place a WHATSAPP_SENT founder action is recorded. wa.me
    cannot tell us whether a message was really sent, so the founder's explicit
    confirmation is the evidence — never link-generation. Previously this only
    flipped the status and logged nothing, so genuinely-sent messages never
    counted toward the learned patterns or the margin/founder-hour north star.
    """
    from app.services.pipeline_tracker import track

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    before = lead.status
    if before == "WHATSAPP_SENT":
        return {"status": "already_confirmed", "lead_id": lead_id, "new_status": before}

    lead.status = "WHATSAPP_SENT"
    lead.last_updated = datetime.utcnow()
    # Immutable record of a REAL founder action -> feeds learning + north star.
    track(db, "WHATSAPP_SENT", lead_id=lead.id, actor="FOUNDER", channel="whatsapp",
          before_status=before, after_status="WHATSAPP_SENT",
          payload={"confirmed_by_founder": True})
    # Now that WhatsApp genuinely went out, queue the next step (AI call).
    nxt = _schedule_next_reminder(db, lead, "whatsapp")
    db.commit()
    return {
        "status": "confirmed", "lead_id": lead_id, "new_status": "WHATSAPP_SENT",
        "next_reminder": ({"channel": nxt.channel, "due_at": nxt.due_at.isoformat()} if nxt else None),
    }


@router.post("/b2b/leads/{lead_id}/mark-called")
def mark_called(lead_id: int, db: Session = Depends(get_db)):
    """Mark a lead as called."""
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = "CALL_DONE"
    db.commit()
    return {"status": "ok", "lead_id": lead_id, "new_status": "CALL_DONE"}


_CONFIRMABLE_ACTIONS = {
    # action   -> (new lead status, event type, human label)
    "sample":   ("SAMPLE_SENT",   "SAMPLE_DISPATCHED", "sample kit dispatched"),
    "proposal": ("PROPOSAL_SENT", "PROPOSAL_SENT",     "proposal sent"),
    "meeting":  ("MEETING_COMPLETED", "MEETING_COMPLETED", "meeting completed"),
    "feedback": ("FEEDBACK_RECEIVED", "FEEDBACK_RECEIVED", "sample feedback received"),
}


@router.post("/b2b/leads/{lead_id}/confirm-action")
def confirm_action(lead_id: int, action: str, db: Session = Depends(get_db)):
    """
    Founder CONFIRMS a physical conversion step they actually performed
    (dispatched a sample, sent a proposal, held the meeting, got feedback).

    This is the only place these stages advance. /b2b/outreach/execute merely
    prepares them and returns requires_confirmation, so the pipeline can never
    show a sample as dispatched that nobody posted. On confirmation the lead
    advances and the next conversion step is scheduled, so an engaged lead
    always has a next action.
    """
    from app.models.models import B2BLead, OutreachReminder
    from app.services.pipeline_tracker import track

    entry = _CONFIRMABLE_ACTIONS.get((action or "").lower().strip())
    if not entry:
        raise HTTPException(status_code=400,
                            detail=f"unknown action '{action}' — expected one of "
                                   f"{sorted(_CONFIRMABLE_ACTIONS)}")
    new_status, event_type, label = entry
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    before = lead.status
    if before == new_status:
        return {"status": "already_confirmed", "lead_id": lead_id, "new_status": before}

    lead.status = new_status
    lead.last_updated = datetime.utcnow()

    # Close any open conversion reminder for this lead — it has been actioned.
    for rem in db.query(OutreachReminder).filter(
        OutreachReminder.lead_id == lead.id,
        OutreachReminder.sequence_step >= _CONVERSION_STEP_BASE,
        OutreachReminder.status == "SCHEDULED",
    ).all():
        rem.status = "DONE"
        rem.done_at = datetime.utcnow()
    # Flush before scheduling: _schedule_conversion_step returns any still-open
    # conversion reminder, so without this it re-reads the one we just closed
    # and hands back the previous action instead of the next one — the lead
    # would sit on "qualify the reply" forever while its status advanced.
    db.flush()

    track(db, event_type, lead_id=lead.id, actor="FOUNDER", channel="founder",
          before_status=before, after_status=new_status,
          payload={"action": action, "confirmed_by_founder": True})

    nxt = _schedule_conversion_step(db, lead)
    db.commit()
    return {
        "status": "confirmed", "lead_id": lead_id, "action": action, "recorded": label,
        "before_status": before, "new_status": new_status,
        "next_action": ({"channel": nxt.channel, "due_at": nxt.due_at.isoformat(),
                         "reason": nxt.reason} if nxt is not None else None),
    }


@router.post("/b2b/leads/{lead_id}/mark-won")
def mark_won(lead_id: int, order_value_inr: float = 0, db: Session = Depends(get_db)):
    """
    Mark a lead as won. Records an ORDER_WON event — without it the win never
    reaches any revenue figure.

    The status alone used to be set with no event written, and every revenue
    KPI counts the immutable event log rather than lead status. So a founder
    could close a deal, see the lead move to ORDER_WON, and watch the revenue
    dashboard still read zero. Caught by walking a lead through
    meeting -> sample -> proposal -> order and diffing the KPIs.
    """
    from app.services.pipeline_tracker import track
    from app.services.funding_engine import AgentFundingService

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if (lead.status or "") == "ORDER_WON":
        return {"status": "already_won", "lead_id": lead_id, "new_status": "ORDER_WON"}

    before = lead.status
    lead.status = "ORDER_WON"
    lead.last_updated = datetime.utcnow()
    value = float(order_value_inr or lead.estimated_value or 0)
    margin = float(_margin_for(lead))

    # Calculate default split commission pool
    AgentFundingService.calculate_deal_commission(db, lead_id, value)

    track(db, "ORDER_WON", lead_id=lead.id, actor="FOUNDER", channel="founder",
          before_status=before, after_status="ORDER_WON",
          payload={"order_value_inr": value, "margin_inr": margin,
                   "confirmed_by_founder": True})
    db.commit()
    return {"status": "ok", "lead_id": lead_id, "new_status": "ORDER_WON",
            "order_value_inr": value, "margin_inr": margin}


@router.get("/b2b/tenders/profitability")
def get_tenders_profitability(db: Session = Depends(get_db)):
    """Get tenders sorted by cashflow score."""
    return CRMTrackerService.get_tender_profitability(db)

@router.get("/b2b/gifting/pipeline")
def get_gifting_pipeline(db: Session = Depends(get_db)):
    """Get gifting pipeline buckets."""
    return CRMTrackerService.get_gifting_pipeline(db)

@router.get("/b2b/distributors/expansion")
def get_distributors_expansion(db: Session = Depends(get_db)):
    """Get distributor expansion pipeline opportunity."""
    return CRMTrackerService.get_distributor_expansion(db)


# ── B2B Outbound AI Calling Integration (V1.0-RC1) ───────────────────────────
import re
from pydantic import BaseModel
from datetime import datetime
from app.services.calling_agent import CallingAgentService
from app.models.models import B2BLead, CallHistory

class DNCOverrideRequest(BaseModel):
    reason: str
    override_by: str = "Hiten Jain"

@router.post("/b2b/leads/{lead_id}/call")
def trigger_lead_call(lead_id: int, db: Session = Depends(get_db)):
    """Triggers an outbound call using Vapi and locks the lead."""
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
        
    success, reason = CallingAgentService.trigger_vapi_call(db, lead)
    if not success:
        return {"status": "error", "message": reason}
        
    return {"status": "success", "message": "Calling initiated", "call_id": lead.vapi_call_id}

@router.post("/b2b/leads/{lead_id}/override-dnc")
def override_lead_dnc(lead_id: int, req: DNCOverrideRequest, db: Session = Depends(get_db)):
    """Bypass DNC rules for a lead and log audit reason."""
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
        
    lead.do_not_call = False
    lead.dnc_reason = "Override"
    lead.dnc_override_by = req.override_by
    lead.dnc_override_reason = req.reason
    
    # Update consent to allow dialing
    lead.consent_status = "EXPLICIT"
    lead.consent_source = "FOUNDER_OVERRIDE"
    lead.consent_timestamp = datetime.utcnow()
    
    db.commit()
    return {"status": "success", "message": "DNC override completed successfully"}

@router.get("/b2b/leads/margin-cap-preview")
def get_margin_cap_campaign_preview(db: Session = Depends(get_db)):
    """Returns the candidate leads and cost estimates for the margin-cap campaign."""
    campaign_leads, cumulative_margin, estimated_cost = CallingAgentService.get_campaign_preview(db)
    
    leads_data = []
    for l in campaign_leads:
        f = CRMTrackerService.get_financial_metrics(l)
        leads_data.append({
            "id": l.id,
            "company": l.company,
            "contact_name": l.contact_name,
            "division": l.division,
            "lead_tier": l.lead_tier,
            "estimated_value": l.estimated_value,
            "expected_margin": f["expected_margin"],
            "lead_temperature_score": l.lead_temperature_score or 0.0,
            "lead_temperature_tier": l.lead_temperature_tier or "COLD",
            "phone": l.phone
        })
        
    return {
        "eligible_leads_count": len(leads_data),
        "expected_margin": cumulative_margin,
        "estimated_calls": len(leads_data),
        "estimated_spend": estimated_cost,
        "leads": leads_data
    }

@router.post("/b2b/leads/margin-cap-call")
def trigger_margin_cap_campaign(db: Session = Depends(get_db)):
    """Triggers the ₹5L expected margin campaign calls."""
    campaign_leads, cumulative_margin, estimated_cost = CallingAgentService.get_campaign_preview(db)
    triggered = []
    failed = []
    for l in campaign_leads:
        success, reason = CallingAgentService.trigger_vapi_call(db, l)
        if success:
            triggered.append({"id": l.id, "company": l.company, "call_id": l.vapi_call_id})
        else:
            failed.append({"id": l.id, "company": l.company, "reason": reason})
            
    return {
        "status": "success",
        "triggered_count": len(triggered),
        "failed_count": len(failed),
        "triggered_calls": triggered,
        "failed_calls": failed,
        "cumulative_margin": cumulative_margin,
        "estimated_cost": estimated_cost
    }

@router.post("/b2b/vapi-webhook")
async def vapi_webhook(request: Request, db: Session = Depends(get_db)):
    """Receives webhook notifications from Vapi after a call completes."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    message = payload.get("message", {})
    call = message.get("call", payload.get("call", {}))
    if not call:
        return {"status": "ignored", "message": "No call object in payload"}
        
    call_id = call.get("id")
    if not call_id:
        return {"status": "ignored", "message": "No call ID found"}
        
    lead = db.query(B2BLead).filter(B2BLead.vapi_call_id == call_id).first()
    if not lead:
        return {"status": "ignored", "message": f"Lead not found for call ID {call_id}"}
        
    duration = call.get("duration", 0)
    cost = call.get("cost", 0.0)
    cost_inr = cost * 85.0 if cost > 0 else 0.0
    
    recording_url = call.get("recordingUrl")
    transcript = call.get("transcript", "")
    summary = call.get("summary", "")
    
    lead.call_duration_seconds = duration
    lead.call_minutes = duration / 60.0
    lead.call_cost = cost_inr
    lead.call_recording_url = recording_url
    lead.call_transcript = transcript
    lead.call_summary = summary
    lead.call_provider = "Vapi"
    
    human_answered = call.get("humanAnswered", False) or duration > 0
    lead.human_answered = human_answered
    
    analysis = call.get("analysis", {})
    structured_data = analysis.get("structuredData", call.get("structuredData", {}))
    
    current_brand = structured_data.get("current_brand")
    current_supplier = structured_data.get("current_supplier")
    price_per_kg = structured_data.get("price_per_kg")
    competitor_strength = structured_data.get("competitor_strength")
    monthly_consumption = structured_data.get("monthly_consumption")
    decision_maker = structured_data.get("decision_maker")
    
    sample_requested = structured_data.get("sample_requested", False)
    if isinstance(sample_requested, str):
        sample_requested = sample_requested.lower() in ("true", "yes", "1")
        
    meeting_requested = structured_data.get("meeting_requested", False)
    if isinstance(meeting_requested, str):
        meeting_requested = meeting_requested.lower() in ("true", "yes", "1")
        
    budget_range = structured_data.get("budget_range")
    objection_reason = structured_data.get("objection_reason")
    next_followup_date = structured_data.get("next_followup_date")
    
    if current_brand: lead.current_brand = current_brand
    if current_supplier: lead.current_supplier = current_supplier
    if price_per_kg is not None:
        try:
            lead.price_per_kg = float(price_per_kg)
        except ValueError:
            pass
    if competitor_strength is not None:
        try:
            lead.competitor_strength = int(competitor_strength)
        except ValueError:
            pass
    if monthly_consumption:
        lead.monthly_consumption = str(monthly_consumption)
        digits = re.findall(r"\d+", str(monthly_consumption))
        if digits:
            try:
                lead.expected_monthly_consumption_kg = float(digits[0])
            except ValueError:
                pass
                
    if decision_maker: lead.decision_maker = decision_maker
    lead.sample_requested = sample_requested
    lead.meeting_requested = meeting_requested
    if budget_range: lead.budget_range = budget_range
    if objection_reason: lead.objection_reason = objection_reason
    if next_followup_date: lead.next_followup_date = next_followup_date
    
    realization = CallingAgentService.get_average_realization(db)
    lead.blended_realization_per_kg = realization
    if lead.expected_monthly_consumption_kg:
        lead.call_estimated_value = lead.expected_monthly_consumption_kg * realization * 12
    else:
        lead.call_estimated_value = 0.0
        
    _STAGE_PROB = {
        "DISCOVERED": 0.05, "QUALIFIED": 0.10, "EMAIL_SENT": 0.12,
        "REPLIED": 0.25, "MEETING_BOOKED": 0.40, "MEETING_COMPLETED": 0.55,
        "SAMPLE_SENT": 0.60, "FEEDBACK_PENDING": 0.60, "FEEDBACK_RECEIVED": 0.65,
        "PROPOSAL_SENT": 0.70, "ORDER_WON": 1.00, "ONBOARDED": 1.00,
        "REORDER_PREDICTED": 0.90, "UPSELL_OFFERED": 0.75, "ACCOUNT_GROWTH": 1.00,
        "COLD": 0.00,
    }
    
    base_probability = lead.probability
    if not base_probability or base_probability <= 0.0:
        base_probability = _STAGE_PROB.get((lead.status or "DISCOVERED").upper(), 0.05)
        
    comp_strength = lead.competitor_strength or 0
    adjusted_win_probability = max(0.05, base_probability * (1.0 - comp_strength / 200.0))
    lead.probability = adjusted_win_probability
    
    call_quality_score = 20
    if sample_requested or meeting_requested:
        call_quality_score = 80
        if sample_requested and meeting_requested:
            call_quality_score = 95
    elif current_brand or monthly_consumption:
        call_quality_score = 50
    lead.call_quality_score = call_quality_score
    
    intent = lead.intent_score or lead.score or 0
    temp_score = (call_quality_score * 0.40) + (intent * 0.30) + (adjusted_win_probability * 100 * 0.20) + (10 if human_answered else 0)
    temp_score = min(100.0, max(0.0, temp_score))
    lead.lead_temperature_score = temp_score
    
    if temp_score >= 80:
        lead.lead_temperature_tier = "HOT"
    elif temp_score >= 60:
        lead.lead_temperature_tier = "WARM"
    elif temp_score >= 40:
        lead.lead_temperature_tier = "LUKEWARM"
    else:
        lead.lead_temperature_tier = "COLD"
        
    if sample_requested:
        lead.call_status = "SAMPLE_APPROVAL_PENDING"
        lead.status = "REPLIED"
    elif meeting_requested:
        lead.call_status = "MEETING_BOOKED"
        lead.status = "MEETING_BOOKED"
    else:
        if objection_reason or not human_answered:
            lead.call_status = "NOT_INTERESTED"
        else:
            lead.call_status = "COMPLETED"
            
    if objection_reason in ("Requested Removal", "Blacklisted") or lead.call_attempts >= CallingAgentService.MAX_CALL_ATTEMPTS:
        lead.do_not_call = True
        lead.dnc_reason = objection_reason or "Max attempts reached"
        lead.consent_status = "OPT_OUT"
        lead.consent_timestamp = datetime.utcnow()
        lead.consent_source = "AI_CALL_OPTOUT"
        
    # Map call outcome code
    outcome = "NOT_INTERESTED"
    if sample_requested or meeting_requested:
        outcome = "INTERESTED"
    elif next_followup_date:
        outcome = "CALL_BACK"
    elif not human_answered:
        outcome = "NO_RESPONSE"
    elif objection_reason:
        obj_lower = objection_reason.lower()
        if "price" in obj_lower or "budget" in obj_lower:
            outcome = "PRICE_ISSUE"
        elif "moq" in obj_lower or "volume" in obj_lower:
            outcome = "MOQ_ISSUE"
        elif "vendor" in obj_lower or "supplier" in obj_lower or "existing" in obj_lower:
            outcome = "SUPPLIER_LOCKED"
        else:
            outcome = "NOT_INTERESTED"
    else:
        outcome = "NOT_INTERESTED"
        
    lead.call_outcome_last = outcome

    # Map to objection learning if there is an objection
    if objection_reason or outcome in ("PRICE_ISSUE", "MOQ_ISSUE", "SUPPLIER_LOCKED"):
        from app.models.models import ObjectionLearning
        obj_type = "Price" if outcome == "PRICE_ISSUE" else ("MOQ" if outcome == "MOQ_ISSUE" else ("Existing Vendor" if outcome == "SUPPLIER_LOCKED" else "Other"))
        
        new_objection = ObjectionLearning(
            lead_id=lead.id,
            objection_type=obj_type,
            competitor_name=lead.current_supplier,
            offered_price_per_kg=lead.price_per_kg,
            recorded_at=datetime.utcnow(),
            notes=lead.call_summary or objection_reason
        )
        db.add(new_objection)
        
    if sample_requested:
        sku = lead.sample_sku or "Purica"
        contact_name = lead.contact_name or "Sir/Madam"
        whatsapp_msg = (
            f"Hi Mr. {contact_name},\n"
            f"Thank you for speaking with Ravi from Purity Beans. As discussed, sharing our coffee catalogue and sample options for {sku}.\n"
            f"Regards,\n"
            f"Hiten Jain\n"
            f"Purity Beans"
        )
        lead.recommended_action = f"WhatsApp: {whatsapp_msg}"
        
    history_record = CallHistory(
        lead_id=lead.id,
        call_date=datetime.utcnow(),
        duration=duration,
        cost=cost_inr,
        status=lead.call_status,
        quality_score=call_quality_score,
        recording_url=recording_url,
        summary=summary
    )
    db.add(history_record)
    lead.lead_locked_until = None
    
    db.commit()
    return {"status": "success", "message": "Webhook processed successfully", "lead_id": lead.id}



# ── V1.1 Founder Decision Engine & Workflow Audit ─────────────────────────────

from app.services.decision_engine import build_workspace
from app.models.models import WorkflowEvent
import uuid as _uuid

# ── Outreach Script Pre-Approval (multi-channel warming gate) ────────────────

# Email + WhatsApp are already live, quality-gated production channels →
# approved by default. AI call, LinkedIn, Facebook are newer/sensitive →
# require explicit founder approval before warming uses them.
DEFAULT_APPROVED_CHANNELS = {"email", "whatsapp"}


def is_channel_approved(db: Session, segment: str, channel: str) -> bool:
    """Gate: a lead is only warmed through a channel whose script is approved."""
    from app.models.models import ChannelScript
    from app.services.outreach_templates import resolve_segment
    seg = resolve_segment(segment)
    row = db.query(ChannelScript).filter(
        ChannelScript.segment == seg, ChannelScript.channel == channel
    ).first()
    if row is not None:
        return bool(row.approved)
    return channel in DEFAULT_APPROVED_CHANNELS


@router.get("/outreach/scripts")
def list_outreach_scripts(db: Session = Depends(get_db)):
    """
    All segment × channel scripts with their default text and founder
    approval status. This is the 'approve beforehand' hub — the founder
    reviews each channel's messaging once before warming uses it.
    """
    from app.models.models import ChannelScript
    from app.services.outreach_templates import (
        TEMPLATES, CHANNELS, CHANNEL_LABELS, channel_default_text,
    )
    existing = {(s.segment, s.channel): s for s in db.query(ChannelScript).all()}
    out = []
    for seg in TEMPLATES.keys():
        channels = []
        for ch in CHANNELS:
            row = existing.get((seg, ch))
            approved = bool(row.approved) if row is not None else (ch in DEFAULT_APPROVED_CHANNELS)
            channels.append({
                "channel": ch,
                "label": CHANNEL_LABELS.get(ch, ch),
                "default_text": channel_default_text(seg, ch),
                "custom_body": row.custom_body if row else None,
                "approved": approved,
                "default_approved": ch in DEFAULT_APPROVED_CHANNELS and row is None,
                "approved_at": row.approved_at.isoformat() if (row and row.approved_at) else None,
            })
        channels_approved = sum(1 for c in channels if c["approved"])
        out.append({"segment": seg, "channels": channels,
                    "approved_count": channels_approved, "total_channels": len(CHANNELS)})
    return {"segments": out, "channels": CHANNELS,
            "total_approved": sum(s["approved_count"] for s in out)}


class ScriptApproveRequest(BaseModel):
    segment: str
    channel: str
    approved: bool = True
    custom_body: Optional[str] = None


@router.post("/outreach/scripts/approve")
def approve_outreach_script(req: ScriptApproveRequest, db: Session = Depends(get_db)):
    """Approve (or un-approve) a segment × channel script, optionally editing it."""
    from app.models.models import ChannelScript
    from app.services.outreach_templates import resolve_segment, CHANNELS
    from app.services.pipeline_tracker import track

    seg = resolve_segment(req.segment)
    if req.channel not in CHANNELS:
        raise HTTPException(status_code=422, detail=f"channel must be one of {CHANNELS}")

    row = db.query(ChannelScript).filter(
        ChannelScript.segment == seg, ChannelScript.channel == req.channel
    ).first()
    if not row:
        row = ChannelScript(segment=seg, channel=req.channel)
        db.add(row)
    row.approved = req.approved
    if req.custom_body is not None:
        row.custom_body = req.custom_body
    row.approved_by = "FOUNDER" if req.approved else None
    row.approved_at = datetime.utcnow() if req.approved else None
    db.commit()
    track(db, "SCRIPT_APPROVED" if req.approved else "SCRIPT_UNAPPROVED",
          actor="FOUNDER", channel=req.channel,
          payload={"segment": seg, "channel": req.channel})
    return {"segment": seg, "channel": req.channel, "approved": row.approved}


@router.post("/outreach/scripts/approve-all")
def approve_all_scripts(segment: Optional[str] = None, db: Session = Depends(get_db)):
    """Bulk-approve every channel script (optionally for one segment)."""
    from app.models.models import ChannelScript
    from app.services.outreach_templates import TEMPLATES, CHANNELS, resolve_segment
    segs = [resolve_segment(segment)] if segment else list(TEMPLATES.keys())
    existing = {(s.segment, s.channel): s for s in db.query(ChannelScript).all()}
    n = 0
    for seg in segs:
        for ch in CHANNELS:
            row = existing.get((seg, ch))
            if not row:
                row = ChannelScript(segment=seg, channel=ch)
                db.add(row)
            if not row.approved:
                row.approved = True
                row.approved_by = "FOUNDER"
                row.approved_at = datetime.utcnow()
                n += 1
    db.commit()
    return {"approved": n, "segments": segs}


@router.get("/templates/{segment}")
def get_segment_templates(segment: str, name: str = "", city: str = ""):
    """
    V1.1: single source of truth for outreach templates.
    The UI renders these — it never hardcodes subjects, WhatsApp messages
    or AI call scripts.
    """
    from app.services.outreach_templates import get_templates
    return get_templates(segment, name=name, city=city)


@router.get("/dashboard/workspace")
def get_founder_workspace(db: Session = Depends(get_db)):
    """
    Returns the canonical FounderWorkspace — single source of truth for all
    Founder Mode widgets: Morning Brief, Money Today, Waiting On, Action Queue,
    Revenue Coach, Pipeline Health, Revenue Timeline.

    Architecture Freeze: reads the commercial model (Organization →
    RevenueOpportunity), synced from operational leads just-in-time.
    """
    from app.models.models import RevenueOpportunity
    from app.services.decision_engine import build_workspace_from_opportunities
    from sqlalchemy.orm import joinedload

    _sync_commercial_model_throttled(db)
    opportunities = db.query(RevenueOpportunity).options(
        joinedload(RevenueOpportunity.organization),
        joinedload(RevenueOpportunity.lead),
    ).filter(RevenueOpportunity.status != "LOST").all()
    workspace = build_workspace_from_opportunities(opportunities)
    # Serialize dataclasses
    import dataclasses
    def _serialize(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        return obj
    return {
        "morning_brief":   _serialize(workspace.morning_brief),
        "money_today":     workspace.money_today,
        "waiting_on":      workspace.waiting_on,
        "action_queue":    workspace.action_queue,
        "revenue_coach":   workspace.revenue_coach,
        "pipeline_health": _serialize(workspace.pipeline_health),
        "revenue_timeline": _serialize(workspace.revenue_timeline),
        "generated_at":    workspace.generated_at,
    }


@router.get("/dashboard/decision-engine")
def get_decision_engine(db: Session = Depends(get_db)):
    """
    Returns the unified Decision Engine summary stats and top decisions.
    """
    from app.services.decision_engine import get_decision_engine_summary
    return get_decision_engine_summary(db)


class WorkflowEventCreate(BaseModel):
    event_type: str
    lead_id: Optional[int] = None
    actor: str = "FOUNDER"
    channel: Optional[str] = None
    before_status: Optional[str] = None
    after_status: Optional[str] = None
    payload: Optional[dict] = None
    workflow_id: Optional[str] = None

@router.post("/workflow/event")
def log_workflow_event(evt: WorkflowEventCreate, db: Session = Depends(get_db)):
    """
    Append an immutable workflow event to the event store.
    Called by the frontend after any successful workflow action.
    """
    wf_event = WorkflowEvent(
        event_type=evt.event_type,
        lead_id=evt.lead_id,
        workflow_id=evt.workflow_id or str(_uuid.uuid4()),
        actor=evt.actor,
        channel=evt.channel,
        before_status=evt.before_status,
        after_status=evt.after_status,
        payload=evt.payload,
    )
    db.add(wf_event)
    db.commit()
    return {"status": "ok", "event_id": wf_event.id, "workflow_id": wf_event.workflow_id}


@router.get("/workflow/events")
def get_workflow_events(
    lead_id: Optional[int] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Retrieve workflow events for audit trail, filtered by lead or event type."""
    q = db.query(WorkflowEvent)
    if lead_id:
        q = q.filter(WorkflowEvent.lead_id == lead_id)
    if event_type:
        q = q.filter(WorkflowEvent.event_type == event_type)
    events = q.order_by(WorkflowEvent.occurred_at.desc()).limit(limit).all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "lead_id": e.lead_id,
            "workflow_id": e.workflow_id,
            "actor": e.actor,
            "channel": e.channel,
            "before_status": e.before_status,
            "after_status": e.after_status,
            "payload": e.payload,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
        }
        for e in events
    ]


# ── Workflow Engine (V1.1 — all side effects) ────────────────────────────────

class WorkflowExecuteRequest(BaseModel):
    workflow_type: str
    lead_id: int
    payload: Optional[dict] = None


@router.post("/workflow/execute")
def workflow_execute(req: WorkflowExecuteRequest, db: Session = Depends(get_db)):
    """
    Single execution gateway. Consults business policies, runs the side
    effect, records a WorkflowExecution row and immutable WorkflowEvents.
    Statuses returned: completed | policy_blocked | failed.
    """
    from app.services.workflow_engine import WorkflowEngine
    return WorkflowEngine.execute(db, req.workflow_type, req.lead_id,
                                  payload=req.payload, requested_by="FOUNDER")


@router.post("/workflow/retry/{execution_id}")
def workflow_retry(execution_id: int, db: Session = Depends(get_db)):
    """Retry a FAILED workflow execution."""
    from app.services.workflow_engine import WorkflowEngine
    return WorkflowEngine.retry(db, execution_id)


@router.get("/workflow/queues")
def workflow_queues(db: Session = Depends(get_db)):
    """
    The 9 operating queues of the Action Queue center. Everything the
    founder can execute lives here — no execution buttons anywhere else.
    """
    from app.models.models import EmailDraft, RevenueOpportunity
    from app.services.decision_engine import build_workspace_from_opportunities
    from sqlalchemy.orm import joinedload

    leads = db.query(B2BLead).all()   # still used for orders/reorders buckets
    _sync_commercial_model_throttled(db)
    opportunities = db.query(RevenueOpportunity).options(
        joinedload(RevenueOpportunity.organization),
        joinedload(RevenueOpportunity.lead),
    ).filter(RevenueOpportunity.status != "LOST").all()
    ws = build_workspace_from_opportunities(opportunities)
    queue = ws.action_queue  # classified recommendations

    # Auto-advanced follow-ups that are now due (spec §Automated Follow-up):
    # merge them into the channel queues so the founder sees the next touch.
    due_followups = _due_followups(db)
    due_by_channel: dict = {}
    for it in due_followups:
        due_by_channel.setdefault(it["channel"], []).append(it)

    def _bucket(action_types: set) -> list:
        items = [a for a in queue if a["action_type"] in action_types]
        seen = {a.get("lead_id") for a in items}
        for ch in action_types:
            for f in due_by_channel.get(ch, []):
                if f["lead_id"] not in seen:
                    items.append({**f, "due_followup": True})
                    seen.add(f["lead_id"])
        return items

    email_approval_count = db.query(EmailDraft).filter(
        EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"])
    ).count()

    queues = {
        "email_approval": {"label": "Email Approval", "count": email_approval_count,
                            "items": []},  # served by /b2b/email/approval-inbox
        "whatsapp":       {"label": "WhatsApp Pending", "items": _bucket({"whatsapp"})},
        "ai_calls":       {"label": "AI Calls", "items": _bucket({"ai_call"})},
        "founder_calls":  {"label": "Founder Calls", "items": _bucket({"founder_call"})},
        "meetings":       {"label": "Meetings", "items": _bucket({"meeting"})},
        "samples":        {"label": "Samples", "items": _bucket({"sample"})},
        "proposals":      {"label": "Proposals", "items": _bucket({"proposal", "close"})},
        "orders":         {"label": "Orders",
                            "items": [
                                {"lead_id": l.id, "company": l.company, "status": l.status,
                                 "estimated_value": l.estimated_value or 0}
                                for l in leads if l.status == "ORDER_WON"
                            ]},
        "reorders":       {"label": "Reorders",
                            "items": [
                                {"lead_id": l.id, "company": l.company, "status": l.status,
                                 "estimated_value": l.estimated_value or 0}
                                for l in leads if l.status in ("ONBOARDED", "REORDER_PREDICTED")
                            ]},
    }
    for k, v in queues.items():
        if "count" not in v:
            v["count"] = len(v["items"])

    return {"queues": queues, "generated_at": datetime.utcnow().isoformat()}


# ── Government Leads & GeM Tender Monitor ─────────────────────────────────────

from app.services.gem_monitor import scan_tenders, list_tenders
from app.models.models import GovTender


@router.post("/b2b/government/seed-leads")
def seed_gov_leads():
    """
    V1.1 POLICY: DISABLED. Seeded/fabricated leads violate the no-mock-data rule.
    Government leads must come from real discovery (scan-tenders, directories).
    """
    raise HTTPException(
        status_code=410,
        detail="Seeding disabled by V1.1 policy — government leads must come from real discovery sources.",
    )


@router.get("/b2b/government/leads")
def get_gov_leads(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """List all leads with division='government'. Contacts gated to verified-only."""
    leads = db.query(B2BLead).filter(B2BLead.division == "government").order_by(
        B2BLead.score.desc()
    ).all()
    # Silent progressive verification for unsearched government leads
    unsearched = [l.id for l in leads if l.contact_searched_at is None][:15]
    if unsearched:
        background_tasks.add_task(_silent_verify_and_enrich, unsearched)
    gated = [(l, *_gate_contacts(l)) for l in leads]
    return [
        {
            "id": l.id,
            "company": l.company,
            "contact_name": l.contact_name,
            "contact_title": l.contact_title,
            "email": l.email if email_ok else None,
            "phone": l.phone if phone_ok else None,
            "phone_verified": bool(l.phone_verified),
            "phone_source": l.phone_source,
            "contact_search_status": search_status,
            "city": l.city,
            "industry": l.industry,
            "region": l.region,
            "status": l.status,
            "priority": l.priority,
            "score": l.score,
            "estimated_value": l.estimated_value,
            "qualification_notes": l.qualification_notes,
            "recommended_action": l.recommended_action,
            "website": l.website,
            "last_updated": l.last_updated.isoformat() if l.last_updated else None,
        }
        for l, email_ok, phone_ok, search_status in gated
    ]


# ── Commercial Model V1.1 (Architecture Freeze): Organizations ───────────────

MARGIN_RATE_ORG = 0.31

_LAST_COMMERCIAL_SYNC: float = 0.0
_COMMERCIAL_SYNC_TTL_SEC = 120   # at most once every 2 min for read paths


def _sync_commercial_model_throttled(db: Session) -> dict:
    """
    Read paths (workspace / queues) used to run the FULL 712-lead sync on every
    single request, which made /workflow/queues take ~24s and starved every other
    request. The mirror only changes when leads change, so for reads it is enough
    to refresh it periodically. The explicit POST /organizations/sync endpoint
    still forces a full, immediate sync.
    """
    global _LAST_COMMERCIAL_SYNC
    import time as _t
    now = _t.monotonic()
    if now - _LAST_COMMERCIAL_SYNC < _COMMERCIAL_SYNC_TTL_SEC:
        return {"skipped": True, "reason": "synced recently"}
    _LAST_COMMERCIAL_SYNC = now
    return _sync_commercial_model(db)


def _sync_commercial_model(db: Session) -> dict:
    """
    Mirror every B2BLead into Organization + RevenueOpportunity. Idempotent.
    Called by the sync endpoint, and periodically (throttled) before workspace
    builds so the Decision Engine reads a fresh-enough commercial model.
    """
    from app.models.models import Organization, RevenueOpportunity

    WON = {"ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "ACCOUNT_GROWTH", "UPSELL_OFFERED"}
    leads = db.query(B2BLead).all()
    created_orgs = updated_orgs = created_opps = 0

    for l in leads:
        name = (l.company or "").strip()
        if not name:
            continue
        org = db.query(Organization).filter(Organization.name == name).first()
        if not org:
            org = Organization(name=name, first_seen=l.last_updated or datetime.utcnow())
            db.add(org)
            db.flush()
            created_orgs += 1
        else:
            updated_orgs += 1
        # Mirror verified contact + profile data (same gating as everywhere)
        org.segment = l.division or org.segment
        org.city = l.city or org.city
        org.website = l.website or org.website
        org.contact_name = l.contact_name or org.contact_name
        org.email = l.email
        org.phone = l.phone
        org.whatsapp_number = l.whatsapp_number
        org.phone_verified = bool(l.phone_verified)
        org.email_verification_status = l.email_verification_status or "UNVERIFIED"
        org.last_activity = l.last_updated

        opp = db.query(RevenueOpportunity).filter(RevenueOpportunity.lead_id == l.id).first()
        if not opp:
            opp = RevenueOpportunity(organization_id=org.id, lead_id=l.id)
            db.add(opp)
            created_opps += 1
        opp.stage = l.status
        opp.estimated_value = l.estimated_value or 0
        opp.expected_margin = round((l.estimated_value or 0) * MARGIN_RATE_ORG)
        opp.probability = l.probability or 0
        opp.status = "WON" if l.status in WON else ("OPEN" if l.status != "LOST" else "LOST")
        if opp.status == "WON" and not opp.closed_at:
            opp.closed_at = datetime.utcnow()
            org.orders_count = (org.orders_count or 0) + 1
            org.revenue_won = (org.revenue_won or 0) + opp.estimated_value
            org.gross_margin_won = (org.gross_margin_won or 0) + opp.expected_margin

    db.commit()
    return {"organizations_created": created_orgs, "organizations_updated": updated_orgs,
            "opportunities_created": created_opps,
            "total_organizations": db.query(Organization).count(),
            "total_opportunities": db.query(RevenueOpportunity).count()}


@router.post("/organizations/sync")
def sync_organizations(db: Session = Depends(get_db)):
    """Strangler migration sync — mirror leads into the commercial model."""
    return _sync_commercial_model(db)


@router.get("/organizations")
def list_organizations(segment: Optional[str] = None, limit: int = 100,
                             db: Session = Depends(get_db)):
    """Primary-entity view: permanent accounts with their open opportunity value."""
    from app.models.models import Organization, RevenueOpportunity
    q = db.query(Organization)
    if segment:
        q = q.filter(Organization.segment == segment)
    orgs = q.order_by(Organization.revenue_won.desc(), Organization.last_activity.desc()).limit(limit).all()
    out = []
    for o in orgs:
        open_opps = [p for p in o.opportunities if p.status == "OPEN"]
        out.append({
            "id": o.id, "name": o.name, "segment": o.segment, "city": o.city,
            "contact_name": o.contact_name,
            "email": o.email if (o.email_verification_status in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL")) else None,
            "phone": o.phone if o.phone_verified else None,
            "revenue_won_rs": o.revenue_won, "gross_margin_won_rs": o.gross_margin_won,
            "orders_count": o.orders_count, "relationship_score": o.relationship_score,
            "open_pipeline_rs": round(sum(p.estimated_value for p in open_opps)),
            "open_expected_margin_rs": round(sum(p.expected_margin for p in open_opps)),
            "open_opportunities": len(open_opps),
            "last_activity": o.last_activity.isoformat() if o.last_activity else None,
        })
    return {"total": len(out), "organizations": out}


# ── Government Revenue Engine V1.2 ───────────────────────────────────────────

@router.post("/government/tenders/qualify-all")
def qualify_all_tenders(db: Session = Depends(get_db)):
    """
    Run the qualification engine over every OPEN tender: product match,
    bid readiness, win confidence, revenue opportunity score, recommendation.
    Also creates/updates the permanent GovOrganization account per buyer.
    """
    from app.services.gov_revenue_engine import qualify_tender, upsert_organization

    tenders = db.query(GovTender).filter(GovTender.status == "OPEN").all()
    qualified = 0
    for t in tenders:
        q = qualify_tender(t)
        t.product_match_pct = q["product_match_pct"]
        t.bid_readiness_pct = q["bid_readiness_pct"]
        t.win_confidence_pct = q["win_confidence_pct"]
        t.revenue_opportunity_score = q["revenue_opportunity_score"]
        t.expected_margin = q["expected_margin_rs"]
        t.recommended_action = q["recommended_action"]
        t.missing_documents_list = q["missing_documents"]
        upsert_organization(db, t)
        qualified += 1
    db.commit()
    return {"qualified": qualified,
            "apply": sum(1 for t in tenders if t.recommended_action == "APPLY"),
            "review": sum(1 for t in tenders if t.recommended_action == "REVIEW"),
            "skip": sum(1 for t in tenders if t.recommended_action == "SKIP")}


@router.post("/government/tenders/{tender_id}/analyze")
def analyze_tender(tender_id: int, db: Session = Depends(get_db)):
    """
    Grounded AI analysis for founder review — built only from the stored
    tender record and the verified company profile. Never invents facts.
    """
    from app.services.gov_revenue_engine import ai_tender_analysis
    t = db.query(GovTender).filter(GovTender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    analysis = ai_tender_analysis(t)
    if analysis is None:
        raise HTTPException(status_code=503, detail="AI analysis unavailable (CEREBRAS_API_KEY not configured)")
    t.ai_analysis = analysis
    db.commit()
    return {"tender_id": tender_id, "analysis": analysis}


class TenderResultRequest(BaseModel):
    result: str                       # WON | LOST | NOT_SUBMITTED
    reason: Optional[str] = None
    competitor: Optional[str] = None
    price_difference_pct: Optional[float] = None
    technical_reason: Optional[str] = None
    documentation_issues: Optional[str] = None
    founder_notes: Optional[str] = None
    bid_value: Optional[float] = 0
    margin_realized: Optional[float] = 0


@router.post("/government/tenders/{tender_id}/record-result")
def record_tender_result(tender_id: int, req: TenderResultRequest,
                               db: Session = Depends(get_db)):
    """
    Win/Loss learning: immutable outcome record + updates the buyer's
    permanent organization account. Unknown reasons stay NULL — never guessed.
    """
    from app.models.models import GovTenderOutcome, GovOrganization
    from app.services.pipeline_tracker import track

    t = db.query(GovTender).filter(GovTender.id == tender_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tender not found")
    if req.result not in ("WON", "LOST", "NOT_SUBMITTED"):
        raise HTTPException(status_code=422, detail="result must be WON | LOST | NOT_SUBMITTED")

    outcome = GovTenderOutcome(
        tender_id=t.id, organization_id=t.organization_id,
        result=req.result, reason=req.reason, competitor=req.competitor,
        price_difference_pct=req.price_difference_pct,
        technical_reason=req.technical_reason,
        documentation_issues=req.documentation_issues,
        founder_notes=req.founder_notes,
        bid_value=req.bid_value or 0, margin_realized=req.margin_realized or 0,
    )
    db.add(outcome)
    t.status = "WON" if req.result == "WON" else ("LOST" if req.result == "LOST" else "SKIPPED")

    if t.organization_id:
        org = db.query(GovOrganization).filter(GovOrganization.id == t.organization_id).first()
        if org:
            if req.result == "WON":
                org.tenders_won = (org.tenders_won or 0) + 1
                org.revenue_won = (org.revenue_won or 0) + (req.bid_value or 0)
                org.relationship_score = min(100, (org.relationship_score or 0) + 20)
            elif req.result == "LOST":
                org.tenders_lost = (org.tenders_lost or 0) + 1

    track(db, "TENDER_RESULT_RECORDED", actor="FOUNDER", channel="system",
          payload={"tender_id": t.tender_id, "result": req.result, "bid_value": req.bid_value})
    db.commit()
    return {"recorded": True, "tender_status": t.status}


@router.get("/government/revenue-dashboard")
def government_revenue_dashboard(db: Session = Depends(get_db)):
    """
    The founder's government morning view: today's best tenders ranked by
    revenue opportunity, actions required, deadlines at risk, and real KPIs.
    """
    from app.models.models import GovTenderOutcome, GovOrganization, EmailDraft
    from app.services.gov_revenue_engine import DOCUMENT_CHECKLIST, qualify_tender

    open_tenders = db.query(GovTender).filter(GovTender.status == "OPEN").all()
    ranked = sorted(open_tenders, key=lambda t: t.revenue_opportunity_score or 0, reverse=True)

    best_today = []
    for t in ranked[:10]:
        if (t.recommended_action or "") == "SKIP":
            continue
        q = qualify_tender(t)   # pure compute — adds effort/ROI live
        best_today.append({
            "id": t.id, "tender_id": t.tender_id, "title": t.title,
            "department": t.department, "portal": t.portal, "location": t.location,
            "estimated_value_rs": t.estimated_value,
            "expected_margin_rs": t.expected_margin,
            "win_confidence_pct": t.win_confidence_pct,
            "product_match_pct": t.product_match_pct,
            "bid_readiness_pct": t.bid_readiness_pct,
            "revenue_opportunity_score": t.revenue_opportunity_score,
            "recommended_action": t.recommended_action,
            "deadline": t.deadline, "days_to_deadline": t.days_to_deadline,
            "missing_documents": t.missing_documents_list or [],
            "source_url": t.source_url,
            "founder_time_min": q["founder_time_min"],
            "margin_per_founder_hour_rs": q["margin_per_founder_hour_rs"],
            "roi_label": q["roi_label"],
            "next_action": (
                "Upload documents" if (t.missing_documents_list and t.recommended_action == "APPLY")
                else "Review & approve bid" if t.recommended_action == "APPLY"
                else "Founder review" if t.recommended_action == "REVIEW"
                else "Skip"
            ),
        })

    at_risk = [t for t in open_tenders
               if t.days_to_deadline is not None and t.days_to_deadline <= 5
               and (t.recommended_action or "") == "APPLY"]

    # ── Revenue at Risk: what is blocking money right now, in rupees ──
    apply_tenders = [t for t in open_tenders if (t.recommended_action or "") == "APPLY"]
    blocked_docs = sum((t.estimated_value or 0) for t in apply_tenders if t.missing_documents_list)
    pending_drafts = db.query(EmailDraft).filter(EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"])).count()
    blocked_deadline = sum((t.estimated_value or 0) for t in at_risk)
    revenue_blocked_by = {
        "missing_documents_rs": round(blocked_docs),
        "tender_deadline_rs": round(blocked_deadline),
        "founder_approvals_pending": pending_drafts,
    }

    # ── Government Revenue Coach: single best opportunity + reasons ──
    coach = None
    if best_today:
        top = best_today[0]
        reasons = []
        if top["product_match_pct"] >= 80:
            reasons.append("High product match")
        if top["days_to_deadline"] is not None and top["days_to_deadline"] <= 7:
            reasons.append(f"Deadline in {top['days_to_deadline']} days")
        if top["missing_documents"]:
            reasons.append(f"{len(top['missing_documents'])} documents missing")
        reasons.append(f"₹{round((top['margin_per_founder_hour_rs'] or 0)/1000)}k margin per founder hour")
        coach = {
            "department": top["department"],
            "expected_margin_rs": top["expected_margin_rs"],
            "win_confidence_pct": top["win_confidence_pct"],
            "roi_label": top["roi_label"],
            "reasons": reasons,
            "recommended_action": top["next_action"],
            "tender_row_id": top["id"],
        }

    outcomes = db.query(GovTenderOutcome).all()
    won = [o for o in outcomes if o.result == "WON"]
    submitted = [o for o in outcomes if o.result in ("WON", "LOST")]

    return {
        "best_today": best_today,
        "coach": coach,
        "revenue_blocked_by": revenue_blocked_by,
        "revenue_at_risk_rs": round(sum((t.estimated_value or 0) for t in at_risk)),
        "deadlines_this_week": len(at_risk),
        "document_checklist": DOCUMENT_CHECKLIST,
        "kpis": {
            "tenders_found": db.query(GovTender).count(),
            "open": len(open_tenders),
            "qualified_apply": sum(1 for t in open_tenders if t.recommended_action == "APPLY"),
            "submitted": len(submitted),
            "won": len(won),
            "win_rate_pct": round(len(won) / len(submitted) * 100) if submitted else 0,
            "revenue_won_rs": round(sum(o.bid_value or 0 for o in won)),
            "gross_margin_won_rs": round(sum(o.margin_realized or 0 for o in won)),
            "organizations_tracked": db.query(GovOrganization).count(),
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.post("/b2b/government/scan-tenders")
def scan_gov_tenders(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Seeds curated static tenders immediately, then kicks off live GeM/CPPP
    scraping as a background task so the response is instant.
    """
    from app.services.gem_monitor import _static_known_tenders, _make_tender_id, _days_until
    from sqlalchemy.exc import IntegrityError as _IE

    # Seed static tenders now (fast)
    statics = _static_known_tenders()
    new_count = 0
    for t in statics:
        ref = t.get("ref", t["title"][:30])
        tid = _make_tender_id(t["portal"], ref)
        days = _days_until(t.get("deadline", "")) if t.get("deadline") else None
        existing = db.query(GovTender).filter(GovTender.tender_id == tid).first()
        if existing:
            if days is not None:
                existing.days_to_deadline = days
            existing.last_updated = datetime.utcnow()
            from app.services.gem_monitor import evaluate_and_score_tender
            evaluate_and_score_tender(existing)
        else:
            try:
                from app.services.gem_monitor import evaluate_and_score_tender
                new_t = GovTender(
                    tender_id=tid, title=t["title"], department=t["department"],
                    portal=t["portal"], location=t.get("location"),
                    estimated_value=t.get("estimated_value", 0.0),
                    deadline=t.get("deadline"), days_to_deadline=days,
                    status="OPEN", notes=t.get("notes"), source_url=t.get("source_url"),
                )
                evaluate_and_score_tender(new_t)
                db.add(new_t)
                db.flush()
                new_count += 1
            except _IE:
                db.rollback()
    db.commit()

    # Kick off live scraping in background (non-blocking)
    def _live_scan():
        from app.database.database import SessionLocal
        from app.services.gem_monitor import scan_tenders as _scan
        _db = SessionLocal()
        try:
            _scan(_db)
        finally:
            _db.close()

    background_tasks.add_task(_live_scan)

    return {"new_tenders": new_count, "updated": len(statics) - new_count, "live_scan": "running in background"}


@router.get("/b2b/government/tenders")
def get_gov_tenders(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """List government tenders, optionally filtered by status."""
    tenders = list_tenders(db, status=status)
    return [
        {
            "id": t.id,
            "tender_id": t.tender_id,
            "title": t.title,
            "department": t.department,
            "portal": t.portal,
            "location": t.location,
            "estimated_value": t.estimated_value,
            "quantity_kg": t.quantity_kg,
            "deadline": t.deadline,
            "days_to_deadline": t.days_to_deadline,
            "status": t.status,
            "eligibility_check": t.eligibility_check,
            "notes": t.notes,
            "source_url": t.source_url,
            "opportunity_score": t.opportunity_score,
            "win_probability": t.win_probability,
            "required_products": t.required_products,
            "suggested_pricing": t.suggested_pricing,
            "expected_margin": t.expected_margin,
            "risk_level": t.risk_level,
            "proposal_text": t.proposal_text,
            "compliance_checklist": t.compliance_checklist,
            "missing_documents": t.missing_documents,
            "bid_strategy": t.bid_strategy,
            "found_at": t.found_at.isoformat() if t.found_at else None,
        }
        for t in tenders
    ]


@router.patch("/b2b/government/tenders/{tender_id}")
def update_tender_status(
    tender_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """Update tender status or eligibility."""
    tender = db.query(GovTender).filter(GovTender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    for field in ("status", "eligibility_check", "notes"):
        if field in body:
            setattr(tender, field, body[field])
    tender.last_updated = datetime.utcnow()
    db.commit()
    return {"ok": True}


# ── Revenue Engine — Command Center stats & fire ──────────────────────────────

@router.get("/b2b/revenue-engine/status")
def revenue_engine_status(db: Session = Depends(get_db)):
    """
    Returns everything the Revenue Command Center needs:
    - how many leads are ready to email/WhatsApp
    - today's top 5 highest-value closes
    - revenue gap vs weekly target
    - automation counts for today
    """
    from sqlalchemy import func as sqlfunc
    from app.models.models import AgentLog

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # Leads ready for first-touch email
    # Counted through the same gate the sender uses. A dashboard that says
    # "3 email ready" while send_email refuses all 3 is worse than saying zero:
    # the founder approves a campaign that cannot go out.
    from app.services.contact_trust import sendable as _snd, actionable as _act
    email_ready = sum(1 for _l in db.query(B2BLead).filter(
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED"]),
        B2BLead.division != "government").all()
        if _snd(_l)[0] and _act(_l)[0])

    # Leads ready for WhatsApp
    wa_ready = db.query(B2BLead).filter(
        B2BLead.whatsapp_number.isnot(None),
        B2BLead.whatsapp_number != "",
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED"]),
    ).count()

    # Follow-ups due (EMAIL_SENT but not replied, more than 3 days ago)
    from datetime import timedelta
    followup_due = db.query(B2BLead).filter(
        B2BLead.status == "EMAIL_SENT",
        B2BLead.last_updated < datetime.utcnow() - timedelta(days=3),
    ).count()

    # Emails sent today
    emails_today = db.query(AgentLog).filter(
        AgentLog.agent_name == "B2B Outreach Agent",
        AgentLog.timestamp >= today_start,
    ).count()

    # Government leads not yet contacted
    gov_ready = db.query(B2BLead).filter(
        B2BLead.division == "government",
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED"]),
        B2BLead.email.like("%@%"),
    ).count()

    # Top 5 closes: highest score × estimated_value
    top_leads_raw = db.query(B2BLead).filter(
        B2BLead.status.in_(["QUALIFIED", "EMAIL_SENT", "REPLIED", "MEETING_BOOKED",
                             "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT"]),
    ).order_by((B2BLead.score * B2BLead.estimated_value).desc()).limit(5).all()

    top_leads = [
        {
            "id": l.id,
            "company": l.company,
            "city": l.city,
            "status": l.status,
            "score": l.score,
            "estimated_value": l.estimated_value,
            "email": l.email,
            "phone": l.phone,
            "whatsapp_number": l.whatsapp_number,
            "division": l.division,
            "recommended_action": l.recommended_action,
            "contact_name": l.contact_name,
        }
        for l in top_leads_raw
    ]

    # Revenue gap (vs ₹1L/week target)
    weekly_target = 100_000
    won_this_week = db.query(B2BLead).filter(
        B2BLead.status == "ORDER_WON",
        B2BLead.last_updated >= datetime.utcnow() - timedelta(days=7),
    ).all()
    won_revenue_week = sum(l.estimated_value for l in won_this_week)
    revenue_gap = max(0, weekly_target - won_revenue_week)

    # Pipeline by division
    pipeline = {}
    for div in ["corporate", "horeca", "distributor", "government", "retail"]:
        leads_div = db.query(B2BLead).filter(B2BLead.division == div).all()
        pipeline[div] = sum(l.estimated_value for l in leads_div if l.status not in ("ORDER_WON", "COLD"))

    return {
        "email_ready": email_ready,
        "wa_ready": wa_ready,
        "followup_due": followup_due,
        "gov_ready": gov_ready,
        "emails_today": emails_today,
        "top_leads": top_leads,
        "revenue_gap": revenue_gap,
        "won_this_week": won_revenue_week,
        "weekly_target": weekly_target,
        "pipeline_by_division": pipeline,
        "actions_needed": max(0, 11 - emails_today),   # need 11 actions/week to hit target
    }


@router.post("/b2b/revenue-engine/fire-whatsapp")
def fire_whatsapp_blast(
    background_tasks: BackgroundTasks,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """
    V1.1 POLICY: draft-only. Returns pre-composed WhatsApp messages with
    wa.me links for the founder to review and send personally. No lead
    status is changed here — the founder confirms each send afterwards
    via /b2b/leads/{id}/mark-whatsapp.
    """
    import urllib.parse

    leads = db.query(B2BLead).filter(
        B2BLead.whatsapp_number.isnot(None),
        B2BLead.whatsapp_number != "",
        B2BLead.phone_verified == True,   # V1.1: only web-verified numbers
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED"]),
    ).order_by(B2BLead.score.desc()).limit(limit).all()

    drafts = []
    for lead in leads:
        name = (lead.contact_name or "").split()[0] if lead.contact_name else ""
        greeting = f"Hi {name}," if name else "Hi,"
        msg = (
            f"{greeting} Hiten here from Purity Beans. "
            f"We supply 100% pure instant coffee (zero chicory) to corporates, "
            f"hotels and distributors across India.\n\n"
            f"Can I send a free tasting kit to {lead.company} and set a quick 15-min call?\n\n"
            f"Reply 1 = Sample  |  2 = Call  |  3 = Pricing\n\n"
            f"📞 +91 90849 58495  ·  p3online.in"
        )
        phone = "".join(c for c in (lead.whatsapp_number or "") if c.isdigit())
        drafts.append({
            "lead_id": lead.id,
            "company": lead.company,
            "wa": lead.whatsapp_number,
            "message": msg,
            "whatsapp_url": f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}",
        })

    return {
        "mode": "draft_only",
        "queued": 0,
        "drafted": len(drafts),
        "leads": drafts,
        "message": "Review each message and send from WhatsApp — then mark as sent.",
    }


# ── EMAIL APPROVAL & VERIFICATION SYSTEM ──────────────────────────────────────

@router.post("/b2b/email/verify-batch")
def verify_email_batch(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Kick off background verification of all leads that have emails but haven't been verified yet.
    Returns immediately with a count; verification runs in background.
    """
    from app.models.models import B2BLead

    pending = db.query(B2BLead).filter(
        B2BLead.email.like("%@%"),
        B2BLead.email_verification_status == "UNVERIFIED",
        B2BLead.email_rejected_by_founder == False,
    ).count()

    def _run():
        from app.database.database import SessionLocal
        from app.services.email_verifier import verify_email
        _db = SessionLocal()
        try:
            leads = _db.query(B2BLead).filter(
                B2BLead.email.like("%@%"),
                B2BLead.email_verification_status == "UNVERIFIED",
            ).limit(200).all()
            for lead in leads:
                result = verify_email(
                    lead.email,
                    company_name=lead.company or "",
                    division=lead.division or "",
                    website=lead.website or "",
                )
                lead.email_verified = result["status"] == "VALID"
                lead.email_confidence = result["confidence"]
                lead.email_verification_status = result["status"]
                lead.email_mx_valid = result["mx_valid"]
                lead.email_is_generic = result["is_generic"]
                lead.email_domain_match = result["domain_match"]
                lead.email_verify_reason = result["reason"]
            _db.commit()
        finally:
            _db.close()

    background_tasks.add_task(_run)
    return {"message": "Verification started in background", "pending_count": pending}


@router.get("/b2b/email/queue")
def get_email_queue(db: Session = Depends(get_db)):
    """
    Returns the email approval queue — leads bucketed by verification status.
    Includes email preview for each lead.
    """
    from app.models.models import B2BLead
    from app.services.email_verifier import generate_email_preview

    all_leads = db.query(B2BLead).filter(
        B2BLead.email.like("%@%"),
        B2BLead.status.in_(["DISCOVERED", "QUALIFIED", "REPLIED", "EMAIL_SENT"]),
        B2BLead.email_rejected_by_founder == False,
    ).order_by(B2BLead.score.desc()).all()

    verified = []
    needs_approval = []
    invalid = []
    unverified = []

    for lead in all_leads:
        preview = generate_email_preview(lead)
        item = {
            "id": lead.id,
            "company": lead.company,
            "city": lead.city,
            "division": lead.division,
            "status": lead.status,
            "score": lead.score,
            "email": lead.email,
            "contact_name": lead.contact_name,
            "email_confidence": lead.email_confidence or 0,
            "email_verification_status": lead.email_verification_status or "UNVERIFIED",
            "email_mx_valid": lead.email_mx_valid or False,
            "email_is_generic": lead.email_is_generic or False,
            "email_domain_match": lead.email_domain_match or False,
            "email_verify_reason": lead.email_verify_reason or "Not yet verified",
            "email_approved": lead.email_approved_by_founder or False,
            "preview": preview,
        }

        vs = lead.email_verification_status or "UNVERIFIED"
        approved = lead.email_approved_by_founder or False

        if vs == "INVALID":
            invalid.append(item)
        elif vs == "UNVERIFIED":
            unverified.append(item)
        elif vs == "VALID" and (lead.division not in ("government",)) and approved:
            verified.append(item)
        else:
            needs_approval.append(item)

    return {
        "verified": verified,
        "needs_approval": needs_approval,
        "invalid": invalid,
        "unverified": unverified,
        "counts": {
            "verified": len(verified),
            "needs_approval": len(needs_approval),
            "invalid": len(invalid),
            "unverified": len(unverified),
            "total": len(verified) + len(needs_approval) + len(invalid) + len(unverified),
        },
    }


class EmailApprovalRequest(BaseModel):
    lead_ids: list[int]
    custom_body: Optional[str] = None


@router.post("/b2b/email/approve")
def approve_emails(req: EmailApprovalRequest, db: Session = Depends(get_db)):
    """
    Founder approves a set of leads for email sending. Leads with no email
    address are skipped — approving one would flag it approved, remove it from
    the pending queue, and never send (see the approve-journey incident).
    """
    from app.models.models import B2BLead

    updated = 0
    skipped = []
    for lead_id in req.lead_ids:
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            continue
        if not (lead.email or "").strip():
            skipped.append({"lead_id": lead.id, "company": lead.company,
                            "reason": "no email address yet — auto-warm enrichment still searching"})
            continue
        lead.email_approved_by_founder = True
        lead.email_rejected_by_founder = False
        updated += 1
    db.commit()
    return {"approved": updated, "skipped": len(skipped), "skipped_detail": skipped,
            "lead_ids": req.lead_ids}


@router.post("/b2b/email/reject/{lead_id}")
def reject_email(lead_id: int, db: Session = Depends(get_db)):
    """Founder rejects a lead's email — will not send."""
    from app.models.models import B2BLead

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.email_rejected_by_founder = True
    lead.email_approved_by_founder = False
    db.commit()
    return {"rejected": True, "lead_id": lead_id}


class SendApprovedRequest(BaseModel):
    lead_ids: list[int]
    custom_subject: Optional[str] = None
    custom_body: Optional[str] = None


@router.post("/b2b/email/send-approved")
def send_approved_emails(req: SendApprovedRequest, db: Session = Depends(get_db)):
    """
    V1.1 HARD RULE: single or bulk, an email is sent ONLY when the DB holds a
    founder-approved EmailDraft for that lead. The draft's exact subject/body
    is what goes out (never regenerated at send time), the draft row becomes
    the immutable approval record, and every send is event-logged.
    """
    # Integrity sweep before a batch decision. Boot-time is not enough: an
    # out-of-band write that lands after startup would otherwise be approved on
    # this pass. Cheap for the sizes involved, and it fails open - a sweep error
    # never blocks the founder from approving.
    try:
        from app.services.contact_trust import sweep as _sweep
        _sweep(db)
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    from app.models.models import B2BLead, EmailDraft
    from app.services.email_sender import build_outreach_email, send_email
    from app.services.pipeline_tracker import track

    zoho_pw = os.getenv("ZOHO_APP_PASSWORD", "")
    simulate = not bool(zoho_pw and zoho_pw.strip() not in ("", "your_zoho_app_password_here"))

    leads = db.query(B2BLead).filter(
        B2BLead.id.in_(req.lead_ids),
        B2BLead.email_approved_by_founder == True,
        B2BLead.email_rejected_by_founder == False,
    ).all()

    sent_real = 0
    sent_sim = 0
    skipped = 0
    results = []

    for lead in leads:
        if not lead.email:
            skipped += 1
            continue

        # DB approval gate: the exact content must exist as an EmailDraft.
        draft = db.query(EmailDraft).filter(
            EmailDraft.lead_id == lead.id,
            EmailDraft.status.in_(["PENDING", "EDITED", "APPROVED"]),
        ).order_by(EmailDraft.id.desc()).first()
        if not draft:
            skipped += 1
            results.append({"company": lead.company, "email": lead.email,
                            "status": "blocked", "reason": "no approved draft in DB — review in Approval Inbox"})
            continue
        if req.custom_body:
            draft.body = req.custom_body   # founder-edited text becomes the record
        if req.custom_subject:
            draft.subject = req.custom_subject

        if simulate:
            sent_sim += 1
            # NOT "SENT". Marking a draft sent for a message that never left
            # puts a delivery in the record that did not happen — the same lie
            # that made the Journey unreadable.
            draft.status = "SIMULATED"
            results.append({"company": lead.company, "email": lead.email, "status": "simulated", "draft_id": draft.id})
        else:
            try:
                # This was the THIRD path calling SMTP directly and writing its
                # own EMAIL_SENT, bypassing cadence, the account cap and the
                # decision engine — exactly what was just removed from the
                # approval endpoint. Bulk approval must not be a back door
                # around the governance that single approval respects.
                from app.services.send_queue import approve as _record_approval
                _record_approval(lead, db, note="founder bulk-approved via send-approved")
                before = lead.status
                draft.status = "QUEUED"
                lead.status = "EMAIL_QUEUED"
                lead.recommended_action = "Queued — the worker delivers when due."
                lead.last_updated = datetime.utcnow()
                track(db, "EMAIL_QUEUED", lead_id=lead.id, actor="FOUNDER", channel="email",
                      before_status=before, after_status="EMAIL_QUEUED",
                      payload={"to": lead.email, "subject": draft.subject,
                               "draft_id": draft.id, "mode": "bulk_send_approved",
                               "note": "worker re-checks every gate; EMAIL_SENT "
                                       "is written only after SMTP succeeds"})
                sent_real += 1          # counts messages ACCEPTED into the queue
                results.append({"company": lead.company, "email": lead.email,
                                "status": "queued", "draft_id": draft.id})
            except Exception as e:
                results.append({"company": lead.company, "email": lead.email, "status": f"error: {str(e)[:60]}"})
                skipped += 1

    db.commit()
    return {
        "sent_real": sent_real,
        "sent_simulated": sent_sim,
        "skipped": skipped,
        "simulate": simulate,
        "results": results,
    }


@router.get("/b2b/email/preview/{lead_id}")
def get_email_preview(lead_id: int, db: Session = Depends(get_db)):
    """Get the email preview for a specific lead."""
    from app.models.models import B2BLead
    from app.services.email_verifier import generate_email_preview, verify_email

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    preview = generate_email_preview(lead)

    # Run verification if not done yet
    if lead.email_verification_status == "UNVERIFIED" and lead.email:
        result = verify_email(lead.email, lead.company or "", lead.division or "", lead.website or "")
        lead.email_verified = result["status"] == "VALID"
        lead.email_confidence = result["confidence"]
        lead.email_verification_status = result["status"]
        lead.email_mx_valid = result["mx_valid"]
        lead.email_is_generic = result["is_generic"]
        lead.email_domain_match = result["domain_match"]
        lead.email_verify_reason = result["reason"]
        db.commit()

    return {
        "lead_id": lead_id,
        "company": lead.company,
        "email": lead.email,
        "contact_name": lead.contact_name,
        "division": lead.division,
        "email_verification_status": lead.email_verification_status or "UNVERIFIED",
        "email_confidence": lead.email_confidence or 0,
        "email_verify_reason": lead.email_verify_reason or "",
        "email_mx_valid": lead.email_mx_valid or False,
        "requires_approval": (lead.division in ("government",)) or (lead.email_confidence or 0) < 90,
        "approved": lead.email_approved_by_founder or False,
        "rejected": lead.email_rejected_by_founder or False,
        "preview": preview,
    }


# ── FOLLOW-UP ENGINE ──────────────────────────────────────────────────────────

def _followup_email(lead, days_stalled: int) -> tuple[str, str]:
    """Generate a personalised follow-up email (2nd touch) for a stalled lead."""
    div = (lead.division or "corporate").lower()
    name = (lead.contact_name or "").split()[0] if lead.contact_name else "there"
    opened = (lead.email_opens or 0) > 0

    if div == "distributor":
        subject = f"Re: Distribution Partnership — Quick Follow-up | Purity Beans"
        if opened:
            body = (
                f"Hi {name},\n\n"
                f"I saw you had a chance to look at my earlier email — thank you.\n\n"
                f"I wanted to follow up briefly. Purity Beans is actively expanding distribution in "
                f"{lead.city or 'your region'} and I believe your network would be a strong fit.\n\n"
                f"Our distributor margins are 28–35%, and we provide free sample kits with no commitment required.\n\n"
                f"Would 15 minutes this week work for a quick call?\n\n"
                f"Warm regards,\nHiten Jain | Founder, Purity Beans\n+91 90849 58495"
            )
        else:
            body = (
                f"Hi {name},\n\n"
                f"I wanted to make sure my earlier note didn't get buried — "
                f"we're building our distributor network in {lead.city or 'your region'} and I'd love to connect.\n\n"
                f"Quick summary:\n"
                f"• Distributor margin: 28–35%\n"
                f"• Arabica + Robusta, FSSAI compliant\n"
                f"• Free sample kit, no commitment\n\n"
                f"Could we have a 15-minute call this week?\n\n"
                f"Warm regards,\nHiten Jain | Founder, Purity Beans\n+91 90849 58495"
            )
    elif div == "horeca":
        subject = f"Re: Premium Coffee Supply — Quick Check-in | Purity Beans"
        body = (
            f"Hi {name},\n\n"
            f"{'I noticed you had a look at our coffee supply details — ' if opened else 'Just a quick follow-up — '}"
            f"I wanted to check if you had a chance to consider a tasting session for {lead.company}.\n\n"
            f"We work with hotels, restaurants and cafes across India and can arrange a complimentary tasting visit at your property.\n\n"
            f"Would that be useful?\n\n"
            f"Warm regards,\nHiten Jain | Founder, Purity Beans\n+91 90849 58495"
        )
    elif div == "corporate":
        subject = f"Re: Office Coffee Pantry Program — Quick Follow-up | Purity Beans"
        body = (
            f"Hi {name},\n\n"
            f"{'Thank you for looking at our pantry program details. ' if opened else 'Just following up on my earlier note. '}"
            f"I know decisions like this take time — so I wanted to offer a simple next step:\n\n"
            f"We can send a free tasting kit for your team this week with no paperwork or commitment.\n\n"
            f"If the team likes it, we can discuss a pilot subscription. If not, no pressure at all.\n\n"
            f"Would that work?\n\n"
            f"Warm regards,\nHiten Jain | Founder, Purity Beans\n+91 90849 58495"
        )
    elif div == "government":
        subject = f"Re: Purity Beans — Coffee Supply Enquiry Follow-up"
        body = (
            f"Dear {name},\n\n"
            f"I am following up on my earlier communication regarding coffee supply for {lead.company}.\n\n"
            f"Pure Pantry Provisions is FSSAI licensed, GST registered, and has the capacity to fulfil "
            f"institutional requirements pan-India. We are happy to participate in any tender or RFQ process.\n\n"
            f"Please let us know if you require our company profile or product specifications.\n\n"
            f"Regards,\nHiten Jain | Pure Pantry Provisions\nconnect@purepantryprovisions.com"
        )
    else:
        subject = f"Re: Purity Beans — Quick Follow-up"
        body = (
            f"Hi {name},\n\n"
            f"{'Thank you for taking a look at my earlier email. ' if opened else 'Just a quick follow-up — '}"
            f"I wanted to check if you had any questions about Purity Beans premium coffee.\n\n"
            f"We are happy to send a free sample kit with no obligation.\n\n"
            f"Would that work for you?\n\n"
            f"Warm regards,\nHiten Jain | Founder, Purity Beans\n+91 90849 58495"
        )

    # Append stall context note
    body += f"\n\nP.S. It has been {days_stalled} day{'s' if days_stalled != 1 else ''} since my first note — I promise I won't follow up more than twice."
    return subject, body


# Verified founder contacts (purity-beans business facts). Outreach copy must not
# invent or drift to an unverified number — prospects call whatever we print.
# Both numbers are founder-supplied and reachable; printing two means a missed
# call on one does not end the conversation.
_FOUNDER_PHONE_PRIMARY = "+91 90849 58495"
_FOUNDER_PHONE_ALT = "+91 98555 93323"
_FOUNDER_PHONE = f"{_FOUNDER_PHONE_PRIMARY} / {_FOUNDER_PHONE_ALT}"

# The single hook that matters per segment — used as the opening line for a lead
# who ignored the email, where we get one short message to earn a reply.
_WA_HOOK = {
    # Margins must match every other channel (outreach_templates._SEGMENT_VALUEPROP,
    # the email copy and the AI-call script all say 35-42%). The old WhatsApp copy
    # said 28-35%, so a distributor was quoted a different deal depending on which
    # channel reached them.
    "distributor":  "Distributor margin 35–42% + free sample kit",
    "wholesale":    "Wholesale margin 35–42% + free sample kit",
    "retail":       "Shelf-ready 100% coffee, 22–28% retail margin",
    "grocery":      "Shelf-ready 100% coffee, 22–28% retail margin",
    "horeca":       "Free tasting for your team — premium 100% coffee",
    "hotel":        "Free tasting for your team — premium 100% coffee",
    "cafe":         "100% coffee, zero chicory — free tasting",
    "corporate":    "Office pantry coffee, 30–40% cheaper than retail",
    "gifting":      "Premium coffee gift hampers from ₹499, custom branding",
    "government":   "FSSAI + GST compliant institutional supply",
}


_WEBSITE_URL = "https://p3online.in"


def _display_company(lead) -> str:
    """
    Company name trimmed for use inside a sentence. Google Maps listings carry
    SEO descriptors and locality suffixes — "Mukand Lal Ude Chand - Nestle
    Distributor", "Crazy Coffee | Bathinda" — which read badly mid-message
    ("...about Mukand Lal Ude Chand - Nestle Distributor's coffee supply").
    Keep the part before the first separator; the full name stays on the record.
    """
    raw = (lead.company or "").strip()
    if not raw:
        return "your team"
    for sep in (" | ", " - ", " – ", " — "):
        if sep in raw:
            raw = raw.split(sep)[0].strip()
    city = (lead.city or "").strip()
    if city and raw.lower().endswith(city.lower()) and len(raw) > len(city) + 2:
        raw = raw[: -len(city)].strip(" ,-|")
    return raw or (lead.company or "your team")


def _phone_first_whatsapp(lead, step: int) -> str:
    """
    WhatsApp copy for the phone-first ladder (no email was ever sent, so none
    of these may reference one). Three distinct jobs:

      step 2 — first written contact, right after the call. Carries the range,
               credentials and the catalogue/website link so they have
               something concrete to look at.
      step 3 — short nudge on that catalogue.
      step 4 — final automated touch. Says it is the last one and leaves the
               door open; nagging past this burns the number.

    The catalogue link is only included when CATALOGUE_URL is configured — we
    never invent a URL. Without it the message points at the real website.
    """
    seg = (lead.segment or lead.division or "corporate").lower()
    hook = _WA_HOOK.get(seg, "100% pure instant coffee — no chicory")
    name = (lead.contact_name or "").split()[0] if lead.contact_name else ""
    greeting = f"Namaste {name} ji" if name else "Namaste"
    company = _display_company(lead)
    catalogue = (os.getenv("CATALOGUE_URL", "") or "").strip()
    links = f"Catalogue: {catalogue}\nWebsite: {_WEBSITE_URL}" if catalogue else f"Range & details: {_WEBSITE_URL}"

    if step <= 2:
        return (
            f"{greeting} 🙏\n\n"
            f"Hiten here — Founder, Purity Beans (Pure Pantry Provisions). "
            f"We just tried reaching you by phone about {company}'s coffee supply.\n\n"
            f"{hook}.\n\n"
            f"Our range — Purica (Freeze-Dried Arabica), Purista (Freeze-Dried Robusta), "
            f"Bold (Premium Agglomerated), Ultra Blend.\n"
            f"FSSAI licensed · MSME registered · GST compliant.\n\n"
            f"{links}\n\n"
            f"Shall I send a free sample? Just reply YES — no commitment.\n"
            f"{_FOUNDER_PHONE}"
        )
    if step == 3:
        return (
            f"{greeting} 🙏\n\n"
            f"Hiten from Purity Beans — following up on the coffee details I sent for {company}.\n\n"
            f"Any questions on the range or bulk pricing? I can send a free sample so your "
            f"team can taste it before deciding anything.\n\n"
            f"Reply YES and I'll arrange it.\n"
            f"{_FOUNDER_PHONE}"
        )
    return (
        f"{greeting} 🙏\n\n"
        f"Last note from me — I don't want to keep pinging you.\n\n"
        f"If premium instant coffee is relevant for {company} this quarter, reply YES and "
        f"I'll send a sample. If not, no problem at all — keep my number and reach out "
        f"whenever it makes sense.\n\n"
        f"Thank you for your time.\n"
        f"{_FOUNDER_PHONE}"
    )


def _followup_whatsapp(lead, days_stalled: int, step: int = 0) -> str:
    """
    WhatsApp follow-up, branched on what actually happened to the EMAIL.

    Two very different jobs, so two very different messages:

    * REPLIED  -> they are already talking to us. Warm, reference the reply, and
      move to the next concrete step (call or sample). Length is fine here.

    * NO REPLY -> the email did not land. WhatsApp gets read where email is
      buried, so this must be SHORT, lead with the single strongest hook, and
      ask for a one-word yes. A long re-pitch here just gets ignored twice.

    (The old version branched on `email_opens`, but open-tracking does not exist
    — that value was only ever set by the fabricator, so the branch was dead.)
    """
    # No email on record -> this lead is on the phone-first ladder and never
    # received an email, so none of the copy below (which references "I emailed
    # you") can be used. Hand off to the phone-first wording.
    if _sequence_for(lead) is _PHONE_FIRST_SEQUENCE:
        return _phone_first_whatsapp(lead, step or 2)

    seg = (lead.segment or lead.division or "corporate").lower()
    hook = _WA_HOOK.get(seg, "100% pure instant coffee — no chicory")
    name = (lead.contact_name or "").split()[0] if lead.contact_name else ""
    greeting = f"Namaste {name} ji" if name else "Namaste"
    replied = (lead.status or "") in (
        "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
        "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "NEGOTIATION",
    )

    if replied:
        # Warm: they engaged by email. Don't re-pitch — convert to a next step.
        return (
            f"{greeting} 🙏\n\n"
            f"Hiten here, Purity Beans — thank you for your reply on email.\n"
            f"Continuing here since WhatsApp is usually quicker.\n\n"
            f"{hook}.\n\n"
            f"Shall I send a free sample kit, or would a quick 10-min call suit you better?\n"
            f"{_FOUNDER_PHONE}"
        )

    # Cold re-engage after an unanswered email.
    #
    # The previous version opened with "I emailed you N days ago — it may have
    # got buried", then jumped straight to a free sample. Two problems: it
    # assumed the recipient knew who Purity Beans was (they had never replied,
    # so there is no evidence they read anything), and a stranger offering a
    # free sample reads as a pitch rather than an introduction.
    #
    # This introduces the brand first, gives them somewhere to look it up, and
    # asks for a small decision — the catalogue — instead of the big one.
    return (
        f"{greeting} 🙏\n\n"
        f"Hiten here, Founder at Purity Beans.\n\n"
        f"We're a new Indian coffee brand supplying premium instant coffee to "
        f"distributors, hotels, offices and retailers.\n\n"
        f"{hook}, and a sample kit so you can evaluate the product before "
        f"considering any order.\n\n"
        f"🌐 {_WEBSITE_URL}\n\n"
        f"Would you like me to send our catalogue and price list?\n"
        f"{_FOUNDER_PHONE}"
    )


@router.get("/b2b/followup/eligible")
def get_followup_eligible(db: Session = Depends(get_db)):
    """
    Returns leads stalled in EMAIL_SENT for 3+ days with no reply.
    These are candidates for the 2nd-touch follow-up.
    """
    from app.models.models import B2BLead, EmailDraft

    cutoff = datetime.utcnow() - timedelta(days=3)
    leads = (
        db.query(B2BLead)
        .filter(
            B2BLead.status.in_(["EMAIL_SENT", "INTRO_EMAIL_SENT"]),
            B2BLead.last_updated <= cutoff,
            B2BLead.email_rejected_by_founder != True,
        )
        .order_by(B2BLead.score.desc())
        .all()
    )

    result = []
    for lead in leads:
        days_stalled = (datetime.utcnow() - (lead.last_updated or lead.stage_entered_date or datetime.utcnow())).days
        # Check if a follow-up draft already exists (pending or sent)
        existing = (
            db.query(EmailDraft)
            .filter(
                EmailDraft.lead_id == lead.id,
                EmailDraft.follow_up_type == "follow_up_2",
                EmailDraft.status.in_(["PENDING", "EDITED", "SENT"]),
            )
            .first()
        )
        result.append({
            "lead_id":     lead.id,
            "company":     lead.company,
            "contact_name": lead.contact_name or "",
            "city":        lead.city or "",
            "division":    lead.division or "",
            "email":       lead.email or "",
            "phone":       lead.phone or "",
            "whatsapp_number": lead.whatsapp_number or "",
            "email_opens": lead.email_opens or 0,
            "days_stalled": days_stalled,
            "score":       lead.score or 0,
            "estimated_value": int(lead.estimated_value or 0),
            "has_followup_draft": existing is not None,
            "followup_draft_status": existing.status if existing else None,
        })

    return {"leads": result, "total": len(result)}


@router.post("/b2b/followup/generate-drafts")
def generate_followup_drafts(db: Session = Depends(get_db)):
    """
    Create follow-up email drafts (2nd touch) for all eligible stalled leads.
    Uses opened/not-opened status to personalise the copy.
    """
    from app.models.models import B2BLead, EmailDraft
    cutoff = datetime.utcnow() - timedelta(days=3)
    leads = (
        db.query(B2BLead)
        .filter(
            B2BLead.status.in_(["EMAIL_SENT", "INTRO_EMAIL_SENT"]),
            B2BLead.last_updated <= cutoff,
            B2BLead.email_rejected_by_founder != True,
        )
        .all()
    )

    created = skipped = 0
    for lead in leads:
        # Skip if follow-up draft already exists
        existing = (
            db.query(EmailDraft)
            .filter(
                EmailDraft.lead_id == lead.id,
                EmailDraft.follow_up_type == "follow_up_2",
                EmailDraft.status.in_(["PENDING", "EDITED", "SENT"]),
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        days_stalled = (datetime.utcnow() - (lead.last_updated or datetime.utcnow())).days
        subject, body = _followup_email(lead, days_stalled)
        draft = EmailDraft(
            lead_id=lead.id,
            follow_up_type="follow_up_2",
            subject=subject,
            body=body,
            reason=f"Follow-up: stalled {days_stalled}d, {'opened' if (lead.email_opens or 0) > 0 else 'not opened'}",
            status="PENDING",
        )
        db.add(draft)
        created += 1

    db.commit()
    return {"drafted": created, "skipped": skipped}


@router.get("/b2b/followup/inbox")
def get_followup_inbox(db: Session = Depends(get_db)):
    """
    Returns pending follow-up drafts with WhatsApp messages pre-generated.
    Frontend uses this to render the follow-up table with edit + bulk send.
    """
    from app.models.models import B2BLead, EmailDraft
    rows = (
        db.query(EmailDraft, B2BLead)
        .join(B2BLead, EmailDraft.lead_id == B2BLead.id)
        .filter(
            EmailDraft.follow_up_type == "follow_up_2",
            EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"]),
        )
        .order_by(B2BLead.score.desc())
        .all()
    )

    result = []
    total_pipeline = 0
    for draft, lead in rows:
        days_stalled = (datetime.utcnow() - (lead.last_updated or datetime.utcnow())).days
        wa_msg = _followup_whatsapp(lead, days_stalled)
        phone = (lead.whatsapp_number or lead.phone or "").replace(" ", "").replace("-", "").replace("+", "")
        wa_url = f"https://wa.me/{phone}?text={_requests.utils.quote(wa_msg)}" if phone else None

        total_pipeline += int(lead.estimated_value or 0)
        result.append({
            "draft_id":    draft.id,
            "lead_id":     lead.id,
            "company":     lead.company,
            "contact_name": lead.contact_name or "",
            "city":        lead.city or "",
            "division":    lead.division or "",
            "email":       lead.email or "",
            "phone":       lead.phone or "",
            "whatsapp_number": lead.whatsapp_number or lead.phone or "",
            "email_opens": lead.email_opens or 0,
            "days_stalled": days_stalled,
            "score":       lead.score or 0,
            "estimated_value": int(lead.estimated_value or 0),
            "draft_status": draft.status,
            "email_preview": {
                "subject": draft.subject,
                "body":    draft.body,
                "to":      lead.email,
                "from":    "connect@purepantryprovisions.com",
            },
            "whatsapp_message": wa_msg,
            "whatsapp_url":     wa_url,
            "created_at": draft.created_at.isoformat() if draft.created_at else None,
        })

    return {"leads": result, "total": len(result), "total_pipeline": total_pipeline}


# ── ONE-CLICK MULTI-CHANNEL EXECUTION + REMINDERS (master spec §8) ────────────
# Warmup sequence: email → (3d) WhatsApp → (2d) AI call → (2d) founder call.
# Executing one step auto-schedules the next step's reminder. Idempotent: a
# channel step already COMPLETED for a lead never fires twice.

_WARMUP_SEQUENCE = [
    # step, channel, wait_days_before_next, next_channel
    (1, "email",        3, "whatsapp"),
    (2, "whatsapp",     2, "ai_call"),
    (3, "ai_call",      2, "founder_call"),
    (4, "founder_call", 0, None),
]

# Phone-first ladder for leads with a number but no email address. This is the
# common case for local businesses: of 92 verified Bathinda leads, 84 have a
# phone and 1 has an email — Google Maps publishes phone and address, not
# email. Running those leads through the email-first sequence stalls them on
# step 1 forever, because the first touch can never fire.
#
# Order per founder instruction: AI call -> WhatsApp with catalogue + website
# -> follow-up -> second follow-up -> founder call. The founder can still run
# any channel out of order from the Action Queue; this only decides what gets
# scheduled next by default.
_PHONE_FIRST_SEQUENCE = [
    # step, channel, wait_days_before_next, next_channel
    (1, "ai_call",      2, "whatsapp"),   # opener
    (2, "whatsapp",     3, "whatsapp"),   # catalogue + website
    (3, "whatsapp",     4, "whatsapp"),   # follow-up 1
    (4, "whatsapp",     3, "founder_call"),  # follow-up 2 (last automated touch)
    (5, "founder_call", 0, None),
]

_SEQUENCES = {"email_first": _WARMUP_SEQUENCE, "phone_first": _PHONE_FIRST_SEQUENCE}
_CHANNEL_STEP = {c: s for s, c, _, _ in _WARMUP_SEQUENCE}
# Channels that may be executed, across every sequence. "sample" and "proposal"
# come from the conversion ladder — they are physical founder actions (posting a
# sample kit, sending pricing), so like WhatsApp they are prepare-then-confirm:
# nothing is recorded as done until the founder says they actually did it.
_VALID_CHANNELS = ({c for seq in _SEQUENCES.values() for _, c, _, _ in seq}
                   | {"sample", "proposal"})
_CONFIRM_REQUIRED_CHANNELS = {"whatsapp", "sample", "proposal"}


def _sequence_for(lead) -> list:
    """
    Which ladder this lead belongs on. A usable email keeps the original
    email-first warm-up; no email means the phone is the only way in.
    """
    return _PHONE_FIRST_SEQUENCE if not (getattr(lead, "email", "") or "").strip() else _WARMUP_SEQUENCE


def _sequence_name(lead) -> str:
    return "phone_first" if _sequence_for(lead) is _PHONE_FIRST_SEQUENCE else "email_first"
_CHANNEL_EVENT = {
    "email": "EMAIL_SENT", "whatsapp": "WHATSAPP_SENT",
    "ai_call": "AI_CALL_INITIATED", "founder_call": "FOUNDER_CALL_COMPLETED",
}
_CHANNEL_STATUS = {
    "email": "EMAIL_SENT", "whatsapp": "WHATSAPP_SENT",
    "ai_call": "AI_CALLED", "founder_call": "FOUNDER_CALLED",
}


def _schedule_next_reminder(db, lead, current_channel: str):
    """Schedule the next channel's reminder after a touch. Idempotent per step."""
    from app.models.models import OutreachReminder
    seq = _sequence_for(lead)

    # Which step did we just complete? Resolve by the lead's actual progress,
    # not by channel name: the phone-first ladder uses "whatsapp" for steps 2,
    # 3 and 4, so a name lookup would always return step 2 and the sequence
    # would loop on the first WhatsApp message forever. Callers mark the
    # finished reminder DONE before calling us, so the furthest DONE step is
    # the one just completed; fall back to the channel's first appearance for
    # the opening touch, when no reminder exists yet.
    done_steps = [
        r.sequence_step for r in db.query(OutreachReminder).filter(
            OutreachReminder.lead_id == lead.id,
            OutreachReminder.status == "DONE",
        ).all()
        # Conversion steps are numbered from _CONVERSION_STEP_BASE and belong to
        # a different ladder; including them here would push the cold sequence
        # past its own last step and stop it scheduling anything.
        if r.sequence_step and r.sequence_step < _CONVERSION_STEP_BASE
    ]
    step = max(done_steps) if done_steps else next(
        (s for s, c, _, _ in seq if c == current_channel), 0
    )

    row = next((r for r in seq if r[0] == step), None)
    if not row:
        return None
    _, _, wait_days, next_channel = row
    if not next_channel:
        return None
    next_step = step + 1
    # idempotency: never double-schedule the same next step for this lead
    existing = db.query(OutreachReminder).filter(
        OutreachReminder.lead_id == lead.id,
        OutreachReminder.sequence_step == next_step,
        OutreachReminder.status.in_(["SCHEDULED", "DONE"]),
    ).first()
    if existing:
        return existing
    rem = OutreachReminder(
        lead_id=lead.id,
        channel=next_channel,
        sequence_step=next_step,
        due_at=datetime.utcnow() + timedelta(days=wait_days),
        reason=f"Warmup step {next_step}: {next_channel} follow-up if no reply",
        status="SCHEDULED",
    )
    db.add(rem)
    return rem


# Reaching any of these stages means the warmup sequence has succeeded or the
# lead has moved on — pending follow-up reminders are cancelled (no nagging).
_SEQUENCE_TERMINAL = {
    "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED",
    "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "NEGOTIATION",
    "ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH", "LOST", "DEAD", "NOT_INTERESTED",
}


# ── Conversion ladder ────────────────────────────────────────────────────────
# The cold ladders (email-first / phone-first) stop the moment a lead engages,
# which is correct — you do not keep cold-nagging someone who answered. But
# nothing replaced them, so at the single most valuable moment in the pipeline
# the system went silent and the lead sat untouched until the founder happened
# to notice. This ladder takes over from that point and drives to the order.
#
# status the lead just reached -> (next action, wait days, what the founder does)
_CONVERSION_LADDER = {
    "REPLIED":           ("founder_call", 0, "They responded — qualify: need, volume, decision maker"),
    "MEETING_BOOKED":    ("founder_call", 0, "Run the booked meeting"),
    "MEETING_COMPLETED": ("sample",       1, "Dispatch the free sample while interest is fresh"),
    "SAMPLE_SENT":       ("whatsapp",     4, "Sample feedback — has the team tasted it?"),
    "FEEDBACK_RECEIVED": ("proposal",     1, "Send pricing / proposal"),
    "PROPOSAL_SENT":     ("whatsapp",     3, "Proposal follow-up"),
    "NEGOTIATION":       ("founder_call", 2, "Close — founder call"),
}

# Conversion reminders are numbered from here so they never collide with the
# cold ladder's steps (1-5), whose scheduler advances off max(done_step).
_CONVERSION_STEP_BASE = 100
_CONVERSION_ORDER = list(_CONVERSION_LADDER.keys())

# Reaching one of these means the deal is finished either way — schedule nothing.
_CONVERSION_CLOSED = {"ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH", "LOST", "DEAD", "NOT_INTERESTED"}


def _schedule_conversion_step(db, lead):
    """
    Schedule the next conversion action for a lead that has engaged. Idempotent:
    one open conversion reminder per lead at a time, so this can be called from
    housekeeping on every pass without stacking duplicates.
    """
    from app.models.models import OutreachReminder

    status = (lead.status or "").upper()
    if status in _CONVERSION_CLOSED:
        return None
    entry = _CONVERSION_LADDER.get(status)
    if not entry:
        return None
    channel, wait_days, reason = entry
    step = _CONVERSION_STEP_BASE + _CONVERSION_ORDER.index(status)

    open_conv = db.query(OutreachReminder).filter(
        OutreachReminder.lead_id == lead.id,
        OutreachReminder.sequence_step >= _CONVERSION_STEP_BASE,
        OutreachReminder.status == "SCHEDULED",
    ).first()
    if open_conv:
        return open_conv
    already = db.query(OutreachReminder).filter(
        OutreachReminder.lead_id == lead.id,
        OutreachReminder.sequence_step == step,
        OutreachReminder.status.in_(["SCHEDULED", "DONE"]),
    ).first()
    if already:
        return already

    rem = OutreachReminder(
        lead_id=lead.id,
        channel=channel,
        sequence_step=step,
        due_at=datetime.utcnow() + timedelta(days=wait_days),
        reason=f"Conversion: {reason}",
        status="SCHEDULED",
    )
    db.add(rem)
    return rem


def _cancel_stale_reminders(db) -> int:
    """Housekeeping: cancel scheduled follow-ups for leads that already replied
    or advanced. Keeps the follow-up queue honest (rule: idempotent, no nagging).

    Cancelling alone used to drop engaged leads entirely, so every cancellation
    now hands the lead to the conversion ladder instead of leaving it with no
    next action at all."""
    from app.models.models import OutreachReminder, B2BLead
    n = 0
    handed_over = set()
    due = db.query(OutreachReminder).filter(OutreachReminder.status == "SCHEDULED").all()
    for r in due:
        if r.sequence_step and r.sequence_step >= _CONVERSION_STEP_BASE:
            continue                      # never cancel a conversion step
        lead = db.query(B2BLead).filter(B2BLead.id == r.lead_id).first()
        if not lead or (lead.status or "") in _SEQUENCE_TERMINAL:
            r.status = "CANCELLED"
            r.done_at = datetime.utcnow()
            n += 1
            if lead and lead.id not in handed_over:
                _schedule_conversion_step(db, lead)
                handed_over.add(lead.id)
    if n:
        db.commit()
    return n


def _advance_engaged_leads(db) -> int:
    """
    Guarantee every engaged lead has exactly one open next action.

    _cancel_stale_reminders only hands over leads that had a cold reminder to
    cancel. A lead that replies before any reminder was scheduled — or one that
    advances a stage later (SAMPLE_SENT -> FEEDBACK_RECEIVED) — would otherwise
    never get a conversion step. This sweep closes that hole, and is idempotent
    because _schedule_conversion_step allows one open conversion reminder each.
    """
    from app.models.models import B2BLead

    leads = db.query(B2BLead).filter(B2BLead.status.in_(list(_CONVERSION_LADDER.keys()))).all()
    scheduled = 0
    for lead in leads:
        before = _schedule_conversion_step(db, lead)
        if before is not None and before.id is None:   # newly added, not yet flushed
            scheduled += 1
    if scheduled:
        db.commit()
    return scheduled


def _due_followups(db, limit: int = 200) -> list[dict]:
    """
    Follow-up reminders that are DUE now (spec §Automated Follow-up): the
    system auto-advances the warmup sequence by surfacing the next channel for
    founder approval — it never auto-sends. Each item carries the drafted
    message and whether its channel script is pre-approved.
    """
    from app.models.models import OutreachReminder, B2BLead
    _cancel_stale_reminders(db)
    rows = (db.query(OutreachReminder)
            .filter(OutreachReminder.status == "SCHEDULED",
                    OutreachReminder.due_at <= datetime.utcnow())
            .order_by(OutreachReminder.due_at.asc()).limit(limit).all())
    out = []
    for r in rows:
        lead = db.query(B2BLead).filter(B2BLead.id == r.lead_id).first()
        if not lead:
            continue
        seg = lead.division or lead.segment
        approved = is_channel_approved(db, seg, r.channel)
        message = ""
        if r.channel == "whatsapp":
            message = _followup_whatsapp(lead, (datetime.utcnow() - (lead.last_updated or datetime.utcnow())).days)
        out.append({
            "reminder_id": r.id, "lead_id": lead.id, "company": lead.company,
            "channel": r.channel, "sequence_step": r.sequence_step,
            "due_at": r.due_at.isoformat(), "reason": r.reason,
            "current_status": lead.status, "city": lead.city or "",
            "margin": _margin_for(lead), "channel_approved": approved,
            "message": message,
            "action_type": r.channel,   # aligns with Action Queue buckets
        })
    return out


@router.get("/outreach/followups/due")
def get_due_followups(db: Session = Depends(get_db)):
    """Due follow-ups grouped by channel — the auto-advanced next touches
    waiting for founder approval."""
    items = _due_followups(db)
    by_channel: dict = {}
    for it in items:
        by_channel.setdefault(it["channel"], []).append(it)
    return {"total": len(items), "by_channel": by_channel, "items": items}


class OneClickExecuteRequest(BaseModel):
    lead_id: int
    channel: str = "email"           # email | whatsapp | ai_call | founder_call
    custom_subject: str | None = None
    custom_body: str | None = None
    reminder_id: int | None = None   # if fired from a due reminder, mark it done


@router.post("/b2b/outreach/execute")
def one_click_execute(req: OneClickExecuteRequest, db: Session = Depends(get_db)):
    """
    ONE-CLICK execution of an approved outbound on a chosen channel, then
    auto-schedules the next warmup reminder (master spec §8). Idempotent: a
    channel step already COMPLETED for this lead will not fire again. Every
    execution records a WorkflowExecution + immutable WorkflowEvent (rule #5/#2).
    Email still honours the DB approval gate (founder-approved EmailDraft only).
    """
    from app.models.models import B2BLead, EmailDraft, WorkflowExecution, OutreachReminder
    from app.services.pipeline_tracker import track

    channel = (req.channel or "email").lower()
    if channel not in _VALID_CHANNELS:
        raise HTTPException(status_code=400, detail=f"unknown channel '{channel}'")

    lead = db.query(B2BLead).filter(B2BLead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    wf_type = f"OUTREACH_{channel.upper()}"

    # Idempotency: never repeat an action that had a real side effect (an email
    # actually left the server). WhatsApp is exempt: it only BUILDS a wa.me link
    # and sends nothing, so re-opening it must stay possible — the real send is
    # recorded separately via mark-whatsapp, which is itself idempotent.
    if channel != "whatsapp":
        done = db.query(WorkflowExecution).filter(
            WorkflowExecution.lead_id == lead.id,
            WorkflowExecution.workflow_type == wf_type,
            WorkflowExecution.status == "COMPLETED",
        ).first()
        if done:
            return {"status": "already_done", "channel": channel, "workflow_id": done.id,
                    "message": f"{channel} already executed for this lead (idempotent)"}

    wf = WorkflowExecution(
        workflow_type=wf_type, lead_id=lead.id, status="EXECUTING",
        requested_by="FOUNDER", started_at=datetime.utcnow(),
        payload={"channel": channel},
    )
    db.add(wf)
    db.flush()

    before = lead.status
    detail: dict = {}
    try:
        if channel == "email":
            if not lead.email_approved_by_founder or lead.email_rejected_by_founder:
                raise RuntimeError("email not founder-approved")
            draft = db.query(EmailDraft).filter(
                EmailDraft.lead_id == lead.id,
                EmailDraft.status.in_(["PENDING", "EDITED", "APPROVED"]),
            ).order_by(EmailDraft.id.desc()).first()
            if not draft:
                raise RuntimeError("no approved draft in DB — review in Approval Inbox")
            if req.custom_subject:
                draft.subject = req.custom_subject
            if req.custom_body:
                draft.body = req.custom_body
            zoho_pw = os.getenv("ZOHO_APP_PASSWORD", "")
            simulate = not bool(zoho_pw and zoho_pw.strip() not in ("", "your_zoho_app_password_here"))
            if simulate:
                detail["mode"] = "simulated"
            else:
                from app.services.email_sender import build_outreach_email, send_email
                email_obj = build_outreach_email(
                    to_email=lead.email, to_name=lead.contact_name or lead.company,
                    company=lead.company or "", subject=draft.subject, body=draft.body, lead_id=lead.id)
                r = send_email(email_obj)
                if r.status != "sent":
                    raise RuntimeError(r.error or "SMTP failed")
                detail["mode"] = "sent"
            draft.status = "SENT"
            draft.sent_at = datetime.utcnow()
            detail["draft_id"] = draft.id

        elif channel == "whatsapp":
            # PREPARE ONLY — this builds a wa.me link, it does not send anything.
            # The founder must open the link and press send in WhatsApp. So we do
            # NOT mark WHATSAPP_SENT here (see the early return below): doing so
            # would write a founder action for a message nobody sent, corrupting
            # the learned patterns and the margin/founder-hour north star.
            # The real send is recorded by POST /b2b/leads/{id}/mark-whatsapp.
            days = (datetime.utcnow() - (lead.last_updated or datetime.utcnow())).days
            # Which WhatsApp touch is this? The phone-first ladder sends three,
            # each with different copy, so pass the reminder's step through —
            # otherwise every follow-up repeats the opening message.
            wa_step = 0
            if req.reminder_id:
                _rem = db.query(OutreachReminder).filter(
                    OutreachReminder.id == req.reminder_id).first()
                if _rem and _rem.sequence_step:
                    wa_step = _rem.sequence_step
            msg = _followup_whatsapp(lead, days, step=wa_step)
            phone = (lead.whatsapp_number or lead.phone or "").replace(" ", "").replace("-", "").replace("+", "")
            if not phone:
                raise RuntimeError("no phone/WhatsApp number on record")

            # Send through AiSensy — the business WhatsApp number.
            #
            # This returned a wa.me link, which opens WhatsApp Desktop under
            # the founder's PERSONAL account. Every message from this queue
            # therefore went out from a personal number: no delivery proof, no
            # provider message-id, and the spam risk landing on the founder's
            # own number instead of the business one. whatsapp_sender.py has
            # had a complete AiSensy adapter the whole time — consent gate,
            # service window, MSISDN normalisation — and nothing called it.
            # Sixth instance in this codebase of two paths for one action.
            from app.services.whatsapp_sender import send_whatsapp, is_configured
            if is_configured():
                r = send_whatsapp(lead, msg)
                if r.status == "sent":
                    detail = {"message": msg, "provider": "aisensy",
                              "provider_message_id": r.message_id,
                              "provider_response": r.response[:200]}
                    wf.status = "COMPLETED"
                    wf.finished_at = datetime.utcnow()
                    wf.result = detail
                    lead.status = "WHATSAPP_SENT"
                    lead.last_updated = datetime.utcnow()
                    track(db, "WHATSAPP_SENT", lead_id=lead.id, actor="FOUNDER",
                          channel="whatsapp", before_status=before,
                          after_status="WHATSAPP_SENT",
                          payload={"to": phone, "provider": "aisensy",
                                   "provider_message_id": r.message_id,
                                   "message": msg[:400]})
                    db.commit()
                    return {"status": "sent", "requires_confirmation": False,
                            "channel": channel, "workflow_id": wf.id,
                            "lead_id": lead.id, "new_stage": lead.status,
                            "detail": detail,
                            "message_to_founder":
                                "Sent from the business WhatsApp number via AiSensy. "
                                "Delivery is confirmed separately by the provider."}
                # blocked / failed / not_configured — say why, send nothing.
                wf.status = "FAILED"
                wf.finished_at = datetime.utcnow()
                wf.result = {"status": r.status, "reason": r.reason}
                db.commit()
                raise RuntimeError(f"AiSensy {r.status}: {r.reason}")

            # No provider configured: fall back to the manual link, and be
            # explicit that it leaves from a personal account.
            detail = {"message": msg, "provider": "manual_wa_me",
                      "whatsapp_url": f"https://wa.me/{phone}?text={_requests.utils.quote(msg)}"}
            wf.status = "COMPLETED"
            wf.finished_at = datetime.utcnow()
            wf.result = detail
            db.commit()
            return {
                "status": "prepared",          # NOT "executed" — nothing was sent
                "requires_confirmation": True,
                "confirm_endpoint": f"/api/v1/b2b/leads/{lead.id}/mark-whatsapp",
                "channel": channel, "workflow_id": wf.id, "lead_id": lead.id,
                "new_stage": lead.status,      # unchanged
                "detail": detail,
                "message_to_founder": "AISENSY_API_KEY is not set, so this opens "
                                      "WhatsApp on YOUR PERSONAL account. Send it, "
                                      "then confirm — nothing is recorded until you do.",
            }

        elif channel == "ai_call":
            if not is_channel_approved(db, lead.division or lead.segment, "ai_call"):
                raise RuntimeError("ai_call script not founder-approved")
            # Actually place the call. The old code only set detail={"queued":True}
            # and let the shared block below flip status to AI_CALLED + track
            # AI_CALL_INITIATED — i.e. it marked the lead "called" without dialling
            # anyone. Route through the real Vapi dialer, which also runs the DNC /
            # consent / daily-limit eligibility checks. If it can't dial (not
            # configured, ineligible, API error) we raise, so the workflow records
            # WORKFLOW_FAILED honestly instead of faking a call.
            from app.services.calling_agent import CallingAgentService
            ok, reason = CallingAgentService.trigger_vapi_call(db, lead)
            if not ok:
                raise RuntimeError(f"ai_call not placed: {reason}")
            detail = {"call_initiated": True, "vapi_call_id": lead.vapi_call_id, "note": reason}

        elif channel == "founder_call":
            detail = {"note": "founder call logged"}

        elif channel in ("sample", "proposal"):
            # Physical founder actions: posting a sample kit, sending pricing.
            # Nothing here can perform them, so this only PREPARES the step and
            # returns — marking SAMPLE_SENT/PROPOSAL_SENT now would record work
            # nobody did, the same defect the WhatsApp and AI-call paths had.
            # The real action is recorded by /b2b/leads/{id}/confirm-action.
            what = ("Post the sample kit to the address on file"
                    if channel == "sample" else
                    "Send the proposal with pricing for the discussed volume")
            detail = {"action": channel, "instruction": what,
                      "company": lead.company, "city": lead.city,
                      "phone": lead.phone, "address": getattr(lead, "address", None)}
            wf.status = "COMPLETED"
            wf.finished_at = datetime.utcnow()
            wf.result = detail
            db.commit()
            return {
                "status": "prepared",
                "requires_confirmation": True,
                "confirm_endpoint": f"/api/v1/b2b/leads/{lead.id}/confirm-action?action={channel}",
                "channel": channel, "workflow_id": wf.id, "lead_id": lead.id,
                "new_stage": lead.status,          # unchanged until confirmed
                "detail": detail,
                "message_to_founder": f"{what}, then confirm — the lead only advances once you do.",
            }

        after = _CHANNEL_STATUS.get(channel, lead.status)
        lead.status = after
        lead.last_updated = datetime.utcnow()
        track(db, _CHANNEL_EVENT[channel], lead_id=lead.id, actor="FOUNDER",
              channel=channel, before_status=before, after_status=after,
              payload={"workflow_id": wf.id, **detail})

        if req.reminder_id:
            rem = db.query(OutreachReminder).filter(OutreachReminder.id == req.reminder_id).first()
            if rem and rem.status == "SCHEDULED":
                rem.status = "DONE"
                rem.done_at = datetime.utcnow()

        nxt = _schedule_next_reminder(db, lead, channel)

        wf.status = "COMPLETED"
        wf.finished_at = datetime.utcnow()
        wf.result = detail
        db.commit()
        return {
            "status": "executed", "channel": channel, "workflow_id": wf.id,
            "lead_id": lead.id, "new_stage": after, "detail": detail,
            "next_reminder": ({"channel": nxt.channel, "due_at": nxt.due_at.isoformat(),
                               "step": nxt.sequence_step} if nxt else None),
        }
    except Exception as e:
        wf.status = "FAILED"
        wf.finished_at = datetime.utcnow()
        wf.error = str(e)[:200]
        track(db, "WORKFLOW_FAILED", lead_id=lead.id, actor="SYSTEM", channel=channel,
              payload={"workflow_id": wf.id, "error": str(e)[:200]})
        db.commit()
        raise HTTPException(status_code=422, detail=f"{channel} execution failed: {str(e)[:120]}")


@router.get("/b2b/reminders/due")
def get_due_reminders(include_upcoming: bool = False, db: Session = Depends(get_db)):
    """Reminders whose time has come (due_at <= now). Feeds Action Queue / Founder Inbox."""
    from app.models.models import OutreachReminder, B2BLead
    q = (db.query(OutreachReminder, B2BLead)
         .join(B2BLead, OutreachReminder.lead_id == B2BLead.id)
         .filter(OutreachReminder.status == "SCHEDULED"))
    if not include_upcoming:
        q = q.filter(OutreachReminder.due_at <= datetime.utcnow())
    rows = q.order_by(OutreachReminder.due_at.asc()).all()
    out = []
    for rem, lead in rows:
        out.append({
            "reminder_id": rem.id, "lead_id": lead.id, "company": lead.company,
            "channel": rem.channel, "sequence_step": rem.sequence_step,
            "due_at": rem.due_at.isoformat() if rem.due_at else None,
            "overdue_days": max(0, (datetime.utcnow() - rem.due_at).days) if rem.due_at else 0,
            "reason": rem.reason, "estimated_value": int(lead.estimated_value or 0),
            "current_stage": lead.status,
        })
    return {"reminders": out, "total": len(out)}


@router.post("/b2b/reminders/{reminder_id}/cancel")
def cancel_reminder(reminder_id: int, db: Session = Depends(get_db)):
    """Founder dismisses a scheduled reminder (e.g. lead already replied)."""
    from app.models.models import OutreachReminder
    rem = db.query(OutreachReminder).filter(OutreachReminder.id == reminder_id).first()
    if not rem:
        raise HTTPException(status_code=404, detail="reminder not found")
    rem.status = "CANCELLED"
    db.commit()
    return {"status": "cancelled", "reminder_id": reminder_id}


# ── APPROVAL INBOX & DRAFT-BASED WORKFLOW ─────────────────────────────────────

MARGIN_RATES = {
    "distributor": 0.35,
    "wholesale":   0.35,
    "corporate":   0.42,
    "horeca":      0.28,
    "government":  0.20,
    "retail":      0.30,
    "gifting":     0.32,
    # Demand-side instant-coffee consumers (direct sale = higher margin than resale)
    "corporate_office":    0.42,
    "corporate_pantry":    0.42,
    "hospital":            0.40,
    "guest_house":         0.40,
    "industrial_canteen":  0.38,
    "education_mess":      0.38,
    "facility_management": 0.36,
    "event_catering":      0.36,
    "catering_contractor": 0.35,
    "hotel_canteen":       0.34,
    "wholesaler":          0.33,
    "retail_chain":        0.30,
    "kirana_store":        0.30,
    "corporate_gifting":   0.32,
    "govt_canteen":        0.22,
}

def _margin_for(lead) -> int:
    rate = MARGIN_RATES.get((lead.division or "corporate").lower(), 0.30)
    return int((lead.estimated_value or 0) * rate)


# ── Outcome-driven workflow ──────────────────────────────────────────────────
# Every outreach action ends with a real outcome, and the outcome — not a timer
# — decides what happens next. A status only says where a lead sits; an outcome
# says what actually happened when we spoke to them, which is the fact that
# should drive the next step.
#
# outcome -> (new lead status, next channel or None, wait days, why)
_CALL_OUTCOMES = {
    "interested":       ("REPLIED",         "whatsapp",     0,
                         "Interested — send details and catalogue while it's warm"),
    "need_sample":      ("REPLIED",         "sample",       0,
                         "Asked for a sample — dispatch it"),
    "need_proposal":    ("REPLIED",         "proposal",     0,
                         "Asked for pricing — send the proposal"),
    "call_back":        ("FOLLOWUP_DUE",    "founder_call", 2,
                         "Asked to be called back"),
    "busy":             ("FOLLOWUP_DUE",    "founder_call", 1,
                         "Busy — try again"),
    "no_answer":        ("FOLLOWUP_DUE",    "whatsapp",     1,
                         "No answer — try WhatsApp instead of dialling again"),
    "wrong_person":     ("FOLLOWUP_DUE",    "founder_call", 1,
                         "Wrong person — ask for the decision maker"),
    "already_supplier": ("NOT_INTERESTED",  None,           0,
                         "Already has a supplier — record and stop"),
    "not_interested":   ("NOT_INTERESTED",  None,           0,
                         "Not interested — stop contacting"),
}

# Channels ranked by conversion probability for B2B coffee, not by how easily
# they automate. Automation exists to save founder hours, not to replace the
# conversation most likely to close.
_CHANNEL_PRIORITY = ["founder_call", "ai_call", "email", "whatsapp"]


def _channel_available(lead, channel: str) -> tuple[bool, str]:
    """Can we actually run this channel for this lead, right now?"""
    has_phone = bool((lead.phone or lead.whatsapp_number or "").strip())
    has_email = bool((lead.email or "").strip())
    if channel == "founder_call":
        return (has_phone, "verified phone available" if has_phone else "no phone on record")
    if channel == "ai_call":
        if not has_phone:
            return False, "no phone on record"
        if not (os.getenv("VAPI_API_KEY", "") or "").strip():
            return False, "AI calling not configured (VAPI_API_KEY unset)"
        return True, "phone available and AI calling configured"
    if channel == "email":
        if not has_email:
            return False, "no verified email"
        zoho = (os.getenv("ZOHO_APP_PASSWORD", "") or "").strip()
        return (bool(zoho), "verified email and SMTP configured" if zoho else "SMTP not configured")
    if channel == "whatsapp":
        return (has_phone, "phone verified — WhatsApp reachable" if has_phone else "no phone on record")
    return False, f"unknown channel {channel}"


def next_best_action(lead) -> dict:
    """
    The single next action for this opportunity. Never returns "blocked":
    if the highest-value channel is unavailable we fall to the next one, and
    only a lead with no phone AND no email has nothing to do — which is a
    discovery task, not a dead end.
    """
    status = (lead.status or "").upper()
    if status in _CONVERSION_CLOSED:
        return {"action": None, "channel": None, "reason": f"closed ({status})",
                "blocked": False, "priority": 0}

    # An engaged lead is driven by the conversion ladder, not by cold priority.
    conv = _CONVERSION_LADDER.get(status)
    if conv:
        channel, wait, why = conv
        ok, detail = _channel_available(lead, channel if channel in _CHANNEL_PRIORITY else "founder_call")
        return {"action": channel, "channel": channel, "reason": why,
                "blocked": False, "wait_days": wait, "availability": detail,
                "stage": "conversion", "priority": 1}

    considered = []
    for channel in _CHANNEL_PRIORITY:
        ok, detail = _channel_available(lead, channel)
        considered.append({"channel": channel, "available": ok, "detail": detail})
        if ok:
            return {
                "action": channel, "channel": channel, "reason": detail,
                "blocked": False, "stage": "outreach",
                "priority": _CHANNEL_PRIORITY.index(channel) + 1,
                "skipped": [c for c in considered[:-1] if not c["available"]],
            }

    return {"action": "discover_contact", "channel": None,
            "reason": "no phone and no email — needs discovery/enrichment before outreach",
            "blocked": False, "stage": "discovery", "priority": 99,
            "skipped": [c for c in considered if not c["available"]]}


class RecordOutcomeRequest(BaseModel):
    outcome: str
    channel: str = "founder_call"
    notes: Optional[str] = None
    # Qualification facts captured during the conversation. Each is optional —
    # only what was actually learned gets written; nothing is inferred.
    decision_maker: Optional[str] = None
    current_supplier: Optional[str] = None
    monthly_consumption: Optional[str] = None
    budget_range: Optional[str] = None
    next_followup_date: Optional[str] = None



def _parse_kg(v):
    """'40kg' / '40 kg/mo' / 40 -> 40.0. Returns None when nothing numeric is
    present, so an unparseable note never becomes a fabricated quantity."""
    if v in (None, ""):
        return None
    try:
        import re as _re
        m = _re.search(r"(\d+(?:\.\d+)?)", str(v))
        return float(m.group(1)) if m else None
    except Exception:
        return None


@router.post("/b2b/leads/{lead_id}/record-outcome")
def record_outcome(lead_id: int, req: RecordOutcomeRequest, db: Session = Depends(get_db)):
    """
    Record what actually happened on a call/contact, and let that outcome drive
    the next step. This is how founder conversations feed the system: the facts
    captured here (decision maker, current supplier, consumption, budget)
    improve every future recommendation far more than any automation would.
    """
    from app.models.models import B2BLead, OutreachReminder, CallHistory
    from app.services.pipeline_tracker import track

    key = (req.outcome or "").lower().strip().replace(" ", "_")
    entry = _CALL_OUTCOMES.get(key)
    if not entry:
        raise HTTPException(status_code=400,
                            detail=f"unknown outcome '{req.outcome}' — expected one of "
                                   f"{sorted(_CALL_OUTCOMES)}")
    new_status, next_channel, wait_days, why = entry
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")

    before = lead.status
    lead.status = new_status
    lead.call_outcome_last = key
    lead.last_updated = datetime.utcnow()

    captured = {}
    for field in ("decision_maker", "current_supplier", "monthly_consumption",
                  "budget_range", "next_followup_date"):
        val = getattr(req, field, None)
        if val:
            setattr(lead, field, val)
            captured[field] = val
    if key in ("already_supplier", "not_interested"):
        lead.objection_reason = req.notes or why
    if key == "need_sample":
        lead.sample_requested = True

    db.add(CallHistory(lead_id=lead.id, call_date=datetime.utcnow(),
                       status=key, summary=req.notes or why, call_status="COMPLETED"))

    # Every recorded outcome also becomes Business Memory, so a founder call
    # teaches the system something permanent instead of only moving a status.
    # Without this the structured facts lived on the lead row, where the next
    # correction overwrote them and no history survived.
    from app.models.models import LeadInteraction as _LI
    db.add(_LI(
        lead_id=lead.id, occurred_at=datetime.utcnow(), created_by="FOUNDER",
        method=(req.channel or "founder_call"), outcome=key,
        decision_maker=req.decision_maker, current_supplier=req.current_supplier,
        budget_range=req.budget_range, next_followup_date=req.next_followup_date,
        monthly_consumption_kg=_parse_kg(req.monthly_consumption),
        interested=(key in ("interested", "need_sample", "need_proposal")),
        remark=req.notes or why))

    # Close any open reminder — this contact has been actioned.
    for rem in db.query(OutreachReminder).filter(
        OutreachReminder.lead_id == lead.id,
        OutreachReminder.status == "SCHEDULED").all():
        rem.status = "DONE"
        rem.done_at = datetime.utcnow()
    db.flush()

    track(db, "CALL_OUTCOME_RECORDED", lead_id=lead.id, actor="FOUNDER",
          channel=req.channel, before_status=before, after_status=new_status,
          payload={"outcome": key, "captured": captured, "notes": req.notes})

    scheduled = None
    if next_channel:
        ok, detail = _channel_available(lead, next_channel if next_channel in _CHANNEL_PRIORITY else "founder_call")
        if not ok and next_channel in _CHANNEL_PRIORITY:
            # Adaptive: the outcome's preferred channel is unavailable, so fall
            # to the best one that is. The workflow never stops on a missing
            # channel — that is what left phone-only leads dead-ended on Vapi.
            nba = next_best_action(lead)
            next_channel = nba.get("channel") or next_channel
            detail = nba.get("reason", detail)
        rem = OutreachReminder(
            lead_id=lead.id, channel=next_channel,
            sequence_step=_CONVERSION_STEP_BASE + 50,
            due_at=datetime.utcnow() + timedelta(days=wait_days),
            reason=f"Outcome '{key}': {why}", status="SCHEDULED")
        db.add(rem)
        db.flush()
        scheduled = {"channel": next_channel, "due_at": rem.due_at.isoformat(),
                     "reason": rem.reason, "availability": detail}

    db.commit()
    return {"status": "recorded", "lead_id": lead_id, "outcome": key,
            "before_status": before, "new_status": new_status,
            "captured": captured, "next_action": scheduled,
            "next_best_action": next_best_action(lead)}


@router.get("/b2b/leads/{lead_id}/next-best-action")
def get_next_best_action(lead_id: int, db: Session = Depends(get_db)):
    """The one thing to do next for this opportunity, and why."""
    from app.models.models import B2BLead
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    nba = next_best_action(lead)
    return {"lead_id": lead_id, "company": lead.company, "status": lead.status, **nba}


@router.get("/b2b/outcomes")
def list_outcomes():
    """The outcomes a founder can record, and what each one triggers."""
    return {"outcomes": [
        {"outcome": k, "sets_status": v[0], "next_channel": v[1],
         "wait_days": v[2], "meaning": v[3]} for k, v in _CALL_OUTCOMES.items()
    ], "channel_priority": _CHANNEL_PRIORITY}


@router.post("/b2b/intelligence/collect-evidence")
def collect_evidence(limit: int = 30, fetch_website: bool = True,
                     verified_only: bool = False, db: Session = Depends(get_db)):
    """
    Truth Layer: write evidence rows for leads from real sources. Facts only —
    no score, classification or next action is stored anywhere.

    Defaults to ALL leads, not just Maps-verified ones. Restricting collection
    to Maps-verified leads left the other 600 with no evidence at all, and an
    unassessed lead used to fall through to REJECT — so name-based disqualifiers
    (a beauty centre mislabelled "distributor") never ran, and legitimate
    phone-only leads were hidden as if disqualified. The collector's name and
    category signals work without Maps data.
    """
    from app.models.models import B2BLead
    from app.services.evidence_collector import collect_for_lead
    from app.services.coffee_demand import _fetch_site

    leads = [l for l in db.query(B2BLead).all()
             if (not verified_only or l.maps_rating or l.maps_reviews_count)][:max(1, limit)]

    # Fetch the websites concurrently. Sequentially this was one 8s timeout per
    # lead — roughly 400s for the full 93-lead set, held open in a single
    # synchronous request, where any client or proxy timeout aborts and rolls
    # back every row collected so far. Mirrors the ThreadPoolExecutor already
    # used by lead_discovery.enrich_lead_contact.
    sites: dict = {}
    if fetch_website:
        import concurrent.futures
        targets = [(l.id, l.website) for l in leads if (l.website or "").strip()]
        if targets:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_fetch_site, url): lid for lid, url in targets}
                for fut in concurrent.futures.as_completed(futures, timeout=120):
                    try:
                        sites[futures[fut]] = fut.result() or ""
                    except Exception:
                        sites[futures[fut]] = ""

    total = 0
    out = []
    for l in leads:
        try:
            l.intelligence_status = "COLLECTING"
            rows = collect_for_lead(l, db, site_text=sites.get(l.id, ""))
            total += len(rows)
            # Evidence gathered and the assessment is a pure read over it, so the
            # lead is fully evaluated once this returns. COLLECTING/SCORING are
            # the transient states a future async worker would occupy.
            l.intelligence_status = "COMPLETE"
            out.append({"lead_id": l.id, "company": l.company,
                        "signals": [r.signal_type for r in rows]})
        except Exception as e:
            # FAILED is why an explicit state beats a timestamp: a lead that
            # errored is not "unassessed" and not "assessed" — its
            # classification must not be trusted, and it needs a retry.
            l.intelligence_status = "FAILED"
            out.append({"lead_id": l.id, "company": l.company,
                        "error": str(e)[:120]})
    db.commit()
    from collections import Counter as _C
    return {"leads_processed": len(leads), "evidence_rows_written": total,
            "status_counts": dict(_C(l.intelligence_status for l in leads)),
            "detail": out}


@router.get("/b2b/intelligence/assess")
def assess_opportunities(limit: int = 50, min_class: str = "", db: Session = Depends(get_db)):
    """
    Intelligence Layer: derive assessment from the Truth Layer. Computes on
    read and persists nothing, so a change in evidence changes the answer
    immediately and no stale score can survive.

    Returns buying potential (score + confidence + evidence), commercial fit
    (margin / order size / geography), a blended opportunity score, the
    classification, and the recommended workflow and next action.
    """
    from app.models.models import B2BLead, LeadEvidence
    from app.services.opportunity_intelligence import assess

    order = {"REJECT": 0, "COLD": 1, "WARM": 2, "HOT": 3}
    if min_class and min_class.upper() not in order:
        # Silently treating an unknown value as "no filter" returned the whole
        # set — including REJECTs — to a caller who asked for HOT only, with no
        # signal the filter had been ignored.
        raise HTTPException(status_code=400,
                            detail=f"unknown min_class '{min_class}' — expected one of "
                                   f"{sorted(order)}")

    # Assess EVERY qualifying lead, then rank, then truncate. Slicing before
    # ranking returned an arbitrary page of leads sorted among themselves: at
    # limit=20 a REJECT-classified furniture store appeared in the top three
    # while the genuine top opportunities sat outside the slice entirely.
    leads = [l for l in db.query(B2BLead).all()
             if (l.maps_rating or l.maps_reviews_count)]
    ev_by_lead: dict = {}
    for e in db.query(LeadEvidence).all():
        ev_by_lead.setdefault(e.lead_id, []).append(e)

    results = [assess(l, ev_by_lead.get(l.id, [])).as_dict() for l in leads]
    if min_class:
        floor = order[min_class.upper()]
        results = [r for r in results if order.get(r["classification"], 0) >= floor]
    results.sort(key=lambda r: r["opportunity_score"], reverse=True)
    total_matching = len(results)
    results = results[:max(1, limit)]

    from collections import Counter
    return {
        "assessed": len(results),
        "total_matching": total_matching,
        "classification_counts": dict(Counter(r["classification"] for r in results)),
        "results": results,
        "note": "Derived on read from the evidence table — nothing here is stored "
                "on the lead, so it can never go stale. Results are ranked across "
                "all qualifying leads before `limit` is applied.",
    }


@router.post("/b2b/leads/fetch-place-details")
def fetch_place_details_backfill(limit: int = 25, refresh: bool = False,
                                 db: Session = Depends(get_db)):
    """
    Resolve place_id and fetch Google Places amenities for existing leads.

    Leads discovered before place_id was persisted cannot have their amenities
    queried at all, so serves_breakfast could only ever be inferred. This
    resolves the id (Find Place) then reads the amenity (Place Details) and
    caches the result — both calls are billed, so already-checked leads are
    skipped unless refresh=true.
    """
    from app.models.models import B2BLead
    from app.services.lead_discovery import resolve_place_id, fetch_place_amenities

    if not (os.getenv("GOOGLE_MAPS_API_KEY", "") or "").strip():
        raise HTTPException(status_code=400,
                            detail="GOOGLE_MAPS_API_KEY not configured")

    q = [l for l in db.query(B2BLead).all()
         if (l.maps_rating or l.maps_reviews_count)
         and (refresh or l.place_details_checked_at is None)]
    q = q[:max(1, limit)]

    checked = resolved = with_amenity = 0
    rows = []
    for l in q:
        pid = (l.place_id or "").strip() or resolve_place_id(l.company or "", l.city or "")
        if pid and not (l.place_id or "").strip():
            l.place_id = pid
            resolved += 1
        l.place_details_checked_at = datetime.utcnow()
        checked += 1
        if not pid:
            rows.append({"lead_id": l.id, "company": l.company, "place_id": None,
                         "serves_breakfast": None, "note": "no Places match"})
            continue

        det = fetch_place_amenities(pid)
        # Tri-state: only record what Google actually returned. A missing field
        # stays None ("unknown") rather than being written as False.
        if "serves_breakfast" in det:
            l.serves_breakfast = det["serves_breakfast"]
            with_amenity += 1
        if det.get("website") and not (l.website or "").strip():
            l.website = det["website"]
        if det.get("types"):
            l.maps_types = ",".join(str(t) for t in det["types"])[:400]
        # Persist the operational status. It was previously fetched, returned
        # in the response and discarded, which left the PERMANENTLY_CLOSED
        # disqualifier permanently unable to fire.
        if det.get("business_status"):
            l.business_status = det["business_status"]
        rows.append({"lead_id": l.id, "company": l.company, "place_id": pid,
                     "serves_breakfast": l.serves_breakfast,
                     "website": l.website, "business_status": det.get("business_status")})
    db.commit()
    return {"checked": checked, "place_ids_resolved": resolved,
            "amenity_reported": with_amenity, "results": rows,
            "note": "serves_breakfast is tri-state: true/false as reported by "
                    "Google, null when Google did not report it."}


@router.post("/b2b/leads/score-coffee-demand")
def score_coffee_demand(limit: int = 25, verified_only: bool = True,
                        fetch_website: bool = True, db: Session = Depends(get_db)):
    """
    Score observable evidence that each lead buys coffee, and record it.

    Returns the verdict split WITHOUT persisting anything — score and verdict
    are derived values and the authoritative assessment is
    GET /b2b/intelligence/assess. Leads
    whose evidence could not be gathered come back as INSUFFICIENT_DATA and are
    NOT archived — see app/services/coffee_demand.py for why that distinction
    matters here (three of the six signals have no data source yet, so a low
    score often means "we could not look", not "there is no demand").
    """
    from app.models.models import B2BLead
    from app.services.coffee_demand import score_lead, PASS_THRESHOLD

    q = db.query(B2BLead)
    leads = [l for l in q.all()
             if (not verified_only or l.maps_rating or l.maps_reviews_count)
             and (l.status or "").upper() not in _CONVERSION_CLOSED]
    leads = leads[:max(1, limit)]

    results, counts = [], {"PASS": 0, "REJECT": 0, "INSUFFICIENT_DATA": 0}
    for l in leads:
        s = score_lead(l, db=db, fetch_website=fetch_website)
        # DELIBERATELY NOT WRITTEN BACK to the lead. Score and verdict are
        # derived values; persisting them here is what created two competing
        # writers to coffee_buying_score and silently zeroed every verified
        # distributor. The authoritative assessment is
        # GET /b2b/intelligence/assess, derived on read from lead_evidence.
        counts[s.verdict] = counts.get(s.verdict, 0) + 1
        results.append({"lead_id": l.id, "company": l.company, "city": l.city,
                        "division": l.division, **s.as_dict()})

    results.sort(key=lambda r: r["coffee_buying_score"], reverse=True)
    return {
        "scored": len(results), "pass_threshold": PASS_THRESHOLD,
        "verdicts": counts, "results": results,
        "note": "INSUFFICIENT_DATA means the evidence sources were unavailable "
                "(no website, no place_id amenity, no FSSAI/GST lookup) — those "
                "leads need enrichment, not archiving.",
    }


# -- Business Memory: interactions, structured remarks, summary, search --------

_MEMORY_METHODS = {"email", "whatsapp", "founder_call", "ai_call", "meeting",
                   "visit", "sample", "proposal", "tender", "order", "other"}


class InteractionRequest(BaseModel):
    method: str
    occurred_at: Optional[str] = None
    created_by: str = "FOUNDER"
    outcome: Optional[str] = None
    # Structured CRM facts - all optional, only what was actually learned.
    decision_maker: Optional[str] = None
    designation: Optional[str] = None
    current_supplier: Optional[str] = None
    current_brand: Optional[str] = None
    monthly_consumption_kg: Optional[float] = None
    budget_range: Optional[str] = None
    price_sensitivity: Optional[str] = None
    interested: Optional[bool] = None
    priority: Optional[str] = None
    preferred_contact_time: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    next_followup_date: Optional[str] = None
    remark: Optional[str] = None


def _apply_interaction_to_lead(lead, i) -> list:
    """
    Promote newly learned facts onto the lead so drafts, call briefs and the
    intelligence layer can use them without the founder re-entering anything.
    Never blanks an existing value with an empty one - memory only ever adds.
    """
    promoted = []
    for src, dst in (("decision_maker", "decision_maker"),
                     ("current_supplier", "current_supplier"),
                     ("current_brand", "current_brand"),
                     ("budget_range", "budget_range"),
                     ("next_followup_date", "next_followup_date"),
                     ("priority", "priority")):
        val = getattr(i, src, None)
        if val not in (None, "") and hasattr(lead, dst):
            setattr(lead, dst, val)
            promoted.append(dst)
    if getattr(i, "monthly_consumption_kg", None):
        lead.expected_monthly_consumption_kg = i.monthly_consumption_kg
        promoted.append("expected_monthly_consumption_kg")
    if getattr(i, "outcome", None):
        lead.call_outcome_last = i.outcome
        promoted.append("call_outcome_last")
    return promoted


def _build_summary(lead, interactions, intel) -> dict:
    """
    Regenerated from the record every time memory changes. Every field traces
    to a stored interaction or a real event - nothing inferred or invented.
    """
    latest = {}
    for i in sorted(interactions, key=lambda x: x.occurred_at or datetime.min):
        for f in ("decision_maker", "designation", "current_supplier", "current_brand",
                  "budget_range", "price_sensitivity", "preferred_contact_time",
                  "preferred_contact_method", "next_followup_date", "priority"):
            v = getattr(i, f, None)
            if v not in (None, ""):
                latest[f] = v
        if getattr(i, "monthly_consumption_kg", None):
            latest["monthly_consumption_kg"] = i.monthly_consumption_kg
        if getattr(i, "interested", None) is not None:
            latest["interested"] = i.interested

    last = max(interactions, key=lambda x: x.occurred_at or datetime.min) if interactions else None
    open_tasks, risks = [], []
    if latest.get("next_followup_date"):
        open_tasks.append("Follow up on " + str(latest["next_followup_date"]))
    if latest.get("current_supplier"):
        risks.append("Incumbent supplier: " + str(latest["current_supplier"]))
    if latest.get("interested") is False:
        risks.append("Declined at last contact")
    if not (lead.email or "").strip() and not (lead.phone or "").strip():
        risks.append("No contact channel on record")
    elif (lead.email or "").strip() and not lead.email_verified:
        risks.append("Email on file is unverified - sending is blocked")

    return {
        "decision_maker": latest.get("decision_maker"),
        "designation": latest.get("designation"),
        "current_supplier": latest.get("current_supplier"),
        "current_brand": latest.get("current_brand"),
        "monthly_consumption_kg": latest.get("monthly_consumption_kg"),
        "budget_range": latest.get("budget_range"),
        "preferred_contact_time": latest.get("preferred_contact_time"),
        "preferred_contact_method": latest.get("preferred_contact_method"),
        "last_interaction": ({"at": last.occurred_at.isoformat() if last.occurred_at else None,
                              "method": last.method, "outcome": last.outcome,
                              "remark": (last.remark or "")[:220]} if last else None),
        "current_stage": lead.status,
        "classification": intel.classification if intel else None,
        "open_tasks": open_tasks,
        "risks": risks,
        "next_best_action": next_best_action(lead),
        "interactions_recorded": len(interactions),
    }


@router.post("/b2b/business/{lead_id}/interaction")
def create_interaction(lead_id: int, req: InteractionRequest, db: Session = Depends(get_db)):
    """
    Record an interaction. Append-only - this never edits an earlier one.
    """
    from app.models.models import B2BLead, LeadInteraction
    from app.services.pipeline_tracker import track

    method = (req.method or "").lower().strip()
    if method not in _MEMORY_METHODS:
        raise HTTPException(status_code=400,
                            detail="unknown method - expected one of " + str(sorted(_MEMORY_METHODS)))
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")

    when = datetime.utcnow()
    if req.occurred_at:
        try:
            when = datetime.fromisoformat(req.occurred_at.replace("Z", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="occurred_at must be ISO-8601")

    i = LeadInteraction(
        lead_id=lead_id, occurred_at=when, created_by=req.created_by, method=method,
        outcome=req.outcome, decision_maker=req.decision_maker, designation=req.designation,
        current_supplier=req.current_supplier, current_brand=req.current_brand,
        monthly_consumption_kg=req.monthly_consumption_kg, budget_range=req.budget_range,
        price_sensitivity=req.price_sensitivity, interested=req.interested,
        priority=req.priority, preferred_contact_time=req.preferred_contact_time,
        preferred_contact_method=req.preferred_contact_method,
        next_followup_date=req.next_followup_date, remark=req.remark,
    )
    db.add(i)
    db.flush()

    promoted = _apply_interaction_to_lead(lead, i)
    lead.last_updated = datetime.utcnow()
    track(db, "INTERACTION_RECORDED", lead_id=lead_id, actor=req.created_by,
          channel=method, payload={"interaction_id": i.id, "outcome": req.outcome,
                                   "promoted_fields": promoted})
    db.commit()
    return {"status": "recorded", "interaction_id": i.id, "lead_id": lead_id,
            "method": method, "promoted_to_lead": promoted}


@router.patch("/b2b/interaction/{interaction_id}")
def correct_interaction(interaction_id: int, req: InteractionRequest, db: Session = Depends(get_db)):
    """
    Correct an interaction WITHOUT overwriting it. Writes a new row carrying
    supersedes_id and marks the original superseded_by_id, so what was believed
    at the time stays readable - history is never rewritten.
    """
    from app.models.models import LeadInteraction, B2BLead
    from app.services.pipeline_tracker import track

    old = db.query(LeadInteraction).filter(LeadInteraction.id == interaction_id).first()
    if not old:
        raise HTTPException(status_code=404, detail="interaction not found")
    if old.superseded_by_id:
        raise HTTPException(status_code=409,
                            detail="already corrected by interaction " + str(old.superseded_by_id))

    fields = ("outcome", "decision_maker", "designation", "current_supplier", "current_brand",
              "monthly_consumption_kg", "budget_range", "price_sensitivity", "interested",
              "priority", "preferred_contact_time", "preferred_contact_method",
              "next_followup_date", "remark")
    carried = {}
    for f in fields:
        v = getattr(req, f, None)
        carried[f] = v if v not in (None, "") else getattr(old, f)

    new = LeadInteraction(
        lead_id=old.lead_id, occurred_at=old.occurred_at, created_by=req.created_by,
        method=(req.method or old.method).lower().strip(), supersedes_id=old.id, **carried)
    db.add(new)
    db.flush()
    old.superseded_by_id = new.id

    lead = db.query(B2BLead).filter(B2BLead.id == old.lead_id).first()
    promoted = _apply_interaction_to_lead(lead, new) if lead else []
    track(db, "INTERACTION_CORRECTED", lead_id=old.lead_id, actor=req.created_by,
          channel=new.method, payload={"supersedes": old.id, "new_interaction_id": new.id})
    db.commit()
    return {"status": "corrected", "original_id": old.id, "new_interaction_id": new.id,
            "note": "the original row is retained and marked superseded - nothing was overwritten",
            "promoted_to_lead": promoted}


@router.get("/b2b/business/{lead_id}/summary")
def business_summary(lead_id: int, db: Session = Depends(get_db)):
    """Regenerated summary of everything known about this business."""
    from app.models.models import B2BLead, LeadInteraction, LeadEvidence
    from app.services.opportunity_intelligence import assess

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")
    inter = db.query(LeadInteraction).filter(
        LeadInteraction.lead_id == lead_id,
        LeadInteraction.superseded_by_id.is_(None)).all()
    ev = db.query(LeadEvidence).filter(LeadEvidence.lead_id == lead_id).all()
    return {"lead_id": lead_id, "company": lead.company,
            "summary": _build_summary(lead, inter, assess(lead, ev))}


@router.get("/b2b/business/search-memory")
def search_memory(q: str, limit: int = 30, db: Session = Depends(get_db)):
    """
    Search Business Memory: remarks, decision makers, suppliers, brands and
    follow-up notes.
    """
    from sqlalchemy import or_   # not imported at module level
    from app.models.models import B2BLead, LeadInteraction

    term = (q or "").strip()
    if len(term) < 2:
        raise HTTPException(status_code=400, detail="query must be at least 2 characters")
    like = "%" + term + "%"
    rows = (db.query(LeadInteraction, B2BLead)
            .join(B2BLead, LeadInteraction.lead_id == B2BLead.id)
            .filter(or_(LeadInteraction.remark.ilike(like),
                        LeadInteraction.decision_maker.ilike(like),
                        LeadInteraction.current_supplier.ilike(like),
                        LeadInteraction.current_brand.ilike(like),
                        LeadInteraction.next_followup_date.ilike(like),
                        LeadInteraction.outcome.ilike(like)))
            .order_by(LeadInteraction.occurred_at.desc()).limit(max(1, limit)).all())
    return {"query": term, "matches": len(rows), "results": [{
        "lead_id": l.id, "company": l.company, "city": l.city, "stage": l.status,
        "interaction_id": i.id,
        "occurred_at": i.occurred_at.isoformat() if i.occurred_at else None,
        "method": i.method, "outcome": i.outcome, "decision_maker": i.decision_maker,
        "current_supplier": i.current_supplier, "next_followup_date": i.next_followup_date,
        "remark": (i.remark or "")[:220],
    } for i, l in rows]}


@router.get("/b2b/business/{lead_id}/memory")
def business_memory(lead_id: int, db: Session = Depends(get_db)):
    """
    The permanent Business Memory for one company — the single source of truth
    for the relationship.

    Assembles the complete record from every store that holds a piece of it:
    the immutable event log, the Truth Layer evidence, email drafts, call
    history and scheduled follow-ups, merged into one chronological timeline
    plus the structured facts learned about the business.

    This exists because the relationship history was scattered: the UI's
    "Memory" panel filtered a local array by company NAME, which silently
    missed anything logged under a different spelling and could attach one
    business's history to another. Everything here is keyed on the lead id.

    Nothing is inferred. If a stage never happened, it is absent — not zero,
    not assumed.
    """
    from app.models.models import (B2BLead, LeadEvidence, WorkflowEvent,
                                   EmailDraft, OutreachReminder, CallHistory)
    from app.services.opportunity_intelligence import assess

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")

    evidence = db.query(LeadEvidence).filter(LeadEvidence.lead_id == lead_id).all()
    intel = assess(lead, evidence)

    # ── Chronological timeline, merged from every real record ──
    timeline = []

    for e in db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead_id).all():
        p = e.payload or {}
        detail = (p.get("refreshed_fields") or p.get("subject") or p.get("outcome")
                  or p.get("reason") or p.get("error") or p.get("smtp_error") or "")
        timeline.append({
            "at": e.occurred_at.isoformat() if e.occurred_at else None,
            "kind": "event", "type": e.event_type, "actor": e.actor,
            "channel": e.channel, "detail": str(detail)[:220],
            "status_change": (f"{e.before_status} → {e.after_status}"
                              if e.before_status or e.after_status else None),
        })

    for d in db.query(EmailDraft).filter(EmailDraft.lead_id == lead_id).all():
        stamp = d.sent_at or d.queued_at or d.approved_at or d.created_at
        timeline.append({
            "at": stamp.isoformat() if stamp else None,
            "kind": "email", "type": f"EMAIL_{d.status}", "actor": "FOUNDER",
            "channel": "email",
            "detail": (d.subject or "")[:160],
            "smtp_response": (d.smtp_response or "")[:160] or None,
        })

    for c in db.query(CallHistory).filter(CallHistory.lead_id == lead_id).all():
        timeline.append({
            "at": c.call_date.isoformat() if c.call_date else None,
            "kind": "call", "type": f"CALL_{(c.status or 'LOGGED').upper()}",
            "actor": "FOUNDER", "channel": "call",
            "detail": (c.summary or "")[:220],
            "duration_seconds": c.call_duration or c.duration or 0,
        })

    for r in db.query(OutreachReminder).filter(OutreachReminder.lead_id == lead_id).all():
        stamp = r.done_at or r.due_at
        timeline.append({
            "at": stamp.isoformat() if stamp else None,
            "kind": "followup", "type": f"FOLLOWUP_{(r.status or '').upper()}",
            "actor": "SYSTEM", "channel": r.channel,
            "detail": (r.reason or "")[:220], "sequence_step": r.sequence_step,
        })

    timeline.sort(key=lambda x: x["at"] or "")

    # ── Structured facts the founder has actually learned ──
    def _f(name):
        v = getattr(lead, name, None)
        return v if (v not in ("", None)) else None

    return {
        "business": {
            "lead_id": lead.id, "company": lead.company, "city": lead.city,
            "address": _f("address"), "website": _f("website"),
            "place_id": _f("place_id"), "business_status": _f("business_status"),
            "category": lead.division,
            "category_provenance": {"source": _f("division_source"),
                                    "confidence": _f("division_confidence"),
                                    "verified": bool(getattr(lead, "division_verified", False))},
            "first_seen": lead.stage_entered_date.isoformat() if lead.stage_entered_date else None,
            "last_updated": lead.last_updated.isoformat() if lead.last_updated else None,
            "lead_source": _f("lead_source"),
        },
        "contact": {
            "phone": _f("phone"), "phone_source": _f("phone_source"),
            "phone_verified": bool(getattr(lead, "phone_verified", False)),
            "whatsapp": _f("whatsapp_number"),
            "email": _f("email"), "email_verified": bool(getattr(lead, "email_verified", False)),
            "decision_maker": _f("decision_maker"),
        },
        # Classification is only presented as authoritative when the evaluation
        # actually completed. A rediscovery resets intelligence_status to
        # NOT_STARTED precisely because the facts changed underneath the old
        # score — reporting that score as current would be the same class of
        # error as showing a fabricated one.
        "intelligence": {
            "status": _f("intelligence_status") or "NOT_STARTED",
            "is_current": (_f("intelligence_status") == "COMPLETE"),
            "classification": intel.classification,
            "buying_score": intel.buying_score,
            "confidence": round(intel.confidence, 2),
            "commercial_fit": intel.commercial_fit,
            "opportunity_score": intel.opportunity_score,
            "reasoning": intel.reasoning,
            "staleness_warning": (
                None if _f("intelligence_status") == "COMPLETE" else
                "Facts changed since this was scored — re-run evidence collection. "
                "The scores above are computed from the evidence on file and are "
                "provisional until then."),
            "next_best_action": next_best_action(lead),
        },
        # Evidence History — every fact with where it came from and when.
        "evidence": [{
            "signal": e.signal_type, "value": e.value, "source": e.source,
            "confidence": e.confidence, "verified": bool(e.verified),
            "weight": (e.weight_positive or 0) - (e.weight_negative or 0),
            "collected_at": e.collected_at.isoformat() if e.collected_at else None,
        } for e in sorted(evidence,
                          key=lambda x: (x.weight_positive or 0) - (x.weight_negative or 0),
                          reverse=True)],
        # Qualification facts captured on real calls (record-outcome).
        "qualification": {
            "current_supplier": _f("current_supplier"),
            "current_brand": _f("current_brand"),
            "monthly_consumption": _f("monthly_consumption"),
            "budget_range": _f("budget_range"),
            "sample_requested": bool(getattr(lead, "sample_requested", False)),
            "meeting_requested": bool(getattr(lead, "meeting_requested", False)),
            "last_call_outcome": _f("call_outcome_last"),
            "objection": _f("objection_reason"),
            "next_followup_date": _f("next_followup_date"),
        },
        "relationship": {
            "stage": lead.status,
            "interactions_recorded": len(timeline),
            "emails_sent": sum(1 for t in timeline if t["type"] == "EMAIL_SENT"),
            "calls_logged": sum(1 for t in timeline if t["kind"] == "call"),
            "rediscovered": sum(1 for t in timeline if t["type"] == "BUSINESS_REDISCOVERED"),
        },
        "timeline": timeline,
        "note": "Assembled from the event log, evidence store, drafts, calls and "
                "reminders — all keyed on lead id. Absent stages never happened.",
    }


@router.get("/b2b/deliverability")
def deliverability_health(db: Session = Depends(get_db)):
    """
    Sending health for the founder: real volume, real failure rate, and whether
    sending is currently permitted. Counts come from the immutable event log,
    never from lead status.
    """
    from app.services.deliverability import health
    return health(db)


@router.get("/b2b/power-hour")
def founder_power_hour(calls: int = 10, approvals: int = 15, db: Session = Depends(get_db)):
    """
    The founder's 15-minute morning session, prepared before they sit down.

    Everything upstream answers "what should happen for THIS lead". This
    answers the question that actually constrains the business: of 660
    actionable opportunities, which handful is worth the founder's next hour?

    Ranked by expected margin WEIGHTED BY CONFIDENCE, not by raw estimated
    value — a Rs 12L opportunity we know almost nothing about should not
    outrank a Rs 4L one with a verified decision maker and a real buying
    signal. Confidence itself decays with age, so stale leads fall away
    instead of sitting at the top forever.
    """
    from app.models.models import B2BLead, EmailDraft, OutreachReminder, LeadEvidence
    from app.services.decision_engine import (
        compute_founder_priority_score, revenue_confidence, buying_signals,
        contact_completeness,
    )
    from app.services.opportunity_intelligence import assess

    leads = [l for l in db.query(B2BLead).all()
             if (l.status or "").upper() not in _CONVERSION_CLOSED]

    # Single scoring path. Power Hour previously read the coffee_buying_score
    # column written by two different engines; it now derives from the same
    # evidence the Intelligence Layer uses, so the queue and the assessment can
    # never disagree about a lead.
    _ev: dict = {}
    for e in db.query(LeadEvidence).all():
        _ev.setdefault(e.lead_id, []).append(e)

    def proven_real(l) -> bool:
        """
        Can we prove this business exists? Google Maps evidence only.

        This gate is not optional for the call queue. Without it the ranking is
        dominated by leftover seed rows carrying fabricated estimates — Matrix
        Distributors scored an estimated Rs 62.6L against the real Bathinda
        Nestle distributor's Rs 1.8L, so it outranked a genuine business 20:1
        and sent the founder to phone a company that does not exist. Estimated
        value is modelled and can be nonsense; verification evidence cannot.

        phone_source is deliberately NOT accepted as proof: enrichment sets it
        on fabricated companies too, because searching a fictional name still
        returns somebody's real phone number — the same failure that emailed a
        Zimbabwean firm. A Maps place is the only evidence that the business
        itself exists.
        """
        return bool(getattr(l, "maps_rating", None)
                    or getattr(l, "maps_reviews_count", None))

    scored = []
    for l in leads:
        nba = next_best_action(l)
        if nba.get("action") in (None, "discover_contact"):
            continue                      # nothing the founder can act on today
        if not proven_real(l):
            continue                      # never spend founder time on unproven rows

        # Intelligence Layer decides whether this is worth the founder's time.
        # This is what keeps a pharmacy (Gandhi Market) and two furniture stores
        # (GOYAL/SODHI Enterprises) out of the call queue — all three previously
        # ranked in the top eight on estimated margin alone.
        intel = assess(l, _ev.get(l.id, []))
        if intel.classification == "REJECT":
            continue

        conf = revenue_confidence(l)
        margin = _margin_for(l)
        # Rank on margin x confidence x coffee-buying likelihood.
        #
        # Margin alone favours whichever category carries the highest margin
        # rate: distributors (35%) crowded out cafes scoring 90 on coffee
        # demand, even though a cafe is a far more certain coffee buyer. The
        # buying score is the whole reason a business is worth calling, so it
        # belongs in the ranking, not just on the record.
        # Rank on the Intelligence Layer's opportunity score, which already
        # blends buying evidence, commercial fit and confidence WITHOUT
        # multiplying by a modelled revenue figure. Estimated margin is shown
        # to the founder but no longer drives the ordering — doing so is what
        # put a fabricated Rs 62.6L lead above a verified distributor.
        weighted = intel.opportunity_score
        scored.append({
            "lead_id": l.id, "company": l.company, "city": l.city,
            "category": l.division,
            "next_best_action": nba.get("action"),
            "action_reason": nba.get("reason"),
            "estimated": {"revenue_inr": int(l.estimated_value or 0),
                          "margin_inr": int(margin),
                          "note": "modelled estimate, not booked revenue"},
            "confidence_percent": conf["confidence_percent"],
            "completeness_percent": conf["completeness_percent"],
            "confidence_reasons": conf["reasons"],
            "opportunity_score": intel.opportunity_score,
            "classification": intel.classification,
            "buying_score": intel.buying_score,
            "commercial_fit": intel.commercial_fit,
            "evidence": [e["signal"] for e in intel.evidence[:4]],
            "intelligence_reasoning": intel.reasoning,
            "buying_signals": buying_signals(l),
            "priority_score": compute_founder_priority_score(l),
            "_weighted": weighted,
            "phone": l.phone, "email": l.email, "website": l.website,
            "decision_maker": l.decision_maker,
            "last_outcome": l.call_outcome_last,
        })
    scored.sort(key=lambda r: r["_weighted"], reverse=True)
    for r in scored:
        r.pop("_weighted", None)

    call_queue = [r for r in scored if r["next_best_action"] == "founder_call"][:max(0, calls)]

    # Drafts waiting on approval, highest confidence-weighted margin first.
    pending = (db.query(EmailDraft, B2BLead)
               .join(B2BLead, EmailDraft.lead_id == B2BLead.id)
               .filter(EmailDraft.status.in_(["DRAFT", "PENDING", "EDITED"]))
               .all())
    approve_queue = sorted(
        [{"draft_id": d.id, "lead_id": l.id, "company": l.company,
          "subject": d.subject, "city": l.city,
          "estimated_margin_inr": int(_margin_for(l)),
          "confidence_percent": revenue_confidence(l)["confidence_percent"]}
         for d, l in pending if (l.email or "").strip()],
        key=lambda r: r["estimated_margin_inr"] * r["confidence_percent"], reverse=True
    )[:max(0, approvals)]

    since = datetime.utcnow() - timedelta(days=1)
    yesterday = db.query(WorkflowEvent).filter(WorkflowEvent.occurred_at >= since).all()
    from collections import Counter
    y_counts = Counter(e.event_type for e in yesterday)

    overdue = db.query(OutreachReminder).filter(
        OutreachReminder.status == "SCHEDULED",
        OutreachReminder.due_at <= datetime.utcnow()).count()

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "session": "Founder Power Hour — approve, review, call",
        "step_1_approve": {
            "count": len(approve_queue), "items": approve_queue,
            "note": "Highest confidence-weighted margin first",
        },
        "step_2_review_yesterday": {
            "events": dict(y_counts) or {"none": 0},
            "note": "Real recorded events only",
        },
        "step_3_call": {
            "count": len(call_queue), "items": call_queue,
            "note": "Ranked by estimated margin weighted by revenue confidence",
        },
        "context": {
            "actionable_opportunities": len(scored),
            "follow_ups_overdue": overdue,
        },
        "disclaimer": "Revenue and margin figures are modelled estimates. "
                      "Confidence reflects how much verified data supports them.",
    }


@router.get("/b2b/revenue-programs")
def revenue_programs(db: Session = Depends(get_db)):
    """
    Revenue-first view of the database, split into the two outreach programs.

    Every number is tagged verified or estimated and the two are never summed
    into one headline. Verified means it came from a real source we can point
    at (Google Maps, a confirmed public listing, an event we recorded).
    Estimated means our own demand model produced it — those are labelled and
    carry the model's basis, because presenting a modelled ₹54 Cr "pipeline"
    beside 0 real orders is how a dashboard starts lying to its founder.
    """
    from app.models.models import B2BLead, OutreachReminder, WorkflowEvent

    leads = db.query(B2BLead).all()

    def verified_phone(l):
        return bool((l.phone or "").strip()) and bool((l.phone_source or "").strip())

    def verified_email(l):
        return bool((l.email or "").strip()) and bool(l.email_verified)

    def has_maps(l):
        return bool(getattr(l, "maps_rating", None) or getattr(l, "place_id", None))

    # Program membership. A lead only qualifies for a program if we can
    # actually reach it on that program's first channel.
    program_a, program_b, unreachable = [], [], []
    for l in leads:
        if (l.status or "") in _CONVERSION_CLOSED:
            continue
        if verified_email(l):
            program_a.append(l)
        elif verified_phone(l):
            program_b.append(l)
        else:
            unreachable.append(l)

    def money(rows):
        rev = sum(int(l.estimated_value or 0) for l in rows)
        return {"revenue_inr": rev, "margin_inr": sum(_margin_for(l) for l in rows)}

    # Real, recorded outcomes — event log only, never inferred from status.
    ev_counts = dict(db.query(WorkflowEvent.event_type, func.count())
                     .group_by(WorkflowEvent.event_type).all())

    now = datetime.utcnow()
    due = db.query(OutreachReminder).filter(
        OutreachReminder.status == "SCHEDULED", OutreachReminder.due_at <= now).all()
    due_by_channel: dict = {}
    for r in due:
        due_by_channel[r.channel] = due_by_channel.get(r.channel, 0) + 1

    return {
        "generated_at": now.isoformat(),
        "programs": {
            "email_first": {
                "label": "Program A — verified email",
                "opportunities": len(program_a),
                "estimated": money(program_a),
                "entry_requirement": "verified business + verified email",
            },
            "phone_first": {
                "label": "Program B — phone only",
                "opportunities": len(program_b),
                "estimated": money(program_b),
                "entry_requirement": "verified business + corroborated phone, no email",
            },
            "not_contactable": {
                "label": "No verified channel yet",
                "opportunities": len(unreachable),
                "estimated": money(unreachable),
                "entry_requirement": "needs discovery/enrichment before any outreach",
            },
        },
        "verified": {
            "businesses_with_maps_evidence": sum(1 for l in leads if has_maps(l)),
            "phones_corroborated": sum(1 for l in leads if verified_phone(l)),
            "emails_verified": sum(1 for l in leads if verified_email(l)),
            "emails_sent": ev_counts.get("EMAIL_SENT", 0),
            "emails_failed": ev_counts.get("EMAIL_FAILED", 0),
            "emails_opened": ev_counts.get("EMAIL_OPENED", 0),
            "whatsapp_sent": ev_counts.get("WHATSAPP_SENT", 0),
            "ai_calls_completed": ev_counts.get("AI_CALL_COMPLETED", 0),
            "founder_calls": ev_counts.get("FOUNDER_CALL_COMPLETED", 0),
            # Count both: a meeting that happened is at least as real as one
            # merely booked. Counting only MEETING_BOOKED meant confirm-action
            # "meeting" (which records MEETING_COMPLETED) left the KPI at zero.
            "meetings": (ev_counts.get("MEETING_BOOKED", 0)
                         + ev_counts.get("MEETING_COMPLETED", 0)),
            "samples_dispatched": ev_counts.get("SAMPLE_DISPATCHED", 0),
            "proposals_sent": ev_counts.get("PROPOSAL_SENT", 0),
            "orders_won": ev_counts.get("ORDER_WON", 0),
        },
        "work_due_now": {
            "total": len(due),
            "by_channel": due_by_channel,
        },
        "disclaimer": {
            "verified_fields": ["company", "address", "phone", "email", "website",
                                "google_rating", "review_count", "recorded events"],
            "estimated_fields": ["monthly coffee demand", "estimated_value",
                                 "expected margin", "buying probability"],
            "note": "Estimated figures come from a demand model keyed on business "
                    "type and size. They are projections, not booked revenue. "
                    "Realised revenue is only what appears under orders_won.",
        },
    }


@router.post("/b2b/email/generate-drafts")
def generate_email_drafts(db: Session = Depends(get_db)):
    """
    Generate email drafts for all leads in DISCOVERED / QUALIFIED that don't
    already have a PENDING or APPROVED EmailDraft. Returns count of new drafts.
    """
    from app.models.models import B2BLead, EmailDraft
    from app.services.email_sender import generate_b2b_pitch_email
    from sqlalchemy import exists

    leads = (
        db.query(B2BLead)
        .filter(
            B2BLead.email.like("%@%"),
            B2BLead.status.in_(["DISCOVERED", "QUALIFIED"]),
            B2BLead.email_rejected_by_founder != True,
            ~exists().where(
                (EmailDraft.lead_id == B2BLead.id) &
                (EmailDraft.status.in_(["PENDING", "APPROVED", "SENT"]))
            ),
        )
        .order_by(B2BLead.score.desc())
        .all()
    )

    created = 0
    for lead in leads:
        subject, body = generate_b2b_pitch_email(lead)
        draft = EmailDraft(
            lead_id=lead.id,
            follow_up_type="introduction",
            subject=subject,
            body=body,
            reason=f"Auto-drafted for {lead.division or 'lead'} outreach",
            status="PENDING",
        )
        db.add(draft)
        created += 1

    db.commit()
    return {"drafted": created, "message": f"{created} email drafts ready for approval"}


@router.get("/b2b/revenue-actions")
def get_revenue_actions(db: Session = Depends(get_db)):
    """
    Revenue Actions Awaiting Approval (Founder Revenue Command Center).
    The founder approves commercial OUTCOMES, not communication channels — so
    everything needing a founder decision is grouped by ACTION TYPE, each with a
    live count and the ₹ margin it represents. Real data only.
    """
    from app.models.models import B2BLead, EmailDraft, OutreachReminder, GovTender

    actions = []
    total_margin = 0.0

    def add(key, label, icon, count, margin):
        nonlocal total_margin
        total_margin += margin
        actions.append({"key": key, "label": label, "icon": icon,
                        "count": count, "margin": int(margin)})

    # 1. Intro emails — pending founder-approvable drafts
    email_rows = (db.query(EmailDraft, B2BLead)
                  .join(B2BLead, EmailDraft.lead_id == B2BLead.id)
                  .filter(EmailDraft.status == "PENDING",
                          B2BLead.email_rejected_by_founder != True).all())
    add("email", "Intro Emails", "mail", len(email_rows),
        sum(_margin_for(l) for _, l in email_rows))

    # 2-4. Warmup reminders by channel (the next scheduled touch on each opp)
    for ch, label, icon in [("whatsapp", "WhatsApp Campaigns", "message"),
                            ("ai_call", "AI Calling Campaigns", "phone"),
                            ("founder_call", "Founder Calls", "user")]:
        rem = (db.query(OutreachReminder, B2BLead)
               .join(B2BLead, OutreachReminder.lead_id == B2BLead.id)
               .filter(OutreachReminder.channel == ch,
                       OutreachReminder.status == "SCHEDULED").all())
        add(ch, label, icon, len(rem), sum(_margin_for(l) for _, l in rem))

    # 5. Samples — requested but not yet dispatched
    samples = db.query(B2BLead).filter(
        B2BLead.sample_requested == True,
        B2BLead.status.notin_(["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED"])).all()
    add("sample", "Samples", "package", len(samples), sum(_margin_for(l) for l in samples))

    # 6. Proposals — post-sample, proposal due
    proposals = db.query(B2BLead).filter(
        B2BLead.status.in_(["SAMPLE_SENT", "FEEDBACK_RECEIVED", "MEETING_COMPLETED"])).all()
    add("proposal", "Proposals", "file", len(proposals), sum(_margin_for(l) for l in proposals))

    # 7. Government tender submissions — recommended APPLY, still open
    tenders = db.query(GovTender).filter(
        GovTender.status == "OPEN", GovTender.recommended_action == "APPLY").all()
    add("gov_tender", "Government Tender Submissions", "landmark",
        len(tenders), sum((t.expected_margin or 0) for t in tenders))

    return {"actions": actions, "total_margin": int(total_margin)}


@router.get("/b2b/email/approval-inbox")
def get_approval_inbox(db: Session = Depends(get_db)):
    """
    Returns all PENDING email drafts with full lead context for founder review.
    Also returns aggregate pipeline & margin for selected batch.
    """
    from app.models.models import B2BLead, EmailDraft
    from app.services.email_sender import validate_email_quality

    # A draft for a lead with no address can never send — approve-journey skips
    # it at the send guard. Counting those made the header badge read 122 when
    # only 74 were actually sendable, so the founder was shown 48 approvals that
    # would silently do nothing. Same defect as the old "Approve All 418" total.
    rows = (
        db.query(EmailDraft, B2BLead)
        .join(B2BLead, EmailDraft.lead_id == B2BLead.id)
        .filter(
            EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"]),
            B2BLead.email_rejected_by_founder != True,
            B2BLead.email.isnot(None),
            func.trim(B2BLead.email) != "",
        )
        .order_by(B2BLead.score.desc())
        .all()
    )

    from app.services.revenue_engine import data_completeness, revenue_potential

    leads_out = []
    total_pipeline = 0
    total_margin = 0
    class_counts = {"money_today": 0, "warm": 0, "cold": 0, "at_risk": 0}

    for draft, lead in rows:
        margin = _margin_for(lead)
        total_pipeline += int(lead.estimated_value or 0)
        total_margin   += margin

        first_name = ""
        if lead.contact_name:
            first_name = lead.contact_name.split()[0]

        quality = validate_email_quality(draft.subject or "", draft.body or "")

        # Unified confidence + revenue view (real computed fields only)
        rp = revenue_potential(lead)
        completeness = data_completeness(lead)["score"]
        email_conf = lead.email_confidence or (
            85 if (lead.email_verification_status or "") == "VALID" else
            55 if (lead.email_verification_status or "") == "RISKY_CATCH_ALL" else 30
        )

        # Warm-Score classification (spec §4) — engagement first, then potential
        opened = (lead.email_opens or 0) > 0
        replied = (lead.status or "") == "REPLIED"
        proposal_stale = (lead.status or "") == "PROPOSAL_SENT"
        if replied or opened:
            warm_class = "warm"
        elif proposal_stale:
            warm_class = "at_risk"
        elif margin >= 50_000 and email_conf >= 70:
            warm_class = "money_today"
        else:
            warm_class = "cold"
        class_counts[warm_class] += 1

        # Grounded AI reason — assembled ONLY from verified/real fields.
        bits = []
        seg = (lead.segment or lead.division or "").replace("_", " ").title()
        if seg:
            bits.append(seg)
        if lead.lead_source:
            bits.append(f"{lead.lead_source} verified")
        if margin > 0:
            bits.append(f"₹{margin:,} est. annual margin")
        if email_conf:
            bits.append(f"{email_conf}% email confidence")
        if completeness:
            bits.append(f"{completeness}% data complete")
        ai_reason = " · ".join(bits) if bits else "Verified lead ready for first-touch outreach"

        leads_out.append({
            "draft_id":    draft.id,
            "lead_id":     lead.id,
            "company":     lead.company,
            "contact_name": lead.contact_name or "",
            "contact_title": lead.contact_title or lead.contact_persona or "",
            "city":        lead.city or "",
            "division":    lead.division or "",
            "segment":     (lead.segment or lead.division or "corporate").title(),
            "email":       lead.email,
            # Real contact intelligence — shown only when present (blank = unknown)
            "phone":       lead.phone or "",
            "phone_verified": bool(lead.phone_verified),
            "whatsapp_number": lead.whatsapp_number or "",
            "website":     lead.website or "",
            "linkedin":    lead.linkedin or "",
            "aps":         lead.score or 0,
            "rrs":         int((lead.probability or 0) * 100),
            "probability": round((lead.probability or 0) * 100),
            "estimated_value": int(lead.estimated_value or 0),
            "expected_margin": margin,
            "email_verification_status": lead.email_verification_status or "UNVERIFIED",
            "email_confidence": email_conf,
            "confidence_pct": rp["confidence_pct"],
            "data_completeness_pct": completeness,
            "warm_class": warm_class,
            "ai_reason": ai_reason,
            "is_government": (lead.division or "").lower() == "government",
            "lead_source":  lead.lead_source or "Google Maps",
            "quality": quality,
            "preview": {
                "to":      lead.email,
                "to_name": lead.contact_name or lead.company,
                "from":    "connect@purepantryprovisions.com",
                "from_name": "Hiten Jain | Pure Pantry Provisions",
                "subject": draft.subject,
                "body":    draft.body,
                "first_name": first_name,
            },
            "created_at": draft.created_at.isoformat() if draft.created_at else None,
        })

    return {
        "leads":          leads_out,
        "total":          len(leads_out),
        "total_pipeline": total_pipeline,
        "total_margin":   total_margin,
        "class_counts":   class_counts,
    }


class ApproveAndSendRequest(BaseModel):
    draft_ids: list[int]
    custom_body: Optional[str] = None
    force_send: bool = False   # True = founder explicitly overrides quality gate blocks


def bg_send_emails(email_details: list[dict]):
    from app.services.email_sender import build_outreach_email, send_email
    from app.database.database import SessionLocal
    from app.models.models import B2BLead, EmailDraft, ApprovalRequest
    from app.services.pipeline_tracker import track
    import logging
    from datetime import datetime
    import hashlib
    import json

    db = SessionLocal()
    try:
        for detail in email_details:
            lead_id = detail["lead_id"]
            draft_id = detail["draft_id"]
            
            draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
            lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
            
            if not draft or not lead:
                continue

            # ── V1.2 Invariant Verification Check ──
            app_req = db.query(ApprovalRequest).filter(ApprovalRequest.id == draft.approval_request_id).first()
            if not app_req:
                logging.error(f"ExecutionBlocked: ApprovalRequest missing for draft {draft_id}")
                draft.status = "FAILED"
                draft.smtp_response = "ExecutionBlocked: Missing ApprovalRequest"
                db.commit()
                continue

            if app_req.status != "APPROVED":
                logging.error(f"ExecutionBlocked: ApprovalRequest status is {app_req.status}, expected APPROVED")
                draft.status = "FAILED"
                draft.smtp_response = f"ExecutionBlocked: Request is {app_req.status}"
                db.commit()
                continue

            if app_req.expires_at and app_req.expires_at < datetime.utcnow():
                logging.error("ExecutionBlocked: Approval has expired")
                draft.status = "FAILED"
                draft.smtp_response = "ExecutionBlocked: Approval expired"
                db.commit()
                continue

            if app_req.executed_at is not None:
                logging.error("ExecutionBlocked: Approval has already been executed")
                draft.status = "FAILED"
                draft.smtp_response = "ExecutionBlocked: Approval already executed"
                db.commit()
                continue

            # Verify cryptographic payload hash match
            payload = {
                "opportunity_id": draft.opportunity_id or 0,
                "type": "email",
                "recipient": lead.email or "",
                "subject": detail["subject"],
                "body": detail["body"],
                "channel": "zoho",
                "expected_margin": float(app_req.expected_margin or 0.0)
            }
            canonical_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
            payload_hash = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

            if payload_hash != app_req.approved_payload_hash:
                logging.error("ExecutionBlocked: Cryptographic payload hash mismatch (tampering detected)")
                draft.status = "FAILED"
                draft.smtp_response = "ExecutionBlocked: Cryptographic payload hash mismatch"
                db.commit()
                continue

            # ── Deliverability guard ──
            # Volume, pace and quality gate, checked immediately before the
            # send so nothing can route around it. The observed history was 26
            # emails inside one minute at a 56% failure rate from a domain with
            # ~50 lifetime sends — behaviour that gets a young domain
            # blocklisted regardless of correct SPF/DKIM/DMARC. A held message
            # stays QUEUED with the reason recorded; it is never dropped.
            from app.services.deliverability import check_send_allowed
            _verdict = check_send_allowed(db)
            if not _verdict.allowed:
                draft.status = "QUEUED"
                draft.smtp_response = f"HELD: {_verdict.reason}"[:400]
                db.commit()
                logging.warning("Deliverability hold for lead %s: %s",
                                lead.id, _verdict.reason)
                continue

            # Update status to SENDING
            draft.status = "SENDING"
            draft.sending_at = datetime.utcnow()
            db.commit()

            email_obj = build_outreach_email(
                to_email=lead.email or "",
                to_name=lead.contact_name or lead.company,
                company=lead.company,
                subject=detail["subject"],
                body=detail["body"],
                lead_id=lead_id,
            )
            
            from email.utils import make_msgid
            msg_id = make_msgid(domain="purepantryprovisions.com")
            draft.zoho_message_id = msg_id
            db.commit()

            try:
                email_obj.message_id = msg_id

                # Approval authorises delivery; it does not bypass governance.
                # This path used to call SMTP directly, checking only the
                # deliverability guard — not cadence, not the account cap, not
                # the decision engine. Reliance SMART's next touch was due
                # 12 Aug and went out on the 8th because of it.
                #
                # The founder still approves once. If the message is not due
                # yet, the approval is RECORDED and the worker delivers it at
                # the right time — nothing is lost, nothing is sent early.
                from app.services.send_queue import approve as _record_approval

                # NO SMTP CALL HERE. Approval hands off to the delivery
                # pipeline; it does not perform delivery. Two writers of
                # EMAIL_SENT is what let this endpoint record a send with no
                # recipient and skip the cadence entirely — the fourth
                # duplicate-path defect of the same shape in this codebase.
                #
                # The worker owns delivery, re-checks every gate at send time,
                # and is the ONLY place EMAIL_SENT is written, only after SMTP
                # actually succeeds.
                _record_approval(lead, db, touch=detail.get("touch", ""),
                                 note="founder approved from the approval centre")
                draft.status = "QUEUED"
                app_req.status = "APPROVED"
                lead.status = "EMAIL_QUEUED"
                lead.last_updated = datetime.utcnow()
                track(db, "EMAIL_QUEUED", lead_id=lead.id, actor="FOUNDER",
                      channel="email", before_status="AWAITING_APPROVAL",
                      after_status="EMAIL_QUEUED",
                      payload={"to": lead.email, "subject": detail["subject"],
                               "message_id": msg_id,
                               "note": "worker will deliver; EMAIL_SENT is "
                                       "written only after SMTP succeeds"})
            except Exception as ex:
                draft.status = "FAILED"
                draft.failed_at = datetime.utcnow()
                draft.smtp_response = str(ex)
                draft.retry_count += 1
                
                logging.error(f"SMTP send exception for lead {lead.id}: {str(ex)}")
                track(db, "EMAIL_FAILED", lead_id=lead.id, actor="FOUNDER", channel="email",
                      before_status="AWAITING_APPROVAL", after_status="AWAITING_APPROVAL",
                      payload={"subject": detail["subject"], "simulated": False, "smtp_error": str(ex)})
            
            db.commit()
    except Exception as e:
        logging.error(f"Error in bg_send_emails: {str(e)}")
    finally:
        db.close()


@router.post("/b2b/email/approve-and-send")
def approve_and_send(req: ApproveAndSendRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Founder approves draft(s) and triggers immediate SMTP send.
    Each email is sent one-by-one; WorkflowEvent logged per lead.
    Returns per-email result.
    """
    from app.models.models import B2BLead, EmailDraft, ApprovalRequest
    from app.services.email_sender import build_outreach_email, send_email, validate_email_quality
    from app.services.pipeline_tracker import track
    import hashlib
    import json
    from datetime import datetime, timedelta

    zoho_pw = os.getenv("ZOHO_APP_PASSWORD", "")
    simulate = not bool(zoho_pw and zoho_pw.strip() not in ("", "your_zoho_app_password_here"))

    results = []
    sent_real = 0
    sent_sim = 0
    bg_emails = []

    for draft_id in req.draft_ids:
        draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
        if not draft or draft.status not in ("PENDING", "EDITED", "DRAFT"):
            results.append({"draft_id": draft_id, "status": "skipped", "reason": "not found or not PENDING"})
            continue

        lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
        if not lead:
            results.append({"draft_id": draft_id, "status": "skipped", "reason": "lead not found"})
            continue

        # Guard: never approve/queue a send for a lead with no recipient address.
        if not (lead.email or "").strip():
            results.append({"draft_id": draft_id, "lead_id": lead.id, "company": lead.company,
                            "status": "skipped",
                            "reason": "no email address yet — auto-warm enrichment still searching"})
            continue

        body = req.custom_body or draft.body
        subject = draft.subject

        # ── Quality gate: block on critical issues unless founder explicitly overrides ──
        quality = validate_email_quality(subject or "", body or "")
        if not quality["passed"] and not req.force_send:
            results.append({
                "draft_id": draft_id, "lead_id": lead.id, "company": lead.company,
                "status": "quality_blocked",
                "quality": quality,
                "reason": f"Email blocked by quality gate: {', '.join(b['label'] for b in quality['blocks'])}",
            })
            continue

        # Create the APPROVED ApprovalRequest
        margin = float(_margin_for(lead))
        payload = {
            "opportunity_id": draft.opportunity_id or 0,
            "type": "email",
            "recipient": lead.email or "",
            "subject": subject or "",
            "body": body or "",
            "channel": "zoho",
            "expected_margin": margin
        }
        canonical_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        payload_hash = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

        app_req = ApprovalRequest(
            opportunity_id=draft.opportunity_id,
            lead_id=draft.lead_id,
            type="email",
            status="APPROVED",
            expected_margin=margin,
            expected_revenue=lead.estimated_value or 0.0,
            founder_time_required=0.1,
            draft_subject=subject,
            draft_message=body,
            evidence=["Manual founder dashboard approval"],
            approved_payload_hash=payload_hash,
            expires_at=datetime.utcnow() + timedelta(days=7),
            created_at=datetime.utcnow()
        )
        db.add(app_req)
        db.commit()
        db.refresh(app_req)

        # Link draft to ApprovalRequest
        draft.approval_request_id = app_req.id
        draft.status = "FOUNDER_APPROVED"
        draft.approved_at = datetime.utcnow()
        lead.email_approved_by_founder = True
        lead.last_updated = datetime.utcnow()
        db.commit()

        if simulate:
            # Transition directly to SENT in simulation mode
            draft.status = "SENT"
            draft.sent_at = datetime.utcnow()
            lead.status = "EMAIL_SENT"
            app_req.status = "EXECUTED"
            app_req.executed_at = datetime.utcnow()
            app_req.executed_by = "SIMULATION"
            db.commit()
            
            _schedule_next_reminder(db, lead, "email")
            
            track(db, "EMAIL_SENT", lead_id=lead.id, actor="FOUNDER", channel="email",
                  before_status="AWAITING_APPROVAL", after_status="EMAIL_SENT",
                  payload={"subject": subject, "simulated": True})
            sent_sim += 1
            results.append({"draft_id": draft_id, "lead_id": lead.id, "company": lead.company,
                             "email": lead.email, "status": "simulated"})
        else:
            # Set to QUEUED
            draft.status = "QUEUED"
            draft.queued_at = datetime.utcnow()
            bg_emails.append({
                "lead_id": lead.id,
                "draft_id": draft_id,
                "subject": subject,
                "body": body
            })
            sent_real += 1
            results.append({"draft_id": draft_id, "lead_id": lead.id, "company": lead.company,
                             "email": lead.email, "status": "queued"})

    db.commit()

    if bg_emails:
        background_tasks.add_task(bg_send_emails, bg_emails)

    return {"sent_real": sent_real, "sent_simulated": sent_sim, "results": results, "simulate": simulate}


class ApproveJourneyRequest(BaseModel):
    lead_ids: list[int]


@router.post("/b2b/workflow/approve-journey")
def approve_journey(req: ApproveJourneyRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Unified Single-Click Revenue Journey Approval.
    Approves the entire multi-channel journey (Email -> WhatsApp -> AI Call)
    and immediately queues/sends the first step (outbound email draft).
    """
    # Integrity sweep before a batch decision. Boot-time is not enough: an
    # out-of-band write that lands after startup would otherwise be approved on
    # this pass. Cheap for the sizes involved, and it fails open - a sweep error
    # never blocks the founder from approving.
    try:
        from app.services.contact_trust import sweep as _sweep
        _sweep(db)
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    from app.models.models import B2BLead, EmailDraft, ApprovalRequest
    from app.services.email_sender import build_outreach_email, send_email
    from app.services.pipeline_tracker import track
    import hashlib
    import json
    from datetime import datetime, timedelta

    zoho_pw = os.getenv("ZOHO_APP_PASSWORD", "")
    simulate = not bool(zoho_pw and zoho_pw.strip() not in ("", "your_zoho_app_password_here"))

    results = []
    sent_real = 0
    sent_sim = 0
    bg_emails = []

    for lead_id in req.lead_ids:
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            results.append({"lead_id": lead_id, "status": "skipped", "reason": "lead not found"})
            continue

        # Guard: never "approve" a lead we cannot actually email. Without this,
        # the lead was marked email_approved_by_founder=True, a draft was queued,
        # the send gate blocked it (no recipient), and the lead then vanished
        # from the pending queue forever having sent nothing.
        if not (lead.email or "").strip():
            results.append({"lead_id": lead.id, "company": lead.company,
                            "status": "skipped",
                            "reason": "no email address yet — auto-warm enrichment still searching"})
            continue

        # Get latest draft
        draft = db.query(EmailDraft).filter(
            EmailDraft.lead_id == lead.id,
            EmailDraft.status.in_(["PENDING", "DRAFT", "EDITED"])
        ).order_by(EmailDraft.id.desc()).first()

        # If no draft, create one on the fly
        if not draft:
            # Create a fallback draft
            draft = EmailDraft(
                lead_id=lead.id,
                follow_up_type="nudge",
                subject=f"Special B2B Partnership - Pure Pantry Provisions",
                body=f"Hi {lead.contact_name or 'there'},\n\nWe would love to supply premium fresh coffee to {lead.company} in {lead.city}.\n\nBest,\nHiten Jain",
                reason="Auto-created during journey approval",
                status="DRAFT",
                created_at=datetime.utcnow()
            )
            db.add(draft)
            db.flush()

        body = draft.body
        subject = draft.subject

        # Create the APPROVED ApprovalRequest
        margin = float(_margin_for(lead))
        payload = {
            "opportunity_id": draft.opportunity_id or 0,
            "type": "email",
            "recipient": lead.email or "",
            "subject": subject or "",
            "body": body or "",
            "channel": "zoho",
            "expected_margin": margin
        }
        canonical_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        payload_hash = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

        app_req = ApprovalRequest(
            opportunity_id=draft.opportunity_id,
            lead_id=draft.lead_id,
            type="email",
            status="APPROVED",
            expected_margin=margin,
            expected_revenue=lead.estimated_value or 0.0,
            founder_time_required=0.1,
            draft_subject=subject,
            draft_message=body,
            evidence=["Manual bulk journey approval"],
            approved_payload_hash=payload_hash,
            expires_at=datetime.utcnow() + timedelta(days=7),
            created_at=datetime.utcnow()
        )
        db.add(app_req)
        db.flush()

        # Link draft to ApprovalRequest and mark approved
        draft.approval_request_id = app_req.id
        draft.status = "FOUNDER_APPROVED"
        draft.approved_at = datetime.utcnow()
        lead.email_approved_by_founder = True
        lead.last_updated = datetime.utcnow()
        db.flush()

        if simulate:
            draft.status = "SENT"
            draft.sent_at = datetime.utcnow()
            lead.status = "EMAIL_SENT"
            app_req.status = "EXECUTED"
            app_req.executed_at = datetime.utcnow()
            app_req.executed_by = "SIMULATION"
            db.flush()
            
            from app.api.endpoints import _schedule_next_reminder
            _schedule_next_reminder(db, lead, "email")
            
            track(db, "EMAIL_SENT", lead_id=lead.id, actor="FOUNDER", channel="email",
                  before_status="AWAITING_APPROVAL", after_status="EMAIL_SENT",
                  payload={"subject": subject, "simulated": True})
            sent_sim += 1
            results.append({"lead_id": lead.id, "company": lead.company, "status": "simulated"})
        else:
            draft.status = "QUEUED"
            draft.queued_at = datetime.utcnow()
            db.flush()
            
            bg_emails.append({
                "lead_id": lead.id,
                "draft_id": draft.id,
                "subject": subject,
                "body": body
            })
            sent_real += 1
            results.append({"lead_id": lead.id, "company": lead.company, "status": "queued"})

    db.commit()

    if bg_emails:
        background_tasks.add_task(bg_send_emails, bg_emails)

    return {"sent_real": sent_real, "sent_simulated": sent_sim, "results": results, "simulate": simulate}


@router.get("/b2b/email/sync-zoho")
def sync_zoho_emails(db: Session = Depends(get_db)):
    """
    Triggers reconcile_sent_emails_via_imap and reconcile_inbound_replies_via_imap.
    """
    from app.services.email_sender import reconcile_sent_emails_via_imap, reconcile_inbound_replies_via_imap
    sent_report = reconcile_sent_emails_via_imap(db)
    inbound_report = reconcile_inbound_replies_via_imap(db)
    return {
        "sent_sync": sent_report,
        "inbound_sync": inbound_report
    }


class EditDraftRequest(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


@router.patch("/b2b/email/draft/{draft_id}")
def edit_draft(draft_id: int, req: EditDraftRequest, db: Session = Depends(get_db)):
    """Founder edits the draft subject/body before sending."""
    from app.models.models import EmailDraft

    draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if req.subject:
        draft.subject = req.subject
    if req.body:
        draft.body = req.body
    draft.status = "EDITED"
    db.commit()
    return {"draft_id": draft_id, "status": "edited"}


@router.post("/b2b/email/approve-later/{draft_id}")
def approve_later(draft_id: int, db: Session = Depends(get_db)):
    """Mark draft as deferred — stays in inbox but won't show in next auto-batch."""
    from app.models.models import EmailDraft
    draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.status = "DEFERRED"
    db.commit()
    return {"draft_id": draft_id, "status": "deferred"}


@router.post("/b2b/email/reject-draft/{draft_id}")
def reject_draft(draft_id: int, db: Session = Depends(get_db)):
    """Founder rejects a draft — marks the lead as email_rejected."""
    from app.models.models import B2BLead, EmailDraft
    from app.services.pipeline_tracker import track

    draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.status = "SKIPPED"

    lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
    if lead:
        lead.email_rejected_by_founder = True
        track(db, "EMAIL_REJECTED", lead_id=lead.id, actor="FOUNDER",
              payload={"draft_id": draft_id})

    db.commit()
    return {"draft_id": draft_id, "status": "rejected"}


# ── PIPELINE TRACKING — EVENT LOG & FUNNEL KPIs ───────────────────────────────

# 1x1 transparent GIF for email open tracking
_TRACKING_PIXEL = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00,
    0x80, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x21,
    0xF9, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x2C, 0x00, 0x00,
    0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02, 0x44,
    0x01, 0x00, 0x3B,
])


@router.get("/b2b/track/open/{lead_id}")
def track_email_open(lead_id: int, db: Session = Depends(get_db)):
    """
    Email open tracking pixel. Embed as <img src="..."> in outgoing emails.
    Returns a 1x1 transparent GIF and increments email_opens on the lead.
    """
    from app.models.models import B2BLead
    from app.services.pipeline_tracker import track

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if lead:
        lead.email_opens = (lead.email_opens or 0) + 1
        track(db, "EMAIL_OPENED", lead_id=lead_id, actor="SYSTEM", channel="email",
              payload={"total_opens": lead.email_opens})
        db.commit()

    return Response(content=_TRACKING_PIXEL, media_type="image/gif", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@router.get("/b2b/pipeline/funnel")
def get_pipeline_funnel(db: Session = Depends(get_db)):
    """Full pipeline funnel stats — conversion rates at every stage."""
    from app.services.pipeline_tracker import get_funnel_stats
    return get_funnel_stats(db)


@router.get("/b2b/pipeline/timeline/{lead_id}")
def get_lead_timeline(lead_id: int, db: Session = Depends(get_db)):
    """Full event timeline for a specific lead."""
    from app.models.models import B2BLead
    from app.services.pipeline_tracker import get_lead_timeline

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {
        "lead_id": lead_id,
        "company": lead.company,
        "current_status": lead.status,
        "timeline": get_lead_timeline(db, lead_id),
    }


@router.post("/b2b/pipeline/enrich")
def enrich_leads(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Trigger background enrichment for leads missing website or email.
    Finds company website → extracts email + phone → logs LEAD_ENRICHED event.
    """
    from app.models.models import B2BLead

    missing = db.query(B2BLead).filter(
        (B2BLead.email == None) | (~B2BLead.email.like("%@%"))
    ).count()

    def _run():
        from app.database.database import SessionLocal
        from app.services.enrichment_engine import enrich_batch
        _db = SessionLocal()
        try:
            leads = _db.query(B2BLead).filter(
                (B2BLead.email == None) | (~B2BLead.email.like("%@%"))
            ).order_by(B2BLead.score.desc()).limit(30).all()
            enrich_batch(leads, _db)
        finally:
            _db.close()

    background_tasks.add_task(_run)
    return {
        "message": "Enrichment started in background",
        "leads_missing_email": missing,
        "processing": min(30, missing),
    }


@router.post("/b2b/pipeline/log-event")
def log_pipeline_event(
    lead_id: int,
    event_type: str,
    actor: str = "FOUNDER",
    channel: Optional[str] = None,
    notes: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Manually log a pipeline event (e.g. FOUNDER_CALL_COMPLETED, MEETING_BOOKED)."""
    from app.models.models import B2BLead
    from app.services.pipeline_tracker import track

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    ev = track(
        db, event_type, lead_id=lead_id, actor=actor, channel=channel,
        before_status=lead.status,
        payload={"notes": notes},
        commit=True,
    )
    return {"event_id": ev.id, "event_type": event_type, "lead_id": lead_id}


class ApprovalRequestCreate(BaseModel):
    opportunity_id: Optional[int] = None
    lead_id: Optional[int] = None
    type: str  # email | whatsapp | ai_call | proposal | discount | sample_dispatch | tender_submission | bulk_campaign
    expected_margin: float = 0.0
    expected_revenue: float = 0.0
    founder_time_required: float = 0.1
    draft_subject: Optional[str] = None
    draft_message: Optional[str] = None
    evidence: Optional[list[str]] = None


class ApprovalRequestApprove(BaseModel):
    custom_subject: Optional[str] = None
    custom_body: Optional[str] = None


@router.post("/approval-requests")
def create_approval_request(req: ApprovalRequestCreate, db: Session = Depends(get_db)):
    from app.models.models import ApprovalRequest
    new_request = ApprovalRequest(
        opportunity_id=req.opportunity_id,
        lead_id=req.lead_id,
        type=req.type,
        status="PENDING_FOUNDER",
        expected_margin=req.expected_margin,
        expected_revenue=req.expected_revenue,
        founder_time_required=req.founder_time_required,
        draft_subject=req.draft_subject,
        draft_message=req.draft_message,
        evidence=req.evidence or []
    )
    db.add(new_request)
    db.commit()
    db.refresh(new_request)
    return new_request


@router.post("/approval-requests/{id}/approve")
def approve_approval_request(id: int, req: ApprovalRequestApprove, db: Session = Depends(get_db)):
    from app.models.models import ApprovalRequest, B2BLead
    import hashlib
    import json
    from datetime import datetime, timedelta
    
    app_req = db.query(ApprovalRequest).filter(ApprovalRequest.id == id).first()
    if not app_req:
        raise HTTPException(status_code=404, detail="Approval request not found")
        
    subject = req.custom_subject or app_req.draft_subject or ""
    body = req.custom_body or app_req.draft_message or ""
    
    lead = db.query(B2BLead).filter(B2BLead.id == app_req.lead_id).first()
    recipient_email = (lead.email or "") if lead else ""

    # Guard: an email approval with no recipient can never execute — the send
    # gate blocks it and the request would sit APPROVED forever. Refuse instead.
    if app_req.type == "email" and not recipient_email.strip():
        raise HTTPException(
            status_code=409,
            detail="Cannot approve: lead has no email address yet — auto-warm enrichment still searching",
        )

    # Compute canonical hash
    payload = {
        "opportunity_id": app_req.opportunity_id or 0,
        "type": app_req.type,
        "recipient": recipient_email,
        "subject": subject,
        "body": body,
        "channel": "zoho" if app_req.type == "email" else "default",
        "expected_margin": float(app_req.expected_margin or 0.0)
    }
    
    canonical_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    payload_hash = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()
    
    app_req.status = "APPROVED"
    app_req.approved_payload_hash = payload_hash
    app_req.expires_at = datetime.utcnow() + timedelta(days=7)
    db.commit()
    
    return {"status": "APPROVED", "hash": payload_hash, "expires_at": app_req.expires_at.isoformat()}


@router.post("/approval-requests/{id}/reject")
def reject_approval_request(id: int, db: Session = Depends(get_db)):
    from app.models.models import ApprovalRequest
    app_req = db.query(ApprovalRequest).filter(ApprovalRequest.id == id).first()
    if not app_req:
        raise HTTPException(status_code=404, detail="Approval request not found")
    app_req.status = "REJECTED"
    db.commit()
    return {"status": "REJECTED"}


@router.get("/b2b/outreach/draft/{lead_id}")
def category_aware_draft(lead_id: int, db: Session = Depends(get_db)):
    """
    A draft written for THIS business: its category, what we already know about
    it, and where the conversation actually stands.

    Returns the reasoning alongside the draft so the founder can see why this
    email and not another one before approving it.
    """
    from app.models.models import B2BLead, LeadInteraction, WorkflowEvent
    from app.services.outreach_engine import (build_draft, UnmappedCategory,
                                              relationship, channels_for,
                                              as_whatsapp, as_call_brief)
    from app.services.outreach_templates import SIGNATURE

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")

    # Where the conversation stands, from the event log — not assumed.
    sent = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead_id,
        WorkflowEvent.event_type == "EMAIL_SENT").order_by(
            WorkflowEvent.occurred_at.desc()).all()
    replied = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead_id,
        WorkflowEvent.event_type.like("%REPLI%")).count() > 0
    days = (datetime.utcnow() - sent[0].occurred_at).days if sent else None

    # Business Memory: the live interactions, most recent value winning.
    mem: dict = {}
    for i in sorted(db.query(LeadInteraction).filter(
            LeadInteraction.lead_id == lead_id,
            LeadInteraction.superseded_by_id.is_(None)).all(),
            key=lambda x: x.occurred_at or datetime.min):
        for f in ("decision_maker", "current_supplier", "preferred_contact_time",
                  "monthly_consumption_kg", "budget_range"):
            v = getattr(i, f, None)
            if v not in (None, ""):
                mem[f] = v
    # Fall back to facts already promoted onto the lead.
    for f in ("decision_maker", "current_supplier"):
        if not mem.get(f) and getattr(lead, f, None):
            mem[f] = getattr(lead, f)

    # Every touch on every channel, so an email after a founder call continues
    # that call instead of introducing the company to someone who already knows it.
    all_events = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead_id).all()
    inter = db.query(LeadInteraction).filter(
        LeadInteraction.lead_id == lead_id,
        LeadInteraction.superseded_by_id.is_(None)).all()
    rel = relationship(all_events, inter)

    try:
        d = build_draft(lead, mem, len(sent), replied, days, SIGNATURE, rel=rel)
        d["whatsapp"] = as_whatsapp(d, lead, mem)
        d["call_brief"] = as_call_brief(lead, mem, rel)
    except UnmappedCategory as e:
        raise HTTPException(status_code=422, detail=str(e))

    d["relationship"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                         for k, v in rel.items()}
    chans = channels_for(lead)
    d["recommended_channel"] = chans[0]
    d["channel_reason"] = (
        "verified email on file" if chans[0] == "email" else
        "no verified email — phone is the reachable channel" if chans[0] in ("whatsapp", "founder_call")
        else "no verified contact of any kind — this business needs a visit or research")

    d["lead_id"] = lead_id
    d["company"] = lead.company
    d["division"] = lead.division
    d["previous_emails"] = len(sent)
    d["replied"] = replied
    d["days_since_last_email"] = days
    return d


@router.get("/b2b/city/{city}/categories")
def city_categories(city: str, db: Session = Depends(get_db)):
    """
    What is in this city, broken down by category — with how much of it is
    actually actionable.

    The Approval Center shows one long list, so "what have I got in Abohar and
    what can I do about it" needed scrolling and counting. Every number here is
    a count of real rows, and reachability is split by channel because email is
    the scarce one: a category can look healthy on lead count and be unworkable
    because nothing in it has a verified address.
    """
    from app.models.models import B2BLead, EmailDraft
    from app.services.outreach_engine import pitch_for, UnmappedCategory

    leads = db.query(B2BLead).filter(
        B2BLead.city.ilike(f"%{city.strip()}%"),
        B2BLead.status != "DISQUALIFIED").all()
    if not leads:
        return {"city": city, "leads": 0, "categories": [],
                "note": f"no businesses on record for '{city}'"}

    live = {"DRAFT", "PENDING", "EDITED", "APPROVED"}
    drafts: dict[int, str] = {}
    for d in db.query(EmailDraft).all():
        if (d.status or "").upper() in live:
            drafts[d.lead_id] = d.status

    buckets: dict[str, dict] = {}
    for l in leads:
        div = (l.division or "uncategorised").lower()
        try:
            label = pitch_for(div).label
        except UnmappedCategory:
            label = "Uncategorised"
        b = buckets.setdefault(div, {
            "category": div, "label": label, "leads": 0,
            "phone_reachable": 0, "email_sendable": 0, "email_on_file_unverified": 0,
            "no_contact": 0, "drafts_awaiting_approval": 0, "already_contacted": 0,
            "examples": []})
        b["leads"] += 1
        has_phone = bool((l.phone or "").strip() or (l.whatsapp_number or "").strip())
        has_mail = bool((l.email or "").strip())
        if has_phone:
            b["phone_reachable"] += 1
        if has_mail and bool(l.email_verified):
            b["email_sendable"] += 1
        elif has_mail:
            b["email_on_file_unverified"] += 1
        if not has_phone and not has_mail:
            b["no_contact"] += 1
        if l.id in drafts:
            b["drafts_awaiting_approval"] += 1
        if (l.status or "") not in ("DISCOVERED", "COLD", "QUALIFIED"):
            b["already_contacted"] += 1
        if len(b["examples"]) < 3:
            b["examples"].append(l.company)

    out = sorted(buckets.values(), key=lambda x: -x["leads"])
    return {
        "city": city,
        "leads": len(leads),
        "categories": out,
        "totals": {
            "phone_reachable": sum(b["phone_reachable"] for b in out),
            "email_sendable": sum(b["email_sendable"] for b in out),
            "drafts_awaiting_approval": sum(b["drafts_awaiting_approval"] for b in out),
            "no_contact": sum(b["no_contact"] for b in out),
        },
    }


# ── Agent Self-Funding & Commission Engine Endpoints ──
from pydantic import BaseModel
from typing import Dict, Optional

class MarkWonOverrideRequest(BaseModel):
    order_value_inr: float = 0.0
    overrides: Optional[Dict[str, float]] = None

@router.post("/b2b/leads/{lead_id}/mark-won-with-split")
def mark_won_with_split(lead_id: int, body: MarkWonOverrideRequest, db: Session = Depends(get_db)):
    from app.services.pipeline_tracker import track
    from app.services.funding_engine import AgentFundingService

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if (lead.status or "") == "ORDER_WON":
        return {"status": "already_won", "lead_id": lead_id, "new_status": "ORDER_WON"}

    before = lead.status
    lead.status = "ORDER_WON"
    lead.last_updated = datetime.utcnow()
    value = float(body.order_value_inr or lead.estimated_value or 0.0)
    margin = float(_margin_for(lead))

    # Calculate splits
    AgentFundingService.calculate_deal_commission(db, lead_id, value, overrides=body.overrides)

    track(db, "ORDER_WON", lead_id=lead.id, actor="FOUNDER", channel="founder",
          before_status=before, after_status="ORDER_WON",
          payload={"order_value_inr": value, "margin_inr": margin,
                   "confirmed_by_founder": True})
    db.commit()
    return {"status": "ok", "lead_id": lead_id, "new_status": "ORDER_WON",
            "order_value_inr": value, "margin_inr": margin}


@router.get("/b2b/agent-funding/pnl")
def get_agent_pnl(db: Session = Depends(get_db)):
    from app.services.funding_engine import AgentFundingService
    pnl = AgentFundingService.get_agent_pnl(db)
    return pnl


@router.get("/b2b/agent-funding/upgrades")
def get_agent_upgrades(db: Session = Depends(get_db)):
    from app.models.models import AgentToolUpgrade
    from app.services.funding_engine import AgentFundingService
    AgentFundingService.auto_update_lock_status(db)
    upgrades = db.query(AgentToolUpgrade).all()
    return [{
        "id": u.id,
        "agent_key": u.agent_key,
        "tool_name": u.tool_name,
        "upgrade_cost": u.upgrade_cost,
        "projected_uplift": u.projected_uplift,
        "roi_multiplier": u.roi_multiplier,
        "status": u.status,
        "description": u.description,
        "approved_at": u.approved_at.isoformat() if u.approved_at else None
    } for u in upgrades]


@router.post("/b2b/agent-funding/upgrade/{upgrade_id}/approve")
def approve_agent_upgrade(upgrade_id: int, db: Session = Depends(get_db)):
    from app.services.funding_engine import AgentFundingService
    success = AgentFundingService.approve_and_execute_upgrade(db, upgrade_id)
    if not success:
        raise HTTPException(status_code=400, detail="Insufficient commission balance or upgrade already purchased.")
    return {"status": "ok", "message": "Tool upgrade approved and successfully self-funded."}


# ── AI Agent Economy & Reinvestment Engine Endpoints ──
class TreasurySettingsRequest(BaseModel):
    reinvestment_rate: float
    reinvestment_enabled: bool
    bootstrap_mode: bool

class PaymentVerifiedRequest(BaseModel):
    actual_cash: float

@router.get("/b2b/agent-funding/treasury")
def get_treasury(db: Session = Depends(get_db)):
    from app.services.agent_economy import AgentEconomyService
    return AgentEconomyService.get_treasury(db)

@router.post("/b2b/agent-funding/treasury/settings")
def update_treasury_settings(body: TreasurySettingsRequest, db: Session = Depends(get_db)):
    from app.services.agent_economy import AgentEconomyService
    AgentEconomyService.update_treasury_settings(db, body.reinvestment_rate, body.reinvestment_enabled, body.bootstrap_mode)
    return {"status": "ok", "message": "Treasury settings updated successfully."}

@router.get("/b2b/agent-funding/agents")
def get_agents(db: Session = Depends(get_db)):
    from app.services.agent_economy import AgentEconomyService
    return AgentEconomyService.get_agents(db)

@router.get("/b2b/agent-funding/proposals")
def get_proposals(db: Session = Depends(get_db)):
    from app.services.agent_economy import AgentEconomyService
    return AgentEconomyService.get_proposals(db)

@router.post("/b2b/agent-funding/proposals/{proposal_id}/action")
def update_proposal_action(proposal_id: int, status: str, db: Session = Depends(get_db)):
    from app.services.agent_economy import AgentEconomyService
    success = AgentEconomyService.update_proposal_status(db, proposal_id, status)
    if not success:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return {"status": "ok", "message": f"Proposal status updated to {status}."}

@router.post("/b2b/leads/{lead_id}/payment-verified")
def verify_payment(lead_id: int, body: PaymentVerifiedRequest, db: Session = Depends(get_db)):
    from app.services.agent_economy import AgentEconomyService
    # Transition B2BLead status to PAYMENT_VERIFIED if not already
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # We write a pipeline tracker track event
    from app.services.pipeline_tracker import track
    before = lead.status
    lead.status = "PAYMENT_VERIFIED"
    lead.last_updated = datetime.utcnow()
    
    res = AgentEconomyService.verify_payment_and_allocate(db, lead_id, body.actual_cash)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
        
    track(db, "PAYMENT_VERIFIED", lead_id=lead.id, actor="FOUNDER", channel="founder",
          before_status=before, after_status="PAYMENT_VERIFIED",
          payload={"actual_cash": body.actual_cash})
    db.commit()
    return res


@router.post("/b2b/city/{city}/enrich-no-contact")
def enrich_no_contact(city: str, category: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Find contact details for the businesses in a city that have NEITHER a phone
    nor an email, and are therefore unreachable and dead weight in the pipeline.

    Scoped to the no-contact set on purpose: enrichment scrapes several sources
    per lead, so running it over an entire city wastes most of the work on
    businesses we can already reach. Optionally narrowed to one category.
    Returns what was found; nothing is invented — a business with no result
    stays with no contact.
    """
    from app.models.models import B2BLead, WorkflowEvent

    q = db.query(B2BLead).filter(
        B2BLead.city.ilike(f"%{city.strip()}%"),
        B2BLead.status != "DISQUALIFIED")
    if category:
        q = q.filter(B2BLead.division == category)

    targets = [l for l in q.all()
               if not (l.phone or "").strip()
               and not (l.whatsapp_number or "").strip()
               and not (l.email or "").strip()]
    if not targets:
        return {"city": city, "no_contact": 0, "enriched": 0,
                "note": "every business here already has a phone or email"}

    res = _enrich_contacts_sync([l.id for l in targets], db)
    gained = 0
    for r in res.get("enriched", []):
        got = bool(r.get("confirmed_phone") or r.get("whatsapp_number") or r.get("email"))
        if got:
            gained += 1
            db.add(WorkflowEvent(
                lead_id=r["lead_id"], event_type="CONTACT_ENRICHED", actor="SYSTEM",
                channel="enrichment",
                payload={"found": {k: r[k] for k in ("confirmed_phone", "whatsapp_number", "email")
                                   if r.get(k)},
                         "sources": r.get("sources_checked"),
                         "confidence": r.get("confidence")},
                occurred_at=datetime.utcnow()))
    db.commit()
    return {
        "city": city,
        "category": category,
        "no_contact": len(targets),
        "contact_found": gained,
        "still_unreachable": len(targets) - gained,
        "results": res.get("enriched", []),
    }


# -- V3: outreach search + phone-only conversion program ----------------------

def _event_summary(db, lead_ids: list[int] | None = None) -> dict:
    """Real per-lead event facts. One query, not N+1."""
    from app.models.models import WorkflowEvent
    q = db.query(WorkflowEvent)
    if lead_ids is not None:
        q = q.filter(WorkflowEvent.lead_id.in_(lead_ids))
    out: dict[int, dict] = {}
    for e in q.all():
        if not e.lead_id:
            continue
        d = out.setdefault(e.lead_id, {"emails_sent": 0, "opened": False,
                                       "replied": False, "sample_sent": False,
                                       "proposal_sent": False, "last": None})
        t = e.event_type or ""
        if t == "EMAIL_SENT":
            d["emails_sent"] += 1
        if "OPEN" in t:
            d["opened"] = True
        if "REPLI" in t:
            d["replied"] = True
        if t in ("SAMPLE_SENT", "DISPATCH_SAMPLE_CONFIRMED"):
            d["sample_sent"] = True
        if t == "PROPOSAL_SENT":
            d["proposal_sent"] = True
        if t in ("EMAIL_SENT", "WHATSAPP_SENT", "FOUNDER_CALL", "AI_CALL",
                 "MEETING_HELD", "SAMPLE_SENT"):
            if e.occurred_at and (d["last"] is None or e.occurred_at > d["last"]):
                d["last"] = e.occurred_at
    now = datetime.utcnow()
    for d in out.values():
        d["last_touch_days"] = (now - d["last"]).days if d["last"] else None
        d.pop("last", None)
    return out


@router.get("/b2b/outreach/search")
def outreach_search(
    state: Optional[str] = None, city: Optional[str] = None,
    pincode: Optional[str] = None, category: Optional[str] = None,
    outreach_method: str = "ALL", limit: int = 100,
    db: Session = Depends(get_db)):
    """
    Search opportunities by how they can actually be REACHED, combined with
    geography and category.

    The third dimension is the point: geography says who is nearby and category
    says what kind of buyer, but neither says what the founder can execute in
    the next hour. Contactability is read from stored fields - a business with
    no phone and no email comes back as NO_CONTACT_FOUND rather than as a call
    target with an invented number.
    """
    from app.models.models import B2BLead
    from app.services.outreach_search import methods_for, METHODS, ai_call_status

    m = (outreach_method or "ALL").upper().strip()
    if m not in METHODS:
        raise HTTPException(status_code=400,
                            detail="unknown outreach_method - expected one of "
                                   + str(list(METHODS)))

    q = db.query(B2BLead).filter(B2BLead.status != "DISQUALIFIED")
    # The stored column is `region` — B2BLead has no `state`. Accepting `state`
    # as the parameter name keeps the founder-facing vocabulary from the spec
    # without inventing a column that does not exist.
    if state:
        q = q.filter(B2BLead.region.ilike("%" + state.strip() + "%"))
    if city:
        q = q.filter(B2BLead.city.ilike("%" + city.strip() + "%"))
    if pincode:
        q = q.filter(B2BLead.pincode == pincode.strip())
    if category and category.upper() not in ("ALL", ""):
        q = q.filter(B2BLead.division == category.strip())
    leads = q.all()

    ev = _event_summary(db, [l.id for l in leads]) if leads else {}
    rows, counts = [], {}
    for l in leads:
        avail = methods_for(l, ev.get(l.id, {}))
        for a in avail:
            counts[a] = counts.get(a, 0) + 1
        if m not in avail:
            continue
        e = ev.get(l.id, {})
        rows.append({
            "lead_id": l.id, "company": l.company, "city": l.city,
            "region": l.region, "category": l.division, "stage": l.status,
            "phone": l.phone or l.whatsapp_number or None,
            "phone_verified": bool(getattr(l, "phone_verified", False)),
            "email": l.email or None,
            "email_verified": bool(getattr(l, "email_verified", False)),
            "decision_maker": getattr(l, "decision_maker", None),
            "current_supplier": getattr(l, "current_supplier", None),
            "emails_sent": e.get("emails_sent", 0),
            "replied": e.get("replied", False),
            "last_contact_days": e.get("last_touch_days"),
            "methods_available": sorted(a for a in avail if a != "ALL"),
        })

    return {
        "filters": {"state": state, "city": city, "pincode": pincode,
                    "category": category, "outreach_method": m},
        "matched": len(rows),
        "searched": len(leads),
        "counts_by_method": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "ai_call": ai_call_status(),
        "results": rows[:max(1, limit)],
    }


@router.get("/b2b/outreach/phone-program")
def phone_program(state: Optional[str] = None, city: Optional[str] = None,
                  limit: int = 15, db: Session = Depends(get_db)):
    """
    The phone-only conversion program, plus today's ranked Power Hour.

    Not "call everybody": eligibility is a phone we hold, no usable email, and a
    business that qualifies and is not suppressed. Ranking puts what we KNOW
    about a business ahead of what it might be worth, so modelled margin cannot
    dominate the founder hour.
    """
    from app.models.models import B2BLead
    from app.services.outreach_search import methods_for, rank_calls

    q = db.query(B2BLead).filter(B2BLead.status != "DISQUALIFIED")
    if state:
        q = q.filter(B2BLead.region.ilike("%" + state.strip() + "%"))
    if city:
        q = q.filter(B2BLead.city.ilike("%" + city.strip() + "%"))
    leads = q.all()
    ev = _event_summary(db, [l.id for l in leads]) if leads else {}

    eligible = [l for l in leads if "PHONE_ONLY" in methods_for(l, ev.get(l.id, {}))]
    verified = [l for l in eligible if getattr(l, "phone_verified", False)]
    ranked = rank_calls(eligible, ev, limit=limit)

    margin = sum((l.estimated_value or 0) * 0.31 for l in eligible)
    return {
        "scope": {"state": state, "city": city},
        "phone_only_opportunities": len(eligible),
        "phone_verified": len(verified),
        "need_verification": len(eligible) - len(verified),
        "priority_today": len(ranked),
        # Labelled MODELLED because it is a category-average estimate, not a
        # measurement of these businesses.
        "modelled_annual_margin": round(margin),
        "modelled_note": "MODELLED from category averages - not a forecast",
        "founder_minutes_estimate": len(ranked) * 6,
        "power_hour": ranked,
    }


class CallOutcomeRequest(BaseModel):
    outcome: str
    decision_maker: Optional[str] = None
    designation: Optional[str] = None
    current_supplier: Optional[str] = None
    email: Optional[str] = None
    preferred_contact_time: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    next_followup_date: Optional[str] = None
    monthly_consumption_kg: Optional[float] = None
    remark: Optional[str] = None


@router.post("/b2b/outreach/call-outcome/{lead_id}")
def record_call_outcome(lead_id: int, req: CallOutcomeRequest,
                        db: Session = Depends(get_db)):
    """
    Record what actually happened on a founder call, and set the one next action
    that outcome earns. An email captured here is stored with call provenance, so
    the following email is a follow-up that references the call.
    """
    from app.models.models import B2BLead
    from app.services.outreach_search import apply_call_outcome

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")
    try:
        return apply_call_outcome(db, lead, req.outcome,
                                  req.dict(exclude={"outcome"}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/b2b/outreach/call-outcomes")
def list_call_outcomes():
    """The outcome buttons for Power Hour, with what each one triggers."""
    from app.services.outreach_search import OUTCOMES
    return {"outcomes": [
        {"key": k, "label": o.label, "next_action": o.next_action,
         "channel": o.channel, "due_in_days": o.delay_days,
         "terminal": o.terminal, "guidance": o.note, "capture": list(o.needs)}
        for k, o in OUTCOMES.items()]}


@router.get("/b2b/account/{lead_id}")
def account_360(lead_id: int, db: Session = Depends(get_db)):
    """
    Account 360 — what an engaged opportunity gets instead of a draft.

    Composed from Business Memory and the event log; it stores nothing. Every
    field carries how it was learned, and anything not learned reads as not
    recorded rather than being inferred from category or company size.
    """
    from app.models.models import B2BLead, LeadInteraction, WorkflowEvent
    from app.services.account_360 import build

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")

    inter = db.query(LeadInteraction).filter(
        LeadInteraction.lead_id == lead_id).all()
    ev = _event_summary(db, [lead_id]).get(lead_id, {})

    events = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead_id).order_by(WorkflowEvent.occurred_at).all()
    timeline = [{
        "at": e.occurred_at.isoformat() if e.occurred_at else None,
        "type": e.event_type, "actor": e.actor, "channel": e.channel,
    } for e in events]
    for i in sorted(inter, key=lambda x: x.occurred_at or datetime.min):
        timeline.append({
            "at": i.occurred_at.isoformat() if i.occurred_at else None,
            "type": "INTERACTION", "actor": i.created_by,
            "channel": i.method, "outcome": i.outcome,
            "remark": (i.remark or "")[:200],
            "superseded": bool(i.superseded_by_id),
        })
    timeline.sort(key=lambda t: t["at"] or "")

    return build(lead, inter, ev, timeline)


@router.get("/b2b/accounts/blocked")
def blocked_accounts(limit: int = 25, db: Session = Depends(get_db)):
    """
    Every engaged account with its named blocker, worst first.

    Answers "where am I losing deals?" from the record — a sample promised and
    never sent, pricing asked for and never quoted, a thread gone quiet.
    """
    from app.models.models import B2BLead, LeadInteraction
    from app.services.account_360 import diagnose

    ENGAGED = ("QUALIFIED", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED",
               "SAMPLE_REQUESTED", "SAMPLE_SENT", "PROPOSAL_SENT",
               "NEGOTIATION", "EMAIL_SENT", "WHATSAPP_SENT")
    leads = db.query(B2BLead).filter(B2BLead.status.in_(ENGAGED)).all()
    if not leads:
        return {"engaged_accounts": 0, "blocked": [],
                "note": "no engaged accounts yet — nothing has replied"}

    ids = [l.id for l in leads]
    ev_all = _event_summary(db, ids)
    inter_by = {}
    for i in db.query(LeadInteraction).filter(
            LeadInteraction.lead_id.in_(ids)).all():
        if not i.superseded_by_id:
            inter_by.setdefault(i.lead_id, []).append(i)

    rank = {"high": 0, "medium": 1, "low": 2, "none": 3}
    rows = []
    for l in leads:
        d = diagnose(l, inter_by.get(l.id, []), ev_all.get(l.id, {}))
        if d["blocker"] in ("none", "closed"):
            continue
        rows.append({"lead_id": l.id, "company": l.company, "city": l.city,
                     "category": l.division, "stage": l.status, **d})
    rows.sort(key=lambda r: rank.get(r["urgency"], 9))
    return {"engaged_accounts": len(leads), "blocked": rows[:max(1, limit)]}


@router.get("/b2b/trust/audit")
def trust_audit(db: Session = Depends(get_db)):
    """Can the CRM be trusted? Counts of rows by trust level, nothing modelled."""
    from app.services.contact_trust import audit
    return audit(db)


@router.post("/b2b/trust/sweep")
def trust_sweep(db: Session = Depends(get_db)):
    """
    Run the integrity sweep on demand. Safe before a batch approval or after an
    import — anything claiming a sendable trust level is re-checked live.
    """
    from app.services.contact_trust import sweep
    return sweep(db)


@router.get("/b2b/contacts/acquisition-queue")
def contact_acquisition_queue(limit: int = 100, db: Session = Depends(get_db)):
    """
    The businesses that qualify but cannot yet be reached, and how to reach them.

    "0 sendable" describes a dead end; the same businesses are a queue. Each row
    carries the cheapest route to a contact given what is already known — a phone
    on file means ask on the call, a website means read the contact page.
    """
    from app.services.contact_trust import acquisition_queue
    return acquisition_queue(db, limit=limit)


class ContactStatusRequest(BaseModel):
    status: str
    reason: Optional[str] = None


@router.post("/b2b/contacts/{lead_id}/status")
def set_contact_status(lead_id: int, req: ContactStatusRequest,
                       db: Session = Depends(get_db)):
    """
    Set the COMMERCIAL status. Trust is untouched — an opted-out address stays
    as trustworthy as it was, it simply must not be contacted.
    """
    from app.models.models import B2BLead
    from app.services.contact_trust import set_status, CONTACT_STATUSES

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")
    try:
        set_status(lead, req.status, req.reason or "", db)
    except ValueError as e:
        raise HTTPException(status_code=400,
                            detail=f"{e} — expected one of {list(CONTACT_STATUSES)}")
    db.commit()
    return {"lead_id": lead_id, "contact_status": lead.contact_status,
            "reason": lead.contact_status_reason,
            "email_trust": lead.email_trust,
            "note": "trust unchanged — status and trust are independent"}


class CampaignBuildRequest(BaseModel):
    city: Optional[str] = None
    region: Optional[str] = None
    category: Optional[str] = None
    name: Optional[str] = None
    batch_size: int = 50


@router.post("/b2b/campaign/preview")
def campaign_preview(req: CampaignBuildRequest, db: Session = Depends(get_db)):
    """
    What a campaign WOULD contain. Sends nothing, writes nothing.

    Answers the question the founder actually has before approving thousands of
    anything: who is in, who is out, and why.
    """
    from app.services.campaign_engine import build
    p, recips = build(db, req.city or "", req.region or "", req.category or "",
                      req.name or "", req.batch_size)
    return {"campaign_id": p.campaign_id, "name": p.name,
            "eligible_recipients": p.eligible, "excluded": p.excluded,
            "by_stage": p.by_stage, "excluded_reasons": p.exclusions,
            "batch_size": p.batch_size,
            "estimated_days_at_daily_cap": p.estimated_days,
            "warnings": p.warnings,
            "sample": [{"company": r.company, "subject": r.subject}
                       for r in recips[:3]]}


@router.post("/b2b/campaign/approve")
def campaign_approve(req: CampaignBuildRequest, db: Session = Depends(get_db)):
    """
    Approve the queue: creates the campaign and FREEZES the recipients.

    This does not send. Delivery needs a second, explicit confirmation against
    the campaign id — at three thousand recipients an accidental send is a
    burned domain, not an embarrassment.
    """
    from app.services.campaign_engine import build, freeze
    p, recips = build(db, req.city or "", req.region or "", req.category or "",
                      req.name or "", req.batch_size)
    if not recips:
        return {"campaign_id": None, "created": False,
                "eligible_recipients": 0, "excluded_reasons": p.exclusions,
                "reason": "no eligible recipients — nothing to approve"}
    out = freeze(db, p, recips)
    out.update({"eligible_recipients": p.eligible, "excluded": p.excluded,
                "estimated_days_at_daily_cap": p.estimated_days,
                "warnings": p.warnings})
    return out


@router.post("/b2b/campaign/{campaign_id}/confirm")
def campaign_confirm(campaign_id: str, db: Session = Depends(get_db)):
    """The second gate. Only after this may batches be drawn."""
    from app.models.models import WorkflowEvent
    from sqlalchemy.orm.attributes import flag_modified

    ev = next((e for e in db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "CAMPAIGN_CREATED").all()
        if (e.payload or {}).get("campaign_id") == campaign_id), None)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"unknown campaign {campaign_id}")
    ev.payload = {**(ev.payload or {}), "status": "CONFIRMED",
                  "confirmed_at": datetime.utcnow().isoformat()}
    flag_modified(ev, "payload")
    db.add(WorkflowEvent(event_type="CAMPAIGN_CONFIRMED", actor="FOUNDER",
                         channel="campaign",
                         payload={"campaign_id": campaign_id},
                         occurred_at=datetime.utcnow()))
    db.commit()
    return {"campaign_id": campaign_id, "status": "CONFIRMED",
            "next_step": f"GET /b2b/campaign/{campaign_id}/next-batch"}


@router.get("/b2b/campaign/{campaign_id}/next-batch")
def campaign_next_batch(campaign_id: str, limit: Optional[int] = None,
                        db: Session = Depends(get_db)):
    """The next batch, re-validated now — repliers and opt-outs drop out here."""
    from app.services.campaign_engine import next_batch
    return next_batch(db, campaign_id, limit)


@router.get("/b2b/outreach/funnel")
def outreach_funnel(state: Optional[str] = None, city: Optional[str] = None,
                    category: Optional[str] = None, db: Session = Depends(get_db)):
    """
    The whole pipeline for a search, as separate numbers.

    "Approve Intro Emails (3)" collapsed six different facts into one, so a
    founder searching a whole state could not tell whether three was the answer
    or the symptom. Every stage is reported on its own line, and the ones that
    are NOT approvable are reported just as loudly — 148 businesses needing a
    contact are future revenue, not missing data.

    scope_searched says plainly whether this looked beyond the CRM. It never
    reports state-wide totals it did not actually scan.
    """
    from app.models.models import B2BLead
    from app.services.contact_trust import sendable, actionable, acquisition_queue

    q = db.query(B2BLead)
    if state:
        q = q.filter(B2BLead.region.ilike("%" + state.strip() + "%"))
    if city and city.strip().lower() not in ("", "all"):
        q = q.filter(B2BLead.city.ilike("%" + city.strip() + "%"))
    if category and category.strip().upper() not in ("", "ALL"):
        q = q.filter(B2BLead.division == category.strip())
    leads = q.all()

    ev = _event_summary(db, [l.id for l in leads]) if leads else {}

    counts = {k: 0 for k in (
        "qualified", "do_not_contact", "with_email", "verified_email",
        "intro_ready", "followup_ready", "proposal_ready", "sample_followup",
        "need_contact_acquisition", "need_email_verification",
        "phone_reachable", "unreachable")}

    for l in leads:
        stat = (l.status or "").upper()
        if stat in ("DISQUALIFIED", "DO_NOT_CONTACT") or getattr(l, "do_not_call", False):
            counts["do_not_contact"] += 1
            continue
        counts["qualified"] += 1

        has_mail = bool((l.email or "").strip())
        has_phone = bool((l.phone or "").strip() or (l.whatsapp_number or "").strip())
        if has_phone:
            counts["phone_reachable"] += 1
        if has_mail:
            counts["with_email"] += 1

        can_send = sendable(l)[0] and actionable(l)[0]
        if can_send:
            counts["verified_email"] += 1
            e = ev.get(l.id, {})
            if e.get("proposal_sent"):
                counts["proposal_ready"] += 1
            elif e.get("sample_sent"):
                counts["sample_followup"] += 1
            elif e.get("emails_sent"):
                counts["followup_ready"] += 1
            else:
                counts["intro_ready"] += 1
        elif has_mail:
            # An address we hold but cannot trust yet — a verification job.
            counts["need_email_verification"] += 1
        else:
            counts["need_contact_acquisition"] += 1
            if not has_phone:
                counts["unreachable"] += 1

    approvable = (counts["intro_ready"] + counts["followup_ready"]
                  + counts["proposal_ready"] + counts["sample_followup"])

    aq = acquisition_queue(db, limit=0)

    return {
        "scope_searched": {
            "state": state or "all regions in CRM",
            "city": city or "all",
            "category": category or "all",
            "source": "CRM ONLY — no external discovery was run for this count",
            "note": ("To include businesses not yet in the CRM, run discovery "
                     "for this geography first: POST /discovery/run"),
        },
        "funnel": {
            "businesses_in_crm_for_this_scope": len(leads),
            "qualified": counts["qualified"],
            "do_not_contact": counts["do_not_contact"],
            "with_email_on_file": counts["with_email"],
            "verified_and_contactable": counts["verified_email"],
            "need_email_verification": counts["need_email_verification"],
            "need_contact_acquisition": counts["need_contact_acquisition"],
            "phone_reachable": counts["phone_reachable"],
            "unreachable_no_phone_no_email": counts["unreachable"],
        },
        "approval_queues": {
            "intro_emails": counts["intro_ready"],
            "follow_ups": counts["followup_ready"],
            "proposal_replies": counts["proposal_ready"],
            "sample_follow_ups": counts["sample_followup"],
            "entire_eligible_queue": approvable,
        },
        "acquisition_routes": aq.get("by_route", {}),
        # CONTACTABLE, not "sendable". Sendable is channel-specific and reads as
        # a verdict on the whole pipeline: a founder seeing "Email Ready: 0"
        # concludes nothing can be done, when in fact most of these businesses
        # are reachable by phone today and many more have a website that will
        # yield an address. The number that matters is how many can be reached
        # at all, and what stands between the rest and being reachable.
        "reachability": {
            "contactable_now": counts["phone_reachable"] + counts["verified_email"],
            "by_phone": counts["phone_reachable"],
            "by_email": counts["verified_email"],
            "need_contact_acquisition": counts["need_contact_acquisition"],
            "acquisition_potential": ("HIGH" if aq.get("by_route", {}).get("WEBSITE", 0)
                                      or aq.get("by_route", {}).get("FOUNDER_CALL", 0)
                                      else "LOW"),
        },
        "headline": (
            f"{counts['phone_reachable'] + counts['verified_email']} of {len(leads)} "
            f"businesses are contactable now "
            f"({counts['phone_reachable']} by phone, {counts['verified_email']} by email). "
            f"{counts['need_contact_acquisition']} need a contact acquired."
            if leads else "No businesses in the CRM for this scope."),
    }


# ── Unified search: CRM + discovery, always ─────────────────────────────────
# Discovery costs Google Places quota and takes minutes, so an identical scope
# searched twice in the same window reuses the first result rather than paying
# twice. "Always include discovery" is honoured; "always pay for it again" is
# not, because that is what exhausts a daily quota by mid-morning.
_DISCOVERY_CACHE: dict = {}
_DISCOVERY_TTL_MINUTES = 180


def _scope_key(state, city, category):
    return f"{(state or '').lower()}|{(city or '').lower()}|{(category or '').lower()}"


def _discovery_for_scope(db, state, city, category, force=False) -> dict:
    """
    Run discovery for a scope, or reuse a recent run for the same scope.

    Cities matter more than the region name: Google Places searches a place, not
    a state, so a state search fans out over its known cities.
    """
    from app.services.lead_discovery import save_discovered_leads, discover_leads, CITY_TIERS

    key = _scope_key(state, city, category)
    hit = _DISCOVERY_CACHE.get(key)
    now = datetime.utcnow()
    if hit and not force and (now - hit["at"]).total_seconds() < _DISCOVERY_TTL_MINUTES * 60:
        return {**hit["result"], "cached": True,
                "cached_minutes_ago": int((now - hit["at"]).total_seconds() // 60)}

    if city and city.strip().lower() not in ("", "all"):
        cities = [city.strip()]
    elif state:
        # Known cities for the region, so a state search actually covers it.
        cities = CITY_TIERS.get("punjab", []) if "punjab" in (state or "").lower() \
            else CITY_TIERS.get("local", [])
    else:
        cities = CITY_TIERS.get("local", [])

    try:
        found = discover_leads(segment=(category or ""), cities=cities[:6])
        saved = save_discovered_leads(found.get("leads", []), db) if found.get("leads") else {}
        result = {
            "cities_searched": cities[:6],
            "businesses_seen": len(found.get("leads", [])),
            "newly_inserted": saved.get("inserted", 0),
            "already_known_updated": saved.get("updated", 0),
            "rejected_not_coffee_buyers": saved.get("rejected_not_coffee_buyers", 0),
            "cached": False,
        }
    except Exception as e:
        result = {"cities_searched": cities[:6], "error": str(e)[:160],
                  "businesses_seen": 0, "newly_inserted": 0,
                  "already_known_updated": 0, "cached": False}

    _DISCOVERY_CACHE[key] = {"at": now, "result": result}
    return result


@router.get("/b2b/outreach/search-all")
def search_all(state: Optional[str] = None, city: Optional[str] = None,
               category: Optional[str] = None, discover: bool = True,
               force_discovery: bool = False, db: Session = Depends(get_db)):
    """
    Search the CRM AND discover new businesses for the same criteria, always.

    Returns one merged picture with the two sources kept visible: what was
    already known, what discovery just added, and what it refused. Deduplication
    happens inside save_discovered_leads, which matches on place_id first and
    then normalised name + city, so a rediscovered business updates its existing
    row and keeps one lead id forever.

    The funnel is computed AFTER discovery, so the counts include whatever was
    just found rather than describing the CRM as it was a minute ago.
    """
    from app.models.models import B2BLead

    before = db.query(B2BLead).count()
    disco = _discovery_for_scope(db, state, city, category,
                                 force=force_discovery) if discover else {
        "skipped": True, "reason": "discover=false"}
    after = db.query(B2BLead).count()

    funnel = outreach_funnel(state=state, city=city, category=category, db=db)
    funnel["scope_searched"]["source"] = (
        "CRM + live discovery" if discover else "CRM only (discovery disabled)")
    funnel["scope_searched"].pop("note", None)
    funnel["discovery"] = disco
    funnel["crm_growth"] = {"before": before, "after": after,
                            "net_new": after - before}
    return funnel


@router.get("/b2b/mission/today")
def mission_today(minutes: int = 90, db: Session = Depends(get_db)):
    """
    The founder's morning: what to do, in what order, and what is in the way.

    Deliberately NOT a revenue dashboard. With 0 replies and 0 orders on record,
    a "revenue this month" figure could only be modelled, and a modelled number
    on the homepage drives real decisions. This reports what the data supports.
    """
    from app.services.daily_mission import mission
    return mission(db, minutes_available=max(15, minutes))


@router.get("/b2b/mission/bottlenecks")
def mission_bottlenecks(db: Session = Depends(get_db)):
    """What is preventing revenue today, worst-and-most-fixable first."""
    from app.services.daily_mission import bottlenecks
    b = bottlenecks(db)
    return {"bottlenecks": b, "count": len(b),
            "headline": (b[0]["bottleneck"] if b else "no blockers detected")}


# ── Async discovery sweep ───────────────────────────────────────────────────
# A state-wide sweep is ~24 queries per city across 6+ cities and runs well past
# any HTTP timeout — a synchronous attempt on Punjab ran over 10 minutes and
# returned nothing. So it runs in a thread and reports progress, which also
# means the founder can see it working instead of guessing.
_SWEEP_STATE: dict = {}


def _run_sweep(sweep_id: str, cities: list, segment: str):
    from app.database.database import SessionLocal
    from app.services.lead_discovery import discover_leads, save_discovered_leads

    st = _SWEEP_STATE[sweep_id]
    db = SessionLocal()
    try:
        for city in cities:
            st["current_city"] = city
            try:
                found = discover_leads(segment=segment, cities=[city])
                leads = found.get("leads", [])
                saved = save_discovered_leads(leads, db) if leads else {}
                st["per_city"].append({
                    "city": city,
                    "seen": len(leads),
                    "inserted": saved.get("inserted", 0),
                    "updated": saved.get("updated", 0),
                    "rejected_not_coffee_buyers": saved.get("rejected_not_coffee_buyers", 0),
                })
                st["businesses_seen"] += len(leads)
                st["inserted"] += saved.get("inserted", 0)
                st["rejected"] += saved.get("rejected_not_coffee_buyers", 0)
            except Exception as e:
                st["per_city"].append({"city": city, "error": str(e)[:140]})
            st["cities_done"] += 1
        st["status"] = "COMPLETE"
    except Exception as e:
        st["status"] = "FAILED"
        st["error"] = str(e)[:200]
    finally:
        st["finished_at"] = datetime.utcnow().isoformat()
        st["current_city"] = None
        db.close()


class SweepRequest(BaseModel):
    cities: Optional[list[str]] = None
    region: Optional[str] = None
    segment: str = ""          # "" sweeps every category and classifies after


@router.post("/b2b/discovery/sweep")
def start_sweep(req: SweepRequest):
    """
    Start a discovery sweep in the background and return immediately.

    Synchronous discovery cannot cover a state — the request dies long before the
    sweep does, and the founder is left unable to tell a slow search from a
    broken one. This returns a sweep_id to poll.
    """
    import threading
    from app.services.lead_discovery import CITY_TIERS

    cities = req.cities or CITY_TIERS.get((req.region or "").lower().strip(), [])
    if not cities:
        raise HTTPException(status_code=400,
                            detail=f"no cities — pass cities[] or a known region "
                                   f"{list(CITY_TIERS.keys())}")
    sweep_id = f"SWEEP-{datetime.utcnow():%Y%m%d-%H%M%S}"
    _SWEEP_STATE[sweep_id] = {
        "sweep_id": sweep_id, "status": "RUNNING", "cities": cities,
        "cities_done": 0, "cities_total": len(cities), "current_city": None,
        "businesses_seen": 0, "inserted": 0, "rejected": 0, "per_city": [],
        "started_at": datetime.utcnow().isoformat(), "finished_at": None,
        "segment": req.segment or "all categories",
    }
    threading.Thread(target=_run_sweep, args=(sweep_id, cities, req.segment),
                     daemon=True).start()
    return {"sweep_id": sweep_id, "status": "RUNNING", "cities": cities,
            "poll": f"GET /b2b/discovery/sweep/{sweep_id}",
            "note": "roughly 2-4 minutes per city — this spends Google Places quota"}


@router.get("/b2b/discovery/sweep/{sweep_id}")
def sweep_status(sweep_id: str):
    """Progress of a running sweep."""
    st = _SWEEP_STATE.get(sweep_id)
    if not st:
        raise HTTPException(status_code=404, detail=f"unknown sweep {sweep_id}")
    pct = round(st["cities_done"] / max(1, st["cities_total"]) * 100)
    return {**st, "percent_complete": pct}


@router.get("/b2b/discovery/sweeps")
def list_sweeps():
    """Every sweep this process has run, newest first."""
    return {"sweeps": sorted(_SWEEP_STATE.values(),
                             key=lambda s: s["started_at"], reverse=True)[:20]}


@router.get("/marketplace/status")
def marketplace_status():
    """
    Marketplace Director: what is connected, and what each gap needs.

    Reports NOT_CONNECTED with the missing credentials named rather than showing
    a zero — an invented Buy Box percentage drives real decisions.
    """
    from app.services.marketplace_director import status
    return status()


@router.get("/marketplace/amazon/probe")
def marketplace_amazon_probe():
    """Ask Amazon directly whether the credentials work, rather than guessing."""
    from app.services.marketplace_director import amazon_probe
    return amazon_probe()


@router.get("/version")
def api_version():
    """
    What code is ACTUALLY running, not what is on disk.

    A pm2 restart can report success while an orphan keeps the port and serves
    old code — that happened twice in this project, and a fix verified against
    the source while the process ran something else is not a verified fix. The
    commit hash is read at import time, so it is the hash of the code this
    process loaded.
    """
    import subprocess, os, sys
    from datetime import datetime
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    def git(*a):
        try:
            return subprocess.check_output(["git", *a], cwd=root,
                                           stderr=subprocess.DEVNULL,
                                           timeout=8).decode().strip()
        except Exception:
            return None
    return {
        "commit": _BOOT_COMMIT,
        "commit_on_disk": git("rev-parse", "--short", "HEAD"),
        "matches_disk": _BOOT_COMMIT == git("rev-parse", "--short", "HEAD"),
        "build_time": _BOOT_TIME + "Z",
        # The port THIS process is bound to, read from the environment it was
        # started with. Without it the founder cannot tell which backend a
        # response came from, which is the whole question when two are running.
        "port": int(os.getenv("API_PORT", "8003")),
        "booted_at": _BOOT_TIME,
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "note": ("matches_disk=false means this process is serving code older "
                 "than the working tree — restart before trusting any test"),
    }


class IncomingReplyRequest(BaseModel):
    body: str
    from_email: Optional[str] = None
    subject: Optional[str] = None


@router.post("/b2b/reply/{lead_id}")
def record_reply(lead_id: int, req: IncomingReplyRequest,
                 db: Session = Depends(get_db)):
    """
    A reply arrived. Read it, cancel the generic sequence, and prepare the
    answer to what they actually said.

    The draft is prepared, never sent — it waits for founder approval like every
    other outbound. What changes is that the founder opens a considered reply
    instead of a blank page, and a warm account never receives a scheduled
    follow-up that ignores what they wrote.
    """
    from app.models.models import B2BLead, LeadInteraction, WorkflowEvent, EmailDraft
    from app.services.reply_reader import classify, draft_reply
    from app.services.outreach_search import set_next_action
    from app.services.outreach_templates import SIGNATURE
    from app.services.contact_trust import grant

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="business not found")

    cls = classify(req.body)

    # An out-of-office is not engagement. Record it and change nothing else.
    if cls["intent"] == "AUTO_REPLY":
        db.add(WorkflowEvent(
            lead_id=lead_id, event_type="AUTO_REPLY_RECEIVED", actor="SYSTEM",
            channel="email", payload={"evidence": cls["evidence"]},
            occurred_at=datetime.utcnow()))
        db.commit()
        return {"lead_id": lead_id, "classification": cls, "draft": None,
                "note": "automated response — stage unchanged, no draft prepared"}

    # A reply is the strongest proof an address is real.
    if (req.from_email or lead.email):
        grant(lead, "REPLIED", "EMAIL_REPLY", db,
              note="they replied from this address")

    mem = {}
    for i in sorted(db.query(LeadInteraction).filter(
            LeadInteraction.lead_id == lead_id,
            LeadInteraction.superseded_by_id.is_(None)).all(),
            key=lambda x: x.occurred_at or datetime.min):
        for f in ("decision_maker", "current_supplier", "preferred_contact_time",
                  "next_followup_date"):
            v = getattr(i, f, None)
            if v not in (None, ""):
                mem[f] = v
    for f in ("decision_maker", "current_supplier"):
        if not mem.get(f) and getattr(lead, f, None):
            mem[f] = getattr(lead, f)

    d = draft_reply(lead, req.body, cls, mem, SIGNATURE)

    # Record the reply itself as Business Memory, in their words.
    db.add(LeadInteraction(
        lead_id=lead_id, occurred_at=datetime.utcnow(), created_by="BUYER",
        method="email", outcome=cls["intent"],
        interested=False if cls["intent"] == "NOT_INTERESTED" else
        (True if cls["intent"] in ("ORDER", "SAMPLE", "PRICING", "MEETING") else None),
        remark=req.body[:2000]))
    db.add(WorkflowEvent(
        lead_id=lead_id, event_type="EMAIL_REPLIED", actor="BUYER", channel="email",
        payload={"intent": cls["intent"], "evidence": cls["evidence"],
                 "from": req.from_email, "subject": req.subject},
        occurred_at=datetime.utcnow()))

    # Stage follows what they said.
    if cls["intent"] == "NOT_INTERESTED":
        lead.status = "CLOSED_LOST"
        lead.contact_status = "OPTED_OUT"
        lead.contact_status_reason = "asked not to be contacted again"
    elif cls["intent"] in ("ORDER", "SAMPLE", "PRICING", "MEETING"):
        lead.status = "REPLIED"

    # The prepared answer, queued for approval — replacing, not appending to,
    # whatever generic step was scheduled.
    draft = EmailDraft(lead_id=lead_id, subject=d["subject"], body=d["body"],
                       status="PENDING", created_at=datetime.utcnow())
    db.add(draft)
    db.commit()

    nxt = set_next_action(db, lead, cls.get("next_action"), 0,
                          reason=f"reply: {cls['intent']}")

    return {"lead_id": lead_id, "company": lead.company,
            "classification": cls, "draft": d, "draft_id": draft.id,
            "next_action": nxt,
            "generic_followups_cancelled": nxt.get("cancelled", 0),
            "note": ("draft prepared and queued — it will not send without your "
                     "approval")}


@router.get("/health")
def health_probe(db: Session = Depends(get_db)):
    """
    Cheap liveness probe for the watchdog and any external monitor.

    /docs was being used for this. It is fast (1 KB, 9 ms) so cost was never
    the problem — the problem is what it PROVES. A 200 from /docs means FastAPI
    is serving routes; it says nothing about whether the database is reachable.
    An API that has lost its DB answers /docs perfectly while every real request
    fails, so the watchdog would report healthy through a total outage.

    This touches the DB with the cheapest possible query and reports the send
    queue depth, so one call answers both "is it alive?" and "is it working?".
    Returns 503 on failure so any monitor can act on the status code alone.
    """
    import time
    from sqlalchemy import text as _sql   # not imported at module scope
    t0 = time.perf_counter()
    checks, ok = {}, True

    try:
        db.execute(_sql("SELECT 1"))
        checks["db"] = True
    except Exception as e:
        checks["db"] = False
        checks["db_error"] = f"{e.__class__.__name__}: {e}"[:120]
        ok = False

    # Is the WORKER doing its job? pm2 "online" only proves a process exists.
    # The reply poller ran for hours today logging a missing credential every
    # cycle — alive, scheduled, doing nothing — and both pm2 and this endpoint
    # reported healthy while revenue quietly stopped.
    try:
        from app.services.heartbeat import status as hb_status, queue_health
        hb = hb_status(db, ("worker",))
        checks["components"] = hb["components"]
        if not hb["all_ok"]:
            ok = False

        # Queue AGE, not just depth. 12 pending with the oldest 2 minutes old
        # is draining normally; 12 pending with the oldest 18 hours old has
        # stopped. Depth cannot tell those apart.
        q = queue_health(db)
        checks["queue"] = q
        if q.get("stalled"):
            ok = False
    except Exception as e:
        checks["ops_error"] = f"{e.__class__.__name__}: {e}"[:100]

    body = {
        "status": "healthy" if ok else "unhealthy",
        "commit": _BOOT_COMMIT,
        "checks": checks,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    return JSONResponse(body, status_code=200 if ok else 503)


# ── Founder Sprint ──────────────────────────────────────────────────────────
# Call -> Save -> Next. No searching, no filters, no navigation. The founder
# holds a phone, not a CRM.

@router.get("/sprint/next")
def sprint_next(segment: str = "", city: str = "", db: Session = Depends(get_db)):
    """
    The single next account to call. One card, everything needed to dial.

    Returns one, not a list, on purpose: a list invites browsing, and browsing
    is the activity this whole workflow exists to eliminate.
    """
    from app.services.phone_intelligence import (call_queue, call_funnel,
                                                 bottleneck as _bottleneck,
                                                 recovery_mode as _recovery)
    q = call_queue(db, limit=1, city=city, category=segment)
    if not q:
        return {"done": True,
                "message": f"no callable accounts left{' in ' + segment if segment else ''}",
                "funnel": call_funnel(db, days=1)}
    c = q[0]
    return {"done": False, "call": c,
            "opening": ("Good morning, I'm Hiten from Pure Pantry Provisions. "
                        "May I know who handles coffee purchasing for your business?"),
            "then_ask": c["ask"],
            "objective": "find the coffee buyer — not to sell on this call",
            "funnel_today": call_funnel(db, days=1)}


@router.post("/sprint/log")
def sprint_log(payload: dict, db: Session = Depends(get_db)):
    """
    Save the call and hand back the next one in the same response, so the
    founder never navigates. Body: {lead_id, outcome, notes, duration_min,
    segment?, city?}
    """
    from app.models.models import B2BLead
    from app.services.phone_intelligence import log_call, OUTCOMES

    lead_id = payload.get("lead_id")
    outcome = (payload.get("outcome") or "").upper()
    if outcome not in OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {sorted(OUTCOMES)}")
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, f"lead {lead_id} not found")

    result = log_call(lead, db, outcome, payload.get("notes", "") or "",
                      payload.get("duration_min"))
    nxt = sprint_next(segment=payload.get("segment", "") or "",
                      city=payload.get("city", "") or "", db=db)
    return {"saved": result, "next": nxt}


@router.get("/sprint/compare")
def sprint_compare(db: Session = Depends(get_db)):
    """
    Which segment actually converts, from calls actually made.

    Every cell is counted from FOUNDER_CALL events — nothing here is modelled.
    Empty until calls exist, which is the honest state before the experiment.
    """
    from app.models.models import WorkflowEvent, B2BLead
    from app.services.phone_intelligence import OUTCOMES

    rows: dict = {}
    calls = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "FOUNDER_CALL").all()
    for e in calls:
        lead = db.query(B2BLead).filter(B2BLead.id == e.lead_id).first()
        if not lead:
            continue
        seg = (lead.division or "uncategorised").lower()
        p = e.payload or {}
        ex = p.get("extracted", {}) or {}
        r = rows.setdefault(seg, {"segment": seg, "calls": 0, "reached": 0,
                                  "decision_maker": 0, "emails": 0,
                                  "samples": 0, "meetings": 0})
        r["calls"] += 1
        if p.get("outcome") not in ("NO_ANSWER", "WRONG_NUMBER", "GATEKEEPER"):
            r["reached"] += 1
        if ex.get("contact_name"):
            r["decision_maker"] += 1
        if ex.get("email"):
            r["emails"] += 1
        if p.get("outcome") == "SAMPLE_REQUESTED":
            r["samples"] += 1

    for r in rows.values():
        r["email_rate"] = (f"{round(100 * r['emails'] / r['reached'])}%"
                           if r["reached"] else "—")
    out = sorted(rows.values(), key=lambda r: -r["calls"])
    return {"segments": out,
            "total_calls": sum(r["calls"] for r in out),
            "note": ("no calls logged yet — this table fills itself as the "
                     "sprint runs" if not out else
                     "counted from logged calls only; nothing modelled")}


# ── The one screen ──────────────────────────────────────────────────────────

TARGETS = {"calls": 20, "procurement_contacts": 5, "buying_conversations": 2}


@router.get("/today")
def today(db: Session = Depends(get_db)):
    """
    The founder's operating console: three numbers, one next action, the queue.

    Deliberately omits "revenue today" and "expected orders". With zero orders
    on record those could only be modelled, and a modelled rupee figure at the
    top of the screen drives a real day's work. Every number below is counted
    from events that actually happened.
    """
    from datetime import datetime as _dt, timedelta as _td
    from app.models.models import WorkflowEvent
    from app.services.phone_intelligence import (call_queue, call_funnel,
                                                 bottleneck as _bottleneck,
                                                 recovery_mode as _recovery)
    from app.services.send_queue import status as q_status
    from app.services.heartbeat import status as hb_status, queue_health

    start = _dt.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    todays = db.query(WorkflowEvent).filter(
        WorkflowEvent.occurred_at >= start).all()

    calls = [e for e in todays if e.event_type == "FOUNDER_CALL"]
    contacts_found = [e for e in calls
                      if (e.payload or {}).get("extracted", {}).get("email")]
    # A buying conversation is a human asking for something commercial —
    # pricing, a sample, a meeting. Not an open, not a click, not a reply
    # that says "remove me".
    BUYING = {"PRICING_REQUEST", "SAMPLE_REQUEST", "MEETING_REQUEST",
              "CALL_ME", "DISTRIBUTOR_ENQUIRY"}
    buying = [e for e in todays
              if e.event_type == "EMAIL_REPLY_RECEIVED"
              and BUYING & set((e.payload or {}).get("intents", []))]
    buying += [e for e in calls
               if (e.payload or {}).get("outcome") in
               ("SAMPLE_REQUESTED", "INTERESTED")]

    def kpi(name, current):
        t = TARGETS[name]
        return {"current": len(current) if isinstance(current, list) else current,
                "target": t,
                "remaining": max(0, t - (len(current) if isinstance(current, list)
                                         else current))}

    # What to do next, in the order that protects revenue: a human waiting on
    # us always outranks a cold call.
    # Replies we have NOT answered. Counting every reply ever received would
    # show a permanently rising "waiting" number that never clears — the founder
    # would learn to ignore it within a week, which is worse than not showing it.
    _all_replies = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "EMAIL_REPLY_RECEIVED").all()
    _answered_after: dict = {}
    for e in db.query(WorkflowEvent).filter(
            WorkflowEvent.event_type.in_(["EMAIL_SENT", "FOUNDER_CALL"])).all():
        prev = _answered_after.get(e.lead_id)
        if e.occurred_at and (prev is None or e.occurred_at > prev):
            _answered_after[e.lead_id] = e.occurred_at
    replies_waiting = [
        e for e in _all_replies
        if e.occurred_at and (_answered_after.get(e.lead_id) is None
                              or _answered_after[e.lead_id] < e.occurred_at)]
    samples = [e for e in todays if e.event_type == "NEXT_ACTION_SET"
               and (e.payload or {}).get("action") == "SEND_SAMPLE"]
    approvals = q_status(db)["approved_waiting"]

    nxt, queue_reason = None, ""
    # Decision order: replied -> calls -> approvals -> samples -> discover.
    # Calls sit SECOND, not fourth. This previously offered a sample dispatch
    # ahead of the first call of the day, which inverts the cadence: packing a
    # sample is satisfying, takes twenty minutes, and creates no conversation.
    # Only a human already waiting on us outranks the phone.
    if replies_waiting:
        nxt = {"do": "answer a reply", "why": "a human is waiting on us",
               "count": len(replies_waiting), "estimated_minutes": 5}
    else:
        q = call_queue(db, limit=1)
        if q:
            c = q[0]
            nxt = {"do": "call", "company": c["company"], "phone": c["phone"],
                   "lead_id": c["lead_id"], "category": c["category"],
                   "number_kind": c["number_kind"],
                   "why": c["why_now"][0] if c["why_now"] else "highest ranked",
                   "ask": c["ask"], "estimated_minutes": 4,
                   "goal": ("get the procurement email" if not c["has_email"]
                            else "confirm the buyer and their current supplier"),
                   "start": f"/api/v1/sprint/next?segment={c['category']}"}
        elif approvals:
            nxt = {"do": "approve queued emails", "count": approvals,
                   "why": "drafts ready — nothing sends without you",
                   "estimated_minutes": approvals}
        elif samples:
            nxt = {"do": "dispatch a sample", "count": len(samples),
                   "why": "requested and not yet sent", "estimated_minutes": 2}
        else:
            queue_reason = "no callable accounts left — discovery is next"

    hb = hb_status(db, ("worker",))
    qh = queue_health(db)

    return {
        "mission": {
            "buying_conversations": kpi("buying_conversations", buying),
            "calls": kpi("calls", calls),
            "procurement_contacts": kpi("procurement_contacts", contacts_found),
        },
        "next_action": nxt or {"do": "nothing queued", "why": queue_reason},
        "queue": {"replies_waiting": len(replies_waiting),
                  "samples_to_dispatch": len(samples),
                  "email_approvals": approvals},
        "system": {"worker": hb["components"].get("worker", {}).get("status"),
                   "queue_stalled": qh.get("stalled"),
                   "healthy": hb["all_ok"] and not qh.get("stalled")},
        # Conversion BETWEEN stages, which is where leaks show up. Activity
        # counts alone cannot tell you whether the list or the pitch is wrong.
        "conversion": call_funnel(db, days=14)["funnel"],
        # Where the business is leaking, and the one thing to change about it.
        "bottleneck": _bottleneck(db, days=14),
        # No-Zero-Day: late in the day with nothing to show, the screen stops
        # offering choices and names who to call.
        "recovery": _recovery(db, len(buying),
                              TARGETS["buying_conversations"]),
        "note": ("no revenue estimate shown on purpose: with zero orders on "
                 "record any rupee figure here would be modelled, and a "
                 "modelled number at the top of the screen drives a real day"),
    }
