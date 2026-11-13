import json

with open("priority_a_audit.json", "r") as f:
    leads = json.load(f)

def rank_key(l):
    score = 0
    ev_str = " ".join(l.get("evidence_signals", []) + [l.get("coffee_buying_evidence", "")]).lower()
    if "enquiry" in ev_str or "looking for coffee" in ev_str or "requirement" in ev_str:
        score += 50
    if "EXPLICIT consent" in l.get("whatsapp_eligibility", ""):
        score += 40
    if l.get("email_eligibility", "").startswith("True"):
        score += 20
    if l.get("voice_eligibility", "").startswith("True"):
        score += 20
    if l.get("buying_requirement", "") != "Requirement pending detailed discovery call":
        score += 15
    if l.get("history"):
        score += 10
    score += l.get("intent_score", 0)
    return score

leads_ranked = sorted(leads, key=rank_key, reverse=True)

print(f"Total leads: {len(leads_ranked)}")
print("=== TOP 10 RANKED ===")
for i, l in enumerate(leads_ranked[:10], 1):
    lid = l["id"]
    comp = l["company"]
    score = l["intent_score"]
    seg = l["segment"]
    fit = l["product_recommendation"]
    v = l["voice_eligibility"]
    e = l["email_eligibility"]
    w = l["whatsapp_eligibility"]
    ev = l["coffee_buying_evidence"][:70]
    hist = l["history"]
    nxt = l["next_action"]
    print(f"{i}. Lead #{lid}: {comp} (Score: {score})")
    print(f"   Segment: {seg} | Fit: {fit}")
    print(f"   Voice: {v} | Email: {e} | WA: {w}")
    print(f"   Evidence: {ev}...")
    print(f"   History: {hist}")
    print(f"   Next: {nxt}\n")

with open("priority_a_ranked.json", "w") as f:
    json.dump(leads_ranked, f, indent=2)
