"""
Daily tender auto-pricer — run via Windows Task Scheduler at 9 AM.
Escalates discount on stale PROPOSAL_SENT tender leads and sends nudge emails.

Usage:
  python run_tender_pricer.py              # run with emails
  python run_tender_pricer.py --no-email   # dry run, no emails
"""
import sys, os, json
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from app.services.tender_auto_pricer import run_tender_auto_pricer

send_emails = "--no-email" not in sys.argv
report = run_tender_auto_pricer(send_emails=send_emails)

# Save report
log_dir = Path(__file__).parent / "tender_pricer_logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
log_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Tender auto-pricer complete")
print(f"  Leads checked:   {report['leads_checked']}")
print(f"  Leads escalated: {report['leads_escalated']}")
print(f"  Emails sent:     {report['emails_sent']}")
print(f"  Log saved:       {log_file}")

for action in report["actions"]:
    tier = action.get("tier", "—")
    company = action.get("company", "?")
    days = action.get("days_since_proposal", 0)
    disc = action.get("new_discount_pct", action.get("old_discount_pct", 0))
    emailed = action.get("email_sent", False)
    print(f"  [{tier:12s}] {company} — day {days}, {disc}% off, email={'YES' if emailed else 'no'}")
