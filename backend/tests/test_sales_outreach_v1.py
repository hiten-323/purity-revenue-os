"""B2B Sales Outreach v1 — acceptance tests. Real DB, no mocks."""
import os, sys, tempfile
sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("CEREBRAS_API_KEY", "test-not-a-real-key")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.models import Base, B2BLead, WorkflowEvent, ActionQueue

fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
eng = create_engine(f"sqlite:///{path}")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()

from app.services import phone_intelligence as pi
from app.services.decision_engine import evaluate_next_action

fails, checks = [], 0
def ck(cond, label):
    global checks; checks += 1
    if not cond: fails.append(label)

def mklead(n, **kw):
    l = B2BLead(company=f"Test Co {n}", city="Abohar", phone="9876543210",
                email=kw.pop("email", ""), division="DISTRIBUTOR", **kw)
    db.add(l); db.commit(); return l

# ---- 1. canonical registry contract ----
# EXISTING_CONTRACT remains a supported canonical outcome in the registry;
# the earlier test called this a 12-item registry while actually testing only
# 12 selected outcomes. Pin the real contract explicitly so a future addition
# cannot silently change the vocabulary.
CANON = ["NO_ANSWER","CALLBACK","GATEKEEPER","WRONG_NUMBER","DECISION_MAKER_FOUND",
         "EMAIL_COLLECTED","WHATSAPP_CONSENT","CATALOGUE_REQUESTED","SAMPLE_REQUESTED",
         "PRICING_REQUESTED","INTERESTED","NOT_INTERESTED","EXISTING_CONTRACT"]
missing = [o for o in CANON if o not in pi.OUTCOMES]
ck(not missing, f"canonical outcomes missing: {missing}")
ck(set(pi.OUTCOMES) == set(CANON),
   f"registry vocabulary differs: extra={sorted(set(pi.OUTCOMES)-set(CANON))} missing={sorted(set(CANON)-set(pi.OUTCOMES))}")
print(f"1. canonical registry contract: {not missing}  ({len(pi.OUTCOMES)} outcomes)")

# ---- 2. aliases normalise, unknown still rejected ----
for raw, want in [("CALL_BACK","CALLBACK"), ("call back","CALLBACK"),
                  ("SEND_DETAILS","CATALOGUE_REQUESTED"), ("BUSY","NO_ANSWER"),
                  ("SEND_WHATSAPP","WHATSAPP_CONSENT"), ("WRONG_PERSON","GATEKEEPER"),
                  ("EXISTING_SUPPLIER","EXISTING_CONTRACT")]:
    got = pi.normalise_outcome(raw)
    ck(got == want, f"alias {raw} -> {got}, expected {want}")
try:
    pi.normalise_outcome("BANANA"); ck(False, "unknown outcome not rejected")
except ValueError: pass
print("2. aliases normalise + unknown rejected: OK")

# ---- 3. every outcome leaves exactly ONE next action decision ----
print("3. every outcome -> exactly one next action decision:")
for i, oc in enumerate(sorted(pi.OUTCOMES)):
    l = mklead(f"{i}-{oc}", email="buyer@acmedistributors.in")
    notes = {"EMAIL_COLLECTED": "email is buyer@acmedistributors.in",
             "DECISION_MAKER_FOUND": "Spoke to Rajinder Singh, purchase head",
             "CALLBACK": "call back tomorrow"}.get(oc, "spoke briefly")
    r = pi.log_call(l, db, oc, notes=notes)
    na = r["next_action"]
    sets = [e for e in db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == l.id,
        WorkflowEvent.event_type == "NEXT_ACTION_SET").all()]
    ck(na.get("action") is not None or na.get("terminal") is True,
       f"{oc}: no action/terminal decision")
    ck(len(sets) == 1, f"{oc}: {len(sets)} NEXT_ACTION_SET events, expected 1")
    print(f"   {oc:22s} -> {na.get('action')} via {na.get('decided_by')}"
          + (f"  [blocked: {na.get('blocked','')[:34]}]" if na.get("blocked") else ""))

# ---- 4. WHATSAPP_CONSENT actually unlocks the AiSensy gate ----
l = mklead("wa", email="x@acmedistributors.in")
from app.services.whatsapp_sender import consent_check
before = consent_check(l)[0]
pi.log_call(l, db, "WHATSAPP_CONSENT", notes="yes send it on whatsapp")
db.refresh(l)
after = consent_check(l)[0]
ck(before is False and after is True, f"consent gate {before} -> {after}, expected False -> True")
print(f"4. WHATSAPP_CONSENT unlocks AiSensy: consent_check {before} -> {after}; "
      f"status={l.consent_status} source={l.consent_source}")

