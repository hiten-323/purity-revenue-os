"""V3 acceptance tests — real API, real DB, cleaned up after."""
import sys, httpx
import os
_EXPECT_TREE = os.getenv(
    "V3_TREE",
    r"C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session\backend")
sys.path.insert(0, _EXPECT_TREE)

# Python caches modules: if another suite in this pytest session already
# imported `app` from a different tree, the sys.path.insert above is silently
# ignored and this file binds to THAT tree while still talking to the API
# serving this one — it creates a lead through the API and cannot find it
# locally. Verified: `first is second` is True across a path change.
# Refuse to run against the wrong tree rather than report a misleading failure.
import app.database.database as _dbmod
if not os.path.abspath(_dbmod.__file__).startswith(os.path.abspath(_EXPECT_TREE)):
    import pytest
    # Undo the sys.path.insert before skipping. Skipping protects THIS file,
    # but the insert leaks: jules_session stays at sys.path[0] for the rest of
    # the session, so every module imported for the first time afterwards binds
    # to the legacy tree instead of this one. That is how
    # test_phone_provenance_guard came to import contact_enricher from
    # jules_session and fail on a symbol that exists only here — a suite that
    # does not even run was silently repointing the ones that do.
    try:
        sys.path.remove(_EXPECT_TREE)
    except ValueError:
        pass
    pytest.skip(
        f"app already loaded from {os.path.dirname(_dbmod.__file__)}, not the "
        f"tree this suite targets. Run it in its own process: "
        f"python tests/test_outreach_v3_acceptance.py",
        allow_module_level=True)
from app.database.database import SessionLocal
from app.models.models import B2BLead, ActionQueue, LeadInteraction, WorkflowEvent

# Integration suite against a LIVE server. sys.path above deliberately points
# at the deployed tree, because this file's database MUST be the same one the
# API is serving — repointing the path at this repo while the API still runs
# jules_session makes it create a lead through the API and then fail to find it
# locally. Repoint both together at deploy time, never one alone.
#
# Port is overridable: the API has run on 8001 and 8003, and a hardcoded port
# turns "server on another port" into a hard failure.
import os
B = os.getenv("API_BASE", "http://127.0.0.1:8003/api/v1")

# Module-level code runs at IMPORT, so any failure here aborts pytest collection
# for the whole directory — one unreachable server took down every other suite.
# Skipping is the honest outcome: not run, not passed.
try:
    import httpx as _h
    _h.get(f"{B}/health", timeout=5).raise_for_status()
except Exception as _e:
    import pytest
    pytest.skip(f"live API not reachable at {B} ({_e.__class__.__name__}) — "
                "integration suite skipped", allow_module_level=True)

db = SessionLocal()
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if detail else ""))


print("V3 ACCEPTANCE TESTS\n")

# ---- TEST A: email search excludes no-email businesses -------------------
r = httpx.get(f"{B}/b2b/outreach/search",
              params={"outreach_method": "INTRO_EMAIL"}, timeout=120).json()
no_mail = [x for x in r["results"] if not x["email"]]
check("A1 INTRO_EMAIL returns only businesses with an email",
      len(no_mail) == 0, f"{r['matched']} matched, {len(no_mail)} without email")
unver = [x for x in r["results"] if not x["email_verified"]]
check("A2 unverified addresses are excluded from email-ready",
      len(unver) == 0, f"{len(unver)} unverified in results")

# ---- TEST B: phone-only ---------------------------------------------------
p = httpx.get(f"{B}/b2b/outreach/phone-program", timeout=120).json()
check("B1 phone-only program returns eligible opportunities",
      p["phone_only_opportunities"] > 0,
      f"{p['phone_only_opportunities']} eligible, {p['priority_today']} ranked today")
bad = [c for c in p["power_hour"] if not c["phone"]]
check("B2 no Power Hour card lacks a phone number", len(bad) == 0)
s = httpx.get(f"{B}/b2b/outreach/search",
              params={"outreach_method": "PHONE_ONLY"}, timeout=120).json()
with_mail = [x for x in s["results"] if x["email_verified"]]
check("B3 phone-only excludes anything with a usable email",
      len(with_mail) == 0, f"{s['matched']} phone-only")

# fabrication guard: nothing gained an email
before_mail = db.query(B2BLead).filter(B2BLead.email != "",
                                       B2BLead.email.isnot(None)).count()

# ---- TEST C: phone -> email becomes a FOLLOW-UP, not an intro -------------
LID = p["power_hour"][0]["lead_id"]
company = p["power_hour"][0]["company"]
o = httpx.post(f"{B}/b2b/outreach/call-outcome/{LID}", timeout=120, json={
    "outcome": "SEND_DETAILS", "email": "buyer@testco-v3.example",
    "decision_maker": "Rajesh Sharma",
    "remark": "Interested. Send details."}).json()
