#!/usr/bin/env python3
"""Append one box send to the shared cap ledger (no addresses stored: bot, IST time, hashed recipient).
Usage: email_cap_record_box.py <id> <YYYY-MM-DD HH:MM:SS IST> <bot> <sha1-of-recipient>. Idempotent per id."""
import json, os, sys, fcntl
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import human_pace as hp
sid, ts, bot, rh = sys.argv[1:5]
p = hp._box_ledger(); os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "a+", encoding="utf-8") as f:
    fcntl.flock(f, fcntl.LOCK_EX); f.seek(0)
    if any(json.loads(l).get("id") == sid for l in f if l.strip()):
        print("DUP"); sys.exit(0)
    f.write(json.dumps({"id": sid, "day": ts[:10], "ts": ts, "bot": bot, "rcpt_sha1": rh}) + "\n")
print("OK")
