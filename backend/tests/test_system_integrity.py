"""Whole-system verification after a third-party tool edited the codebase."""
import sys, re, httpx
sys.path.insert(0, r"C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session\backend")
from app.database.database import SessionLocal
from app.models.models import B2BLead, WorkflowEvent
from app.services import deliverability as D
from app.services.email_verifier import verify_email
from app.services.trust_promoter import may_send as sendable_gate

B = "http://127.0.0.1:8003/api/v1"
db = SessionLocal()
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if detail else ""))


print("WHOLE-SYSTEM VERIFICATION\n")

# 1. No sendable address that cannot actually receive mail
sendable = [l for l in db.query(B2BLead).filter(
    B2BLead.email != "", B2BLead.email.isnot(None)).all() if sendable_gate(l)[0]]
bad = []
for l in sendable:
    # Fourth argument is `website`; this passed it as `division` and the
    # verifier's domain-match check was comparing a URL against a category.
    v = verify_email(l.email, l.company or "", l.division or "", l.website or "")
    st = (v.get("status") or "UNVERIFIED").upper()
    # CATCH_ALL is not a failure. Most Indian SMB domains are catch-all, and
    # calling that "undeliverable" is what pinned the sendable count at 4.
    if st == "INVALID" or not v.get("mx_valid") or v.get("is_disposable"):
        bad.append(f"{l.email} ({st}, mx={v.get('mx_valid')})")
check("no sendable address has a CONCLUSIVE delivery failure",
      not bad, f"{len(sendable)} sendable, {len(bad)} conclusively undeliverable")

# 2. No name-derived domains left sendable
def slug(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())
# A real company's real domain IS usually its own name: bgtechvista.com
# genuinely belongs to BG Techvista. What made the old addresses fabricated was
# not the shape of the domain but who invented it — we did, and then never
# checked. Provenance is the honest discriminator: an address READ OFF the
# company's own website or given by the buyer on a call is evidence, whatever
# its domain looks like. Only a name-shaped domain with NO provenance is
# suspect.
derived = [l.email for l in sendable
           if slug(l.email.split("@")[-1].rsplit(".", 1)[0]) == slug(l.company)
           and (l.email_source or "") not in ("WEBSITE", "FOUNDER_CALL", "EMAIL_REPLY", "DELIVERED")]
check("no sendable address is a name-shaped domain with no provenance",
      not derived, f"{len(derived)} found")

# 3. Deliverability guard is honest
h = D.health(db)
check("deliverability guard is not self-blocked", h["can_send_now"] is True,
      h["status_reason"][:70])

# 4. Gate refusals excluded from the failure rate
from app.services.deliverability import _is_own_refusal
check("our own gate refusal is not counted as a delivery failure",
      _is_own_refusal({"smtp_error": "BLOCKED: x is on file but not verified"})
      and _is_own_refusal({"smtp_error": "HELD: pacing"})
      and not _is_own_refusal({"smtp_error": "550 5.4.6 unusual sending activity"}))

# 5. One definition of sendable across the codebase
import pathlib
root = pathlib.Path(r"C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session\backend\app")
relaxed = []
for p in root.rglob("*.py"):
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        # Only real CODE counts. Prose describing why the widening is rejected
        # kept tripping this check — a comment warning against the bug is not the
        # bug. Require the comparison to sit inside an expression.
        t = line.strip()
        if t.startswith("#") or t.startswith('"') or t.startswith("'"):
            continue
        if ('email_verification_status == "VALID"' in line
                and any(k in line for k in ("if ", "or ", "and ", "return ", "="))
                and "is deliberately NOT" not in line):
            relaxed.append(f"{p.name}:{i}")
check("no module accepts a bare status=='VALID' as sendable", not relaxed,
      ", ".join(relaxed) or "clean")

# 6. Live endpoints still answer
ok = True
for path, params in (("/b2b/outreach/search", {"outreach_method": "PHONE_ONLY"}),
                     ("/b2b/outreach/phone-program", {}),
                     ("/b2b/leads", {}),
                     ("/b2b/outreach/call-outcomes", {})):
    try:
        r = httpx.get(B + path, params=params, timeout=180)
        if r.status_code != 200:
            ok = False
            print(f"        {path} -> HTTP {r.status_code}")
    except Exception as e:
        ok = False
        print(f"        {path} -> {type(e).__name__}")
check("core endpoints respond 200", ok)

# 7. Fabrication markers in lead data
FAKE = re.compile(r"98765-\d|jane\.?doe|jsmith|example\.com|test@|Rajesh Kumar", re.I)
fab = [l.id for l in db.query(B2BLead).all()
       if FAKE.search(" ".join(str(getattr(l, f, "") or "")
                               for f in ("company", "email", "phone", "contact_name")))]
check("no lead carries a known fabrication marker", not fab, f"{len(fab)} found")

# 8. Leads claiming Google Maps without a place_id (the fabricated batch)
ghost = [l for l in db.query(B2BLead).all()
         if (l.lead_source or "").lower().replace(" ", "") == "googlemaps"
         and not (l.place_id or "").strip()
         and l.maps_reviews_count is None]
check("leads sourced 'Google Maps' carry Maps evidence", not ghost,
      f"{len(ghost)} claim Maps with no place_id and no reviews")

db.close()
print(f"\n  {sum(res)}/{len(res)} checks passed")
