#!/usr/bin/env python3
"""Record mailer-daemon bounces on the server (4 Nov 2026, Hiten approved the bounce fix, item D).

Reads the connect@ Zoho INBOX over IMAP (read-only: EXAMINE + BODY.PEEK, nothing is
marked read, moved or deleted, and NOTHING IS SENT), finds delivery-failure reports
(DSNs), and for each failed recipient that is a lead's address:
  * adds an EMAIL_BOUNCED workflow_event {to, status, diagnostic, message_id}
    (bounce_guard.check() already refuses any address with such an event),
  * moves email_trust -> BOUNCED via trust_promoter.transition() (audit trail kept),
  * marks matching SENT email_drafts delivery_status = BOUNCED.
Only FINAL failures count: DSN "Action: failed", or a 5.x.x code. "delayed"/4.x.x
warnings are skipped. Idempotent (same message_id + address never recorded twice).
Every change is appended to logs/bounce_recorder.jsonl (undo record).

  python scripts/record_bounces.py --dry-run [--days 14]
  python scripts/record_bounces.py            # apply
Output masks addresses.
"""
from __future__ import annotations
import argparse, email as pyemail, imaplib, json, os, re, sys
from datetime import datetime, timedelta

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except Exception:
    pass

ADDR = r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
LOG = os.path.join(BACKEND, "logs", "bounce_recorder.jsonl")
DSN_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery (sub)?system", re.I)
DSN_SUBJ = re.compile(r"undeliver|delivery status notification|delivery (has )?failed|"
                      r"failure notice|returned mail|could not be delivered|mail delivery failed", re.I)


def mask(a):
    u, _, d = (a or "").partition("@")
    return (u[:1] + "***@" + d) if d else "***"


def _pl(p):
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p or "{}") or {}
    except Exception:
        return {}


