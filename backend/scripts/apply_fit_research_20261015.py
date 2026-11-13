"""15 Oct 2026 - write web-evidence INSTANT_COFFEE_FIT markers. Skips any lead
that already has a marker. Dry run by default; --commit writes. Retries on lock."""
import json, sqlite3, sys, time
from datetime import datetime
DB = "purity_beans.db"; SRC = r"C:\Users\hiten\Desktop\ppp\assess.json"
commit = "--commit" in sys.argv
ts = datetime.now().strftime("%Y-%m-%d %H:%M")
rows = json.load(open(SRC))
for attempt in range(6):
    try:
        db = sqlite3.connect(DB, timeout=60); db.execute("PRAGMA busy_timeout=60000")
        done = skip = 0
        for lid, tier, url, note in rows:
            r = db.execute("select coffee_buying_evidence from b2b_leads where id=?", (int(lid),)).fetchone()
            if r is None: print("missing", lid); continue
            old = r[0] or ""
            if "[INSTANT_COFFEE_FIT" in old: skip += 1; continue
            m = f"[INSTANT_COFFEE_FIT {ts} IST] {tier} | web | {url} | {note}"
            new = (old + " || " + m) if old.strip() else m
            db.execute("update b2b_leads set coffee_buying_evidence=? where id=?", (new, int(lid))); done += 1
        if commit: db.commit()
        else: db.rollback()
        print("written" if commit else "would write", done, "skipped(existing marker)", skip); break
    except sqlite3.OperationalError as e:
        print("retry", attempt, e); db.close(); time.sleep(10)
