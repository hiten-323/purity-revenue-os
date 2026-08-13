"""
Account-level frequency cap: every channel consumes the same slot.

The cap exists because 26 More Supermarket branches share one inbox, and
emailing all of them is one relationship contacted 26 times in a day. It used
to count EMAIL_SENT only, so a WhatsApp touch cost an account nothing and the
same 26 branches could be messaged through the other door without the guard
firing once.
"""
import os, sys, tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath("."))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.models import Base, B2BLead, WorkflowEvent

fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
eng = create_engine(f"sqlite:///{path}")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()

from app.services import account_graph as ag

fails, checks = [], 0
def ck(cond, label):
    global checks; checks += 1
    if not cond: fails.append(label)

def branch(n):
    l = B2BLead(company=f"More Supermarket {n}", city="Abohar",
                website="more.in", email=f"b{n}@more.in", division="RETAIL")
    db.add(l); db.commit(); return l

def touch(lead, kind, days_ago=0, mid=None):
    """A PROVEN send. EMAIL_SENT without both payload["to"] and
    payload["message_id"] is renamed EMAIL_SENT_UNPROVEN by a before_insert
    listener, so a test that omits them silently asserts nothing — the key is
    "to", not "recipient"."""
    db.add(WorkflowEvent(
        lead_id=lead.id, event_type=kind, actor="SYSTEM",
        channel="email" if kind == "EMAIL_SENT" else "whatsapp",
        payload={"to": lead.email,
                 "message_id": mid or f"<{kind}-{lead.id}-{days_ago}@test>"},
        occurred_at=datetime.utcnow() - timedelta(days=days_ago)))
    db.commit()

def clear():
    db.query(WorkflowEvent).delete(); db.commit()

a, b, c, d = branch(1), branch(2), branch(3), branch(4)
acct = ag.account_for(a, db)
ck(acct["branch_count"] == 4, f"branches resolved: {acct['branch_count']}")
print(f"1. account resolution: {acct['branch_count']} branches — {acct['basis']}")

# --- WhatsApp consumes the slot (the gap this closes) ---
clear(); touch(a, "WHATSAPP_SENT")
ok, why = ag.can_contact_new(b, db)
ck(ok is False, "WhatsApp did not consume the account cooldown")
ck("WhatsApp" in why, f"channel not named in the reason: {why}")
print(f"2. after a WhatsApp touch, sibling blocked: {not ok}")
print(f"   {why}")

# --- email behaves identically ---
clear(); touch(a, "EMAIL_SENT")
ok, why = ag.can_contact_new(c, db)
ck(ok is False, "email did not consume the account cooldown")
ck("email" in why, f"channel not named in the reason: {why}")
print(f"3. after an email touch, sibling blocked:   {not ok}")
print(f"   {why}")

# --- the chain rule applies to WhatsApp too ---
# 4 branches is a chain, so past the 7-day cooldown the 21-day corporate-first
# rule still holds. Asserting expiry at 8 days would be asserting the wrong
# rule and would "fail" against correct behaviour.
clear(); touch(a, "WHATSAPP_SENT", days_ago=ag.COOLDOWN_DAYS + 1)
ok, why = ag.can_contact_new(b, db)
ck(ok is False, "chain rule did not apply to a WhatsApp touch")
ck("chain" in why, f"reason does not cite the chain rule: {why}")
print(f"4. past cooldown, the {ag.CHAIN_CORPORATE_FIRST_DAYS}d chain rule still holds: {not ok}")
print(f"   {why}")

# --- and both rules do eventually expire ---
clear(); touch(a, "WHATSAPP_SENT", days_ago=ag.CHAIN_CORPORATE_FIRST_DAYS + 1)
ok, _ = ag.can_contact_new(b, db)
ck(ok is True, "WhatsApp cooldown never expires")
print(f"5. after {ag.CHAIN_CORPORATE_FIRST_DAYS}d a WhatsApp touch stops blocking: {ok}")

# --- unproven sends must NOT consume a slot ---
# 56 of the 97 historical send records are EMAIL_SENT_UNPROVEN. Counting them
# would suppress outreach across many accounts on the strength of records that
# by definition cannot show a send happened.
clear()
db.add(WorkflowEvent(lead_id=a.id, event_type="EMAIL_SENT", actor="SYSTEM",
                     channel="email", payload={},        # no proof at all
                     occurred_at=datetime.utcnow()))
db.commit()
stored = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == a.id).first()
ck(stored.event_type == "EMAIL_SENT_UNPROVEN",
   f"unproven send stored as {stored.event_type}")
ok, why = ag.can_contact_new(d, db)
ck(ok is True, "an unproven send consumed a real account slot")
print(f"6. unproven send -> {stored.event_type}, does not consume a slot: {ok}")

# --- a reply still closes the account to cold outreach ---
clear()
db.add(WorkflowEvent(lead_id=a.id, event_type="WHATSAPP_REPLY", actor="BUYER",
                     channel="whatsapp", payload={}, occurred_at=datetime.utcnow()))
db.commit()
ok, why = ag.can_contact_new(b, db)
ck(ok is False, "a live conversation did not pause cold outreach to siblings")
print(f"7. a WhatsApp reply pauses cold outreach to the rest: {not ok}")

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