check("C1 SEND_DETAILS creates a FOLLOWUP_EMAIL action",
      o["next_action"] == "FOLLOWUP_EMAIL", f"got {o['next_action']}")
check("C2 email captured with call provenance",
      "email" in o["promoted"], f"promoted={o['promoted']}")
db.expire_all()
lead = db.query(B2BLead).get(LID)
check("C3 provenance recorded as FOUNDER_CALL_PROVIDED",
      lead.email_verification_status == "FOUNDER_CALL_PROVIDED",
      lead.email_verification_status or "")
sr = httpx.get(f"{B}/b2b/outreach/search",
               params={"outreach_method": "INTRO_EMAIL"}, timeout=120).json()
still_intro = [x for x in sr["results"] if x["lead_id"] == LID]
check("C4 the business no longer qualifies for an INTRO email",
      len(still_intro) == 0)

# ---- TEST D: phone -> WhatsApp ------------------------------------------
o2 = httpx.post(f"{B}/b2b/outreach/call-outcome/{LID}", timeout=120, json={
    "outcome": "SEND_WHATSAPP", "remark": "Send catalogue on WhatsApp."}).json()
check("D1 SEND_WHATSAPP switches the channel to whatsapp",
      o2["channel"] == "whatsapp" and o2["next_action"] == "WHATSAPP",
      f"{o2['next_action']}/{o2['channel']}")
check("D2 the stale email action was cancelled",
      o2["next_action_state"]["cancelled"] >= 1,
      f"cancelled {o2['next_action_state']['cancelled']}")

# ---- TEST E: no answer is not rejection ---------------------------------
o3 = httpx.post(f"{B}/b2b/outreach/call-outcome/{LID}", timeout=120, json={
    "outcome": "NO_ANSWER"}).json()
db.expire_all()
lead = db.query(B2BLead).get(LID)
check("E1 NO_ANSWER schedules a retry, does not close the lead",
      o3["next_action"] == "FOUNDER_CALL" and lead.status != "CLOSED_LOST",
      f"action={o3['next_action']} status={lead.status}")
check("E2 NO_ANSWER is not recorded as interested",
      db.query(LeadInteraction).filter(
          LeadInteraction.lead_id == LID,
          LeadInteraction.outcome == "NO_ANSWER").first().interested is None)

# ---- TEST F: never cancel without replacing (the regression) ------------
ok_all = True
for k in ("GATEKEEPER", "INTERESTED", "CALL_LATER", "EXISTING_SUPPLIER",
          "SAMPLE_REQUESTED", "MEETING_REQUESTED", "PRICE_OBJECTION",
          "WRONG_PERSON", "BUSY", "SEND_PRICING"):
    rr = httpx.post(f"{B}/b2b/outreach/call-outcome/{LID}", timeout=120,
                    json={"outcome": k}).json()
    st = rr["next_action_state"]
    if not (st["ok"] and st["active_now"] == 1):
        ok_all = False
        print(f"        {k}: active={st['active_now']} ok={st['ok']}")
check("F1 every non-terminal outcome leaves EXACTLY ONE next action", ok_all)

# terminal outcomes correctly leave zero
rt = httpx.post(f"{B}/b2b/outreach/call-outcome/{LID}", timeout=120,
                json={"outcome": "NOT_INTERESTED"}).json()
check("F2 a terminal outcome leaves zero next actions and says so",
      rt["next_action_state"]["active_now"] == 0 and rt["terminal"] is True)
db.expire_all()
check("F3 NOT_INTERESTED closes the opportunity",
      db.query(B2BLead).get(LID).status == "CLOSED_LOST")

# ---- TEST G: suppression -------------------------------------------------
rd = httpx.post(f"{B}/b2b/outreach/call-outcome/{LID}", timeout=120,
                json={"outcome": "DO_NOT_CONTACT"}).json()
db.expire_all()
lead = db.query(B2BLead).get(LID)
check("G1 DO_NOT_CONTACT suppresses the business",
      lead.do_not_call is True and rd["next_action_state"]["active_now"] == 0)
sr2 = httpx.get(f"{B}/b2b/outreach/search",
                params={"outreach_method": "ALL"}, timeout=120).json()
check("G2 a suppressed business is excluded from every outreach method",
      not any(x["lead_id"] == LID for x in sr2["results"]))

# ---- no fabrication -----------------------------------------------------
after_mail = db.query(B2BLead).filter(B2BLead.email != "",
                                      B2BLead.email.isnot(None)).count()