# ---- 5. EMAIL_COLLECTED promotes trust and warns when no address parsed ----
l2 = mklead("em")
r = pi.log_call(l2, db, "EMAIL_COLLECTED", notes="send it to purchase@bigtraders.in")
db.refresh(l2)
ck(l2.email == "purchase@bigtraders.in", f"email not stored: {l2.email!r}")
ck(r["now_emailable"], "address from a call should be sendable")
l3 = mklead("em2")
r3 = pi.log_call(l3, db, "EMAIL_COLLECTED", notes="he will send it later")
ck(any("WARNING" in a for a in r3["applied"]), "no warning when address missing")
print(f"5. EMAIL_COLLECTED: stored={l2.email} sendable={r['now_emailable']}; "
      f"missing-address warns={any('WARNING' in a for a in r3['applied'])}")

# ---- 6. a phone catalogue request must NOT promote email trust ----
l4 = mklead("ct", email="info@guessed.in")
t_before = l4.email_trust
pi.log_call(l4, db, "CATALOGUE_REQUESTED", notes="send the catalogue")
db.refresh(l4)
ck(l4.email_trust == t_before,
   f"email_trust moved {t_before} -> {l4.email_trust} on a PHONE request")
print(f"6. phone catalogue request leaves email trust alone: {t_before} -> {l4.email_trust}")

# ---- 7. the engine sees the open commitment and does not schedule over it ----
l5b = mklead("cmb", email="buyer@unverified.in")
pi.log_call(l5b, db, "CATALOGUE_REQUESTED", notes="send catalogue please")
db_ = evaluate_next_action(l5b, db)
ck(db_["action"] == "FOUNDER_REVIEW", f"blocked commitment -> {db_['action']}")
ck("commitment_blocked" in db_["blockers"], f"blockers={db_['blockers']}")

l5 = mklead("cm", email="buyer@realco.in")
from app.services import trust_promoter as tp
tp.on_founder_call(l5, db, confirmed=True, notes="address confirmed on a call")
db.commit()
pi.log_call(l5, db, "CATALOGUE_REQUESTED", notes="send catalogue please")
d = evaluate_next_action(l5, db)
ck(d["action"] == "SEND_CATALOGUE",
   f"engine returned {d['action']}, expected SEND_CATALOGUE from open commitment")
ck(any("commitment" in a for a in d["audit"]), "commitment not in audit trail")
print(f"7. commitment gate — blocked: {db_['action']} | sendable: {d['action']}")
ck(d["action"] != "SEND", "commitment lost to the sequence")
print(f"   outranks the sequence: {d['reason'][:48]}")

# ---- 8. both doors agree on the same call ----
from app.services.outreach_search import apply_call_outcome
la = mklead("doorA", email="b@twodoors.in")
lb = mklead("doorB", email="b@twodoors.in")
ra = pi.log_call(la, db, "SAMPLE_REQUESTED", notes="send a sample")
rb = apply_call_outcome(db, lb, "SAMPLE_REQUESTED", {"remark": "send a sample"})
ck(ra["next_action"]["action"] == rb["next_action"],
   f"doors disagree: phone={ra['next_action']['action']} search={rb['next_action']}")
print(f"8. both call-logging doors agree: phone={ra['next_action']['action']} "
      f"search={rb['next_action']}")

# ---- 9. founder-only actions flagged ----
l6 = mklead("pr", email="b@pricing.in")
r6 = pi.log_call(l6, db, "PRICING_REQUESTED", notes="what is your distributor price")
ck(r6["next_action"]["founder_only"], "PRICING_REQUESTED not founder-only")
ck(r6["next_action"]["action"] == "FOUNDER_PRICING", r6["next_action"]["action"])
print(f"9. pricing is founder-only: {r6['next_action']['action']} "
      f"founder_only={r6['next_action']['founder_only']}")

print("\n" + "=" * 62)
if fails:
    print(f"FAILED {len(fails)}/{checks} checks:")
    for f in fails: print("  -", f)
else:
    print(f"ALL {checks} CHECKS PASSED")
db.close(); eng.dispose()
try: os.unlink(path)
except OSError: pass
sys.exit(1 if fails else 0)