def parse_dsn(msg):
    """[(address, status, diagnostic, final)]"""
    out = {}
    for h in msg.get_all("X-Failed-Recipients") or []:
        for a in re.findall(ADDR, h):
            out[a.lower()] = ["", "X-Failed-Recipients", True]
    text_parts = []
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "message/delivery-status":
            payload = part.get_payload()
            blocks = payload if isinstance(payload, list) else [part]
            for b in blocks:
                raw = b.as_string() if hasattr(b, "as_string") else str(b)
                for chunk in re.split(r"\n\s*\n", raw):
                    m = re.search(r"^(?:Final|Original)-Recipient:\s*[^;]*;\s*(" + ADDR + ")", chunk, re.I | re.M)
                    if not m:
                        continue
                    act = (re.search(r"^Action:\s*(\S+)", chunk, re.I | re.M) or [None, ""])[1].lower()
                    st = (re.search(r"^Status:\s*(\S+)", chunk, re.I | re.M) or [None, ""])[1]
                    dg = (re.search(r"^Diagnostic-Code:\s*(.+)$", chunk, re.I | re.M) or [None, ""])[1]
                    final = act == "failed" or st.startswith("5")
                    out[m.group(1).lower()] = [st, dg.strip()[:200], final]
        elif ct == "text/plain":
            try:
                text_parts.append(part.get_payload(decode=True).decode(errors="ignore"))
            except Exception:
                pass
    if not out:
        # Plain-text bounce (no DSN part): need a permanent 5.x.x code next to an address.
        # Conservative: only an address within 300 chars of the code, before any quoted original.
        body = "\n".join(text_parts)
        body = re.split(r"(?im)^-+\s*(original message|forwarded message)|^received:", body)[0]
        for m in re.finditer(r"\b5\.\d{1,3}\.\d{1,3}\b|\b55[0-4]\b", body):
            win = body[max(0, m.start() - 300): m.end() + 300]
            for a in set(x.lower() for x in re.findall(ADDR, win)):
                out.setdefault(a, [m.group(0), "plain-text bounce", True])
    return [(a, v[0], v[1], v[2]) for a, v in out.items()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    user = os.getenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
    pw = os.getenv("ZOHO_APP_PASSWORD", "")
    if not pw.strip():
        sys.exit("ZOHO app password not set; nothing done")
    own = user.lower().strip()

    import app.models.models  # noqa: F401
    from app.models.models import B2BLead, WorkflowEvent, EmailDraft
    from app.database.database import SessionLocal
    from app.services import trust_promoter as tp
    from app.services.contact_trust import stamp

    M = imaplib.IMAP4_SSL("imap.zoho.in", 993, timeout=30)
    M.login(user, pw)
    st, _ = M.select("INBOX", readonly=True)       # EXAMINE: read-only
    if st != "OK":
        sys.exit("cannot open INBOX")
    since = (datetime.utcnow() - timedelta(days=a.days)).strftime("%d-%b-%Y")
    st, data = M.search(None, f"SINCE {since}")
    ids = data[0].split() if st == "OK" else []
    found = []
    for mid in ids:
        st, hd = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if st != "OK" or not hd or not hd[0]:
            continue
        h = pyemail.message_from_bytes(hd[0][1])
        if not (DSN_FROM.search(h.get("From", "")) or DSN_SUBJ.search(h.get("Subject", ""))):
            continue
        st, full = M.fetch(mid, "(BODY.PEEK[])")
        if st != "OK" or not full or not full[0]:
            continue
        msg = pyemail.message_from_bytes(full[0][1])
        msgid = (msg.get("Message-ID") or f"imap-{mid.decode()}").strip()
        for addr, code, diag, final in parse_dsn(msg):
            if addr == own or addr.endswith("@purepantryprovisions.com"):
                continue
            found.append((msgid, addr, code, diag, final, msg.get("Date", "")))
    M.logout()

    db = SessionLocal()
    rec, skipped, unmatched, soft = [], 0, set(), 0
    try:
        for msgid, addr, code, diag, final, date in found:
            if not final:
                soft += 1
                continue
            leads = db.query(B2BLead).filter(B2BLead.email.isnot(None)).filter(
                B2BLead.email.ilike(addr)).all()
            leads = [l for l in leads if (l.email or "").strip().lower() == addr]
            if not leads:
                unmatched.add(addr)
                continue
            for l in leads:
                dup = [e for e in db.query(WorkflowEvent).filter(
                    WorkflowEvent.lead_id == l.id,
                    WorkflowEvent.event_type == "EMAIL_BOUNCED").all()
                    if _pl(e.payload).get("message_id") == msgid
                    and (_pl(e.payload).get("to") or "").lower() == addr]
                if dup:
                    skipped += 1
                    continue
                old = {"email_trust": l.email_trust, "email_verified": bool(l.email_verified),
                       "email_source": l.email_source}
                rec.append({"lead_id": l.id, "to": addr, "status": code, "message_id": msgid,
                            "bounce_date": date, "before": old})
                if a.dry_run:
                    continue
                db.add(WorkflowEvent(lead_id=l.id, event_type="EMAIL_BOUNCED", actor="BOUNCE_RECORDER",
                                     channel="email",
                                     payload={"to": addr, "status": code, "diagnostic": diag,
                                              "message_id": msgid, "bounce_date": date},
                                     occurred_at=datetime.utcnow()))
                if (l.email_trust or "").upper() not in ("BOUNCED", "PURGED"):
                    tp.transition(l, "BOUNCED", f"mailer-daemon bounce {code}".strip(),
                                  [code, diag[:120]], "", db, 0)
                    l.email_verified = False
                stamp(l)
                ds = db.query(EmailDraft).filter(EmailDraft.lead_id == l.id,
                                                 EmailDraft.status == "SENT").all()
                rec[-1]["drafts_marked"] = []
                for d in ds:
                    if (d.recipient or l.email or "").strip().lower() == addr and \
                            (d.delivery_status or "").upper() != "BOUNCED":
                        rec[-1]["drafts_marked"].append({"id": d.id, "before": d.delivery_status})
                        d.delivery_status = "BOUNCED"
        if not a.dry_run and rec:
            db.commit()
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a") as f:
                for r in rec:
                    f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **r}) + "\n")
        else:
            db.rollback()
    finally:
        db.close()
    print(f"{datetime.now():%F %T} DSNs scanned: {len(found)} recipient(s); soft/delayed skipped: {soft}; "
          f"already recorded: {skipped}; not a lead: {len(unmatched)}; "
          f"{'WOULD RECORD' if a.dry_run else 'RECORDED'}: {len(rec)}")
    for r in rec:
        print(f"  lead {r['lead_id']:>5} {mask(r['to'])} {r['status']} (was {r['before']['email_trust']})")
    for u in sorted(unmatched):
        print(f"  not a lead: {mask(u)}")


if __name__ == "__main__":
    main()
