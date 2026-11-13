"""Send the 17 backfilled founder lead alerts to the founder's own WhatsApp, one at a time.
Approved by Hiten 4 Nov 2026 15:23 IST. Refuses to run until the Klaviyo flow is live:
  python scripts/send_founder_backfill.py --flow-id <FLOW_ID> --go
Without --go it only prints what it would send. Already-SENT rows are skipped (idempotent;
Klaviyo unique_id also dedupes)."""
import sys, os, json, time, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(sys.path[0])
from dotenv import load_dotenv; load_dotenv(".env")
import requests
from app.services import founder_handoff_alert as fa
OUT = "data/founder_alerts/backfill_outbox_20261104.jsonl"
ap = argparse.ArgumentParser(); ap.add_argument("--flow-id", required=True); ap.add_argument("--go", action="store_true")
ap.add_argument("--gap", type=int, default=30); a = ap.parse_args()
key = os.getenv("KLAVIYO_PRIVATE_KEY", ""); H = {"Authorization": f"Klaviyo-API-Key {key}", "revision": "2025-07-15", "accept": "application/vnd.api+json"}
st = requests.get(f"https://a.klaviyo.com/api/flows/{a.flow_id}/", headers=H, timeout=15).json()["data"]["attributes"]["status"]
if st != "live": sys.exit(f"flow {a.flow_id} is {st}, not live: nothing sent")
if os.getenv("FOUNDER_ALERT_SEND") != "1" or not os.getenv("FOUNDER_ALERT_PHONE"): sys.exit("FOUNDER_ALERT_SEND/PHONE not set: nothing sent")
rows = [json.loads(l) for l in open(OUT)]
for r in rows:
    if r.get("status") == "SENT": continue
    print(r["seq"], r["lead_id"], r["key"], "->", fa.mask(os.getenv("FOUNDER_ALERT_PHONE")))
    if not a.go: continue
    ok, why = fa._emit_klaviyo(r["text"], {"lead_id": r["lead_id"], "key": r["key"], "day": "backfill-20261104"})
    r.update(status="SENT" if ok else "SEND_FAILED", why=why, sent_at=time.strftime("%Y-%m-%d %H:%M:%S IST"))
    open(OUT, "w").write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
    if not ok: sys.exit(f"stopped at seq {r['seq']}: {why}")
    time.sleep(a.gap)
