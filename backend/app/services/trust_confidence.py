"""Fair confidence backfill for Contact Trust Engine (evidence-based).

Fixes VERIFIED-at-confidence-0 / stuck VALIDATED by syncing email_confidence
from confidence_for (+ live tech) when provenance warrants it. Does NOT invent
emails, bypass bounce/purge/opt-out, or lower CONFIDENCE_FLOOR.
"""
from __future__ import annotations

from app.services import trust_promoter as tp

STRONG_SOURCES = (
    "WEBSITE", "FOUNDER_CALL", "EMAIL_REPLY", "DELIVERED",
    "BUSINESS_CARD_OCR", "BUSINESS_MEMORY",
)


def _live_tech_bonus(lead) -> tuple[int, list[str]]:
    tech = getattr(lead, "_tech", None) or {}
    if not tech:
        st = (getattr(lead, "email_verification_status", "") or "").upper()
        if st in ("VALID", "CATCH_ALL"):
            tech = {"syntax_ok": True, "mx_ok": True, "smtp_ok": True, "status": st}
    score, why = 0, []
    if tech.get("syntax_ok"):
        score += 5; why.append("LIVE_SYNTAX +5")
    if tech.get("mx_ok"):
        score += tp.EVIDENCE_WEIGHT["MX"]; why.append(f"LIVE_MX +{tp.EVIDENCE_WEIGHT['MX']}")
    if tech.get("smtp_ok"):
        score += tp.EVIDENCE_WEIGHT["SMTP"]; why.append(f"LIVE_SMTP +{tp.EVIDENCE_WEIGHT['SMTP']}")
    return score, why


def fair_confidence(lead, db) -> dict:
    base = tp.confidence_for(lead, db)
    score = int(base["confidence"])
    why = list(base.get("why") or [])
    has_tech = any(w.startswith(("SYNTAX", "MX +", "SMTP")) for w in why)
    if not has_tech:
        bonus, extra = _live_tech_bonus(lead)
        if bonus:
            score = min(100, score + bonus); why.extend(extra)
    return {"confidence": max(0, min(100, score)), "why": why,
            "last_activity": base.get("last_activity")}


def sync_stored_confidence(lead, db) -> dict:
    fair = fair_confidence(lead, db)
    score = int(fair["confidence"])
    stored = getattr(lead, "email_confidence", None)
    stored_i = int(stored) if stored is not None else -1
    wrote = False
    if score > stored_i and score > 0:
        lead.email_confidence = score
        wrote = True
    return {"wrote": wrote, "confidence": score, "was": stored, "why": fair["why"]}


def ensure_tech_for_scoring(lead, *, verify: bool = True) -> None:
    if getattr(lead, "_tech", None):
        return
    if not verify:
        st = (getattr(lead, "email_verification_status", "") or "").upper()
        if st in ("VALID", "CATCH_ALL"):
            lead._tech = {"syntax_ok": True, "mx_ok": True, "smtp_ok": True, "status": st}
        return
    try:
        tp._technically_ok(lead)
    except Exception:
        pass


def reevaluate_one(lead, db, *, verify: bool = True) -> dict:
    ensure_tech_for_scoring(lead, verify=verify)
    moved = tp.evaluate(lead, db, verify=verify)
    ensure_tech_for_scoring(lead, verify=False)
    sync = sync_stored_confidence(lead, db)
    ok, why = tp.may_send(lead)
    return {"lead_id": getattr(lead, "id", None), "evaluate": moved, "sync": sync,
            "may_send": ok, "may_send_why": why,
            "trust": tp.normalise(getattr(lead, "email_trust", None)),
            "confidence": getattr(lead, "email_confidence", None)}


def count_pool(db) -> dict:
    from app.models.models import B2BLead
    leads = db.query(B2BLead).filter(B2BLead.email.isnot(None), B2BLead.email != "").all()
    eligible = validated = verified_hi = verified_lo = 0
    for lead in leads:
        t = tp.normalise(getattr(lead, "email_trust", None))
        c = int(getattr(lead, "email_confidence", None) or 0)
        ok, _ = tp.may_send(lead)
        if ok: eligible += 1
        if t == tp.VALIDATED: validated += 1
        if t in tp.MAY_SEND:
            if c >= tp.CONFIDENCE_FLOOR: verified_hi += 1
            else: verified_lo += 1
    return {"email_eligible": eligible, "VALIDATED": validated,
            "VERIFIED_or_better_conf_ge_40": verified_hi,
            "VERIFIED_or_better_conf_lt_40": verified_lo, "with_email": len(leads)}


def run_limited_sweep(db, *, limit: int = 80, verify: bool = True) -> dict:
    from app.models.models import B2BLead
    before = count_pool(db)
    targets = []
    for lead in db.query(B2BLead).filter(B2BLead.email.isnot(None), B2BLead.email != "").all():
        t = tp.normalise(getattr(lead, "email_trust", None))
        c_i = int(getattr(lead, "email_confidence", None) or 0)
        if t == tp.VALIDATED or (t in tp.MAY_SEND and c_i < tp.CONFIDENCE_FLOOR):
            targets.append(lead)
    targets = targets[: max(1, int(limit))]
    results, moved, synced, newly = [], 0, 0, 0
    for lead in targets:
        try:
            r = reevaluate_one(lead, db, verify=verify)
            results.append(r)
            if r["evaluate"].get("moved"): moved += 1
            if r["sync"].get("wrote"): synced += 1
            if r["may_send"]: newly += 1
        except Exception as e:
            results.append({"lead_id": lead.id, "error": f"{e.__class__.__name__}: {e}"})
    db.commit()
    return {"considered": len(targets), "moved": moved, "confidence_synced": synced,
            "may_send_in_batch": newly, "before": before, "after": count_pool(db),
            "results": results[:20]}


_EVALUATE_PATCHED = False

def patch_evaluate_scoring() -> None:
    global _EVALUATE_PATCHED
    if _EVALUATE_PATCHED: return
    _orig = tp.evaluate
    def _evaluate(lead, db, verify=True):
        result = _orig(lead, db, verify=verify)
        try: sync_stored_confidence(lead, db)
        except Exception: pass
        return result
    tp.evaluate = _evaluate  # type: ignore[assignment]
    _EVALUATE_PATCHED = True

try: patch_evaluate_scoring()
except Exception: pass

def patch_contact_trust_grant() -> None:
    try:
        from app.services import contact_trust as ct
    except Exception:
        return
    if getattr(ct, "_GRANT_CONFIDENCE_PATCHED", False): return
    _orig = ct.grant
    def _grant(lead, trust: str, source: str, db=None, note: str = ""):
        _orig(lead, trust, source, db=db, note=note)
        try:
            if db is not None: sync_stored_confidence(lead, db)
            else:
                src = (source or "").upper()
                if src in STRONG_SOURCES and tp.EVIDENCE_WEIGHT.get(src, 0) >= tp.CONFIDENCE_FLOOR:
                    lead.email_confidence = max(int(lead.email_confidence or 0), tp.EVIDENCE_WEIGHT[src])
        except Exception: pass
    ct.grant = _grant  # type: ignore[assignment]
    ct._GRANT_CONFIDENCE_PATCHED = True

try: patch_contact_trust_grant()
except Exception: pass
