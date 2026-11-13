"""Per-call review for today's (or --date) AI calls. READ-ONLY.

Sources: call_results + call_history transcripts (agent turn log) in
purity_beans.db, and the voice agent PM2 log for latency / interruptions.
Recordings: when RECORDING_BACKEND is enabled and audio exists, transcripts
missing from the DB can be filled with Sarvam STT (not wired until recordings
exist - see report). Writes logs/call_review_<date>.md and prints a summary.
"""
import argparse, collections, datetime as dt, json, os, re, sqlite3, sys

AGENT_LOG = os.path.expanduser(r"~\.pm2\logs\nuraveda-voice-agent-out.log")
RULES = [
    (re.compile(r"100\s*%|sau pratishat", re.I), "said 100%"),
    (re.compile(r"\b3[1-9]\s*%|\b[4-9]\d\s*%", re.I), "margin figure above 30%"),
    (re.compile(r"ultra[^.]{0,40}(no chicory|chicory[- ]free|bina chicory)", re.I), "no-chicory claim on Ultra Blend"),
    # 28 Oct (Hiten): full range may be pitched (made to demand); Prima is no longer a violation.
    (re.compile(r"variety box[^.]{0,60}(no chicory|chicory[- ]free|bina chicory|\bpure\b)", re.I), "pure/no-chicory claim on Variety Box (contains Ultra Blend)"),
    (re.compile(r"ready stock|in stock", re.I), "claimed ready stock (supply is made to demand)"),
    (re.compile(r"main (insaan|human)|i am (a )?human|not an ai", re.I), "claimed to be human"),
    (re.compile(r"free sample", re.I), "promised free sample"),
    (re.compile(r"₹|rs\.?\s*\d|rupees?\s*\d", re.I), "quoted a price"),
]

def latency_by_room(day):
    out = collections.defaultdict(lambda: {"turns": [], "interrupts": 0, "silence": 0})
    if not os.path.exists(AGENT_LOG):
        return out
    room = None
    with open(AGENT_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r'"roomName":"([^"]+)"', line)
            if m: room = m.group(1)
            m2 = re.search(r"call for .* / (purity-lead-\d+)", line)
            if m2: room = room or m2.group(1)
            if room is None: continue
            m = re.search(r"TURN_LATENCY\] Turn #\d+: .*= (\d+)ms", line)
            if m: out[room]["turns"].append(int(m.group(1)))
            if "playout interrupted" in line: out[room]["interrupts"] += 1
            if "[silence] re-prompt" in line: out[room]["silence"] += 1
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args()
    day_ist = dt.date.fromisoformat(a.date)
    start = dt.datetime.combine(day_ist, dt.time()) - dt.timedelta(hours=5, minutes=30)
    end = start + dt.timedelta(days=1)
    c = sqlite3.connect("file:purity_beans.db?mode=ro", uri=True)
    rows = c.execute("""select r.call_id,r.lead_id,l.company,r.room_name,r.connected,r.outcome,r.termination_reason,
        r.drop_off_stage,r.objections,r.duration_seconds,r.opener_variant,r.evidence
        from call_results r join b2b_leads l on l.id=r.lead_id
        where r.started_at>=? and r.started_at<? order by r.started_at""", (str(start), str(end))).fetchall()
    lat = latency_by_room(a.date)
    lines = [f"# Call review {a.date} (IST)", ""]
    agg = collections.Counter(); issues = collections.Counter(); drops = collections.Counter(); objs = collections.Counter()
    allturns = []
    for cid, lid, co, room, conn, out, term, drop, obj, dur, opener, ev in rows:
        agg["calls"] += 1
        if not conn:
            continue
        agg["connected"] += 1
        tr = c.execute("select call_transcript from call_history where lead_id=? and call_date>=? and call_date<? order by id desc limit 1",
                       (lid, str(start), str(end))).fetchone()
        tr = (tr[0] if tr else "") or ""
        asst = "\n".join(l[10:] for l in tr.splitlines() if l.startswith("assistant:"))
        found = [lab for rx, lab in RULES if rx.search(asst)]
        for f_ in found: issues[f_] += 1
        drops[drop or "?"] += 1
        for o in json.loads(obj or "[]"): objs[o] += 1
        L = lat.get(room or "", {"turns": [], "interrupts": 0, "silence": 0})
        allturns += L["turns"]
        gaps = [t for t in L["turns"] if t > 2500]
        evd = json.loads(ev or "{}") if isinstance(ev, str) else (ev or {})
        lines += [f"## {co} (lead {lid}) - {out} / label {evd.get('final_label','-')} ({evd.get('label_score','-')})",
                  f"- opener {opener or 'baseline'}, duration {dur or 0:.0f}s, ended: {term or '-'}, drop-off: {drop or '-'}",
                  f"- turns {len(L['turns'])}, slow turns >2.5s: {gaps or 'none'}, interruptions {L['interrupts']}, silence re-prompts {L['silence']}",
                  f"- objections: {json.loads(obj or '[]') or 'none'}",
                  f"- compliance: {', '.join(found) if found else 'no issues found'}",
                  f"- transcript: {'yes' if tr else 'MISSING'}", ""]
    allturns.sort()
    med = allturns[len(allturns)//2] if allturns else None
    summ = [f"calls={agg['calls']} connected={agg['connected']} median_turn_ms={med} slow_turns={sum(1 for t in allturns if t>2500)}/{len(allturns)}",
            f"drop-off: {dict(drops)}", f"objections: {dict(objs)}", f"compliance issues: {dict(issues) or 'none'}"]
    lines = lines[:2] + ["**Summary:** " + " | ".join(summ), ""] + lines[2:]
    os.makedirs("logs", exist_ok=True)
    out_p = os.path.join("logs", f"call_review_{a.date}.md")
    open(out_p, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(summ)); print("written", out_p)

if __name__ == "__main__":
    main()