check("H1 no email was invented (only the one the founder supplied)",
      after_mail == before_mail + 1, f"{before_mail} -> {after_mail}")

# ---- INSUFFICIENT DATA guard -------------------------------------------
from app.services.outreach_search import rate
check("H2 a rate on a tiny sample reports INSUFFICIENT_DATA",
      rate(1, 3, "reply rate")["status"] == "INSUFFICIENT_DATA"
      and rate(1, 3, "x")["rate"] is None)

# ---- TEST I: BUYING INTENT OVERRIDES MODELLED VALUE ------------------------
# Insert Lead A (₹2,00,000, replied) and Lead B (₹15,00,000, no reply/signals)
lead_a = B2BLead(
    company="Lead A (Intent Dominance Test)",
    city="Bathinda",
    state="Punjab",
    email="lead_a_intent@test.com",
    email_verification_status="VALID",
    phone="9999999991",
    status="DISCOVERED",
    estimated_value=200000.0,
    coffee_buying_score=50,
)
lead_b = B2BLead(
    company="Lead B (Intent Dominance Test)",
    city="Bathinda",
    state="Punjab",
    email="lead_b_value@test.com",
    email_verification_status="VALID",
    phone="9999999992",
    status="DISCOVERED",
    estimated_value=1500000.0,
    coffee_buying_score=50,
)
db.add(lead_a)
db.add(lead_b)
db.commit()

# Add reply event for Lead A
evt_a = WorkflowEvent(lead_id=lead_a.id, event_type="REPLIED")
db.add(evt_a)
db.commit()

# Call discovery run API
run_res = httpx.post(f"{B}/discovery/run", json={
    "segment": "",
    "cities": [],
    "radius": "punjab",
    "save": False,
    "state": "Punjab",
    "method": "EMAIL",
    "search_mode": "existing",
    "sort_by": "best_conversion"
}, timeout=120).json()

# Find their indexes in the returned priority ordered list
leads_returned = []
# Collect all leads across all returned buckets to search
for bucket_leads in run_res.get("buckets", {}).values():
    leads_returned.extend(bucket_leads)

# Remove duplicates while preserving order
seen_leads = {}
leads_unique = []
for l in leads_returned:
    if l["id"] not in seen_leads:
        seen_leads[l["id"]] = True
        leads_unique.append(l)

# Sort unique leads by priority score descending
leads_unique_sorted = sorted(leads_unique, key=lambda x: x.get("priority_score", 0.0), reverse=True)

index_a = next((i for i, x in enumerate(leads_unique_sorted) if x["company"] == "Lead A (Intent Dominance Test)"), -1)
index_b = next((i for i, x in enumerate(leads_unique_sorted) if x["company"] == "Lead B (Intent Dominance Test)"), -1)

# Also assert their scores directly
lead_a_item = next((x for x in leads_unique_sorted if x["company"] == "Lead A (Intent Dominance Test)"), None)
lead_b_item = next((x for x in leads_unique_sorted if x["company"] == "Lead B (Intent Dominance Test)"), None)
score_a = lead_a_item["priority_score"] if lead_a_item else -1.0
score_b = lead_b_item["priority_score"] if lead_b_item else -1.0

print(f"  Lead A Index: {index_a} (Score: {score_a:.2f})")
print(f"  Lead B Index: {index_b} (Score: {score_b:.2f})")

is_ok = (index_a != -1 and index_b != -1 and index_a < index_b and score_a > score_b)
check("TEST: BUYING INTENT OVERRIDES MODELLED VALUE (Lead A ranked above Lead B)", is_ok)

# Cleanup Test I mock rows
db.delete(evt_a)
db.delete(lead_a)
db.delete(lead_b)
db.commit()

if not is_ok:
    print("Acceptance Test Failed: Buying Intent Dominance violated!")
    sys.exit(1)

# ---- cleanup -----------------------------------------------------------
db.query(LeadInteraction).filter(LeadInteraction.lead_id == LID).delete()
db.query(ActionQueue).filter(ActionQueue.lead_id == LID).delete()
db.query(WorkflowEvent).filter(
    WorkflowEvent.lead_id == LID,
    WorkflowEvent.event_type.in_(["FOUNDER_CALL", "NEXT_ACTION_SET",
                                 "CONTACT_CAPTURED"])).delete(synchronize_session=False)
lead = db.query(B2BLead).get(LID)
lead.email = ""
lead.email_verified = False
lead.email_verification_status = None
lead.do_not_call = False
lead.status = "DISCOVERED"
lead.decision_maker = None
lead.current_supplier = None
db.commit()
db.close()

print(f"\n  {sum(res)}/{len(res)} passed")
print(f"  test lead {LID} ({company}) restored to DISCOVERED, test rows removed")
