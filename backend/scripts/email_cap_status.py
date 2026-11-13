#!/usr/bin/env python3
"""Read-only: print today's shared email-cap counts as JSON (used by the box send gate).
4 Nov 2026 (Hiten 15:23 IST): one 15/day cap across server + box."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import human_pace as hp
print(json.dumps({"day": hp._today(), "server": hp.server_daily_count(), "box": hp.box_sent_today(),
                  "cap": hp.effective_daily_cap()}))
