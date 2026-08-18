"""
Marketplace acceptance: PURISTA_50G x Amazon, end to end.

  ingest -> mapping -> metrics -> mi_evaluate() -> diagnosis
         -> mi_recommendation -> FOUNDER_REVIEW -> ZERO EXECUTION

The last step is the one that matters. This path may propose a price change, an
ad change or a restock; it may never perform one. A recommendation is a
proposal, and founder consent lives in founder_actions, which is a separate
table written by a separate call that refuses actor=SYSTEM.

Isolated exactly like test_failclosed_gate: an engine bound to a temp file,
because app.database.database reads DATABASE_URL at import time and a
monkeypatched env var would be silently ignored.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

CHANNEL = "amazon"
SKU_CODE = "PURISTA_50G"
DATE = "2026-08-18"


@pytest.fixture()
def mi_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database.database import Base
    import app.models.models             # noqa: F401
    import app.models.marketplace_intel  # noqa: F401
    # FounderAction is declared in a SERVICE module, so it must be imported
    # before create_all or the zero-execution assertion below queries a table
    # that does not exist and fails as an OperationalError rather than a
    # verdict about consent.
    import app.services.founder_actions  # noqa: F401
    from app.services import marketplace_intel as mi

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    yield db, mi
    db.close()
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _sku(db, mi):
    from app.models.marketplace_intel import SkuMaster
    mi.seed_skus(db)
    return db.query(SkuMaster).filter(SkuMaster.sku_code == SKU_CODE).first()


def test_seed_creates_the_sku_and_channel_map(mi_db):
    db, mi = mi_db
    from app.models.marketplace_intel import SkuChannelMap
    sku = _sku(db, mi)
    assert sku is not None, f"{SKU_CODE} was not seeded"
    amazon = (db.query(SkuChannelMap)
              .filter(SkuChannelMap.sku_id == sku.id,
                      SkuChannelMap.channel == CHANNEL).first())
    assert amazon is not None, "no amazon channel map row"
    print(f"\n  mapping: {SKU_CODE} -> amazon status={amazon.status}")


def test_full_pipeline_ends_in_founder_review_with_zero_execution(mi_db):
    db, mi = mi_db
    from app.models.marketplace_intel import (
        FpSalesDaily, FpAdsDaily, MiPriceSnapshot, MiAvailability,
        MiMetricDaily, MiRecommendation, SkuChannelMap)

    sku = _sku(db, mi)

    # Make the channel live, otherwise the SKU is NOT_LISTED on amazon.
    cm = (db.query(SkuChannelMap)
          .filter(SkuChannelMap.sku_id == sku.id,
                  SkuChannelMap.channel == CHANNEL).first())
    cm.status, cm.channel_sku_id = "ACTIVE", "B0TESTASIN"
    db.commit()

    # ---- ingest -------------------------------------------------------
    # Deliberately unhealthy: ad spend far above ad sales, and a competitor
    # priced below us. A pipeline that returns NONE here proves nothing.
    db.add(FpSalesDaily(date=DATE, channel=CHANNEL, sku_id=sku.id,
                        units=12, gmv_inr=4188.0, net_revenue_inr=3400.0,
                        returns_units=1, source="manual_csv"))
    db.add(FpAdsDaily(date=DATE, channel=CHANNEL, sku_id=sku.id,
                      spend_inr=2600.0, ad_sales_inr=1500.0,
                      clicks=210, impressions=9400, source="manual_csv"))
    db.add(MiPriceSnapshot(channel=CHANNEL, sku_id=sku.id, seller_name="Purity Beans",
                           price_inr=349.0, mrp_inr=sku.mrp_inr, is_self=True,
                           source="manual"))
    db.add(MiAvailability(channel=CHANNEL, sku_id=sku.id, in_stock=True,
                          is_self=True, source="manual"))
    db.commit()
    print(f"\n  ingest: sales + ads + price + availability for {SKU_CODE}")

    # ---- metrics ------------------------------------------------------
    m = mi.recompute_metrics(db, sku.id, CHANNEL, DATE)
    assert isinstance(m, MiMetricDaily)
    stored = (db.query(MiMetricDaily)
              .filter(MiMetricDaily.sku_id == sku.id,
                      MiMetricDaily.channel == CHANNEL,
                      MiMetricDaily.date == DATE).all())
    assert len(stored) == 1, f"expected 1 metric row, got {len(stored)}"
    print(f"  metrics: 1 row for {DATE}")

    # ---- evaluate -----------------------------------------------------
    res = mi.mi_evaluate(db, sku.id, CHANNEL, DATE)
    assert isinstance(res, dict)
    for key in ("metrics", "audit"):
        assert key in res, f"mi_evaluate result missing {key!r}"
    print(f"  evaluate: diagnosis={res.get('primary_diagnosis')} "
          f"action={res.get('recommended_action')} conf={res.get('confidence')}")

    # ---- recommendation is a PROPOSAL awaiting the founder -------------
    recs = db.query(MiRecommendation).all()
    if res.get("recommended_action") not in (None, "NONE"):
        assert recs, "an action was recommended but no mi_recommendation row exists"
    for r in recs:
        assert r.status == "FOUNDER_REVIEW", (
            f"recommendation {r.id} status={r.status}, expected FOUNDER_REVIEW")
    print(f"  recommendation: {len(recs)} row(s), all FOUNDER_REVIEW")

    # ---- ZERO EXECUTION ------------------------------------------------
    # Nothing may have been sent, approved, or consented to. founder_actions is
    # the only place consent can exist, and only a human may write it.
    from app.services.founder_actions import FounderAction
    from app.models.models import WorkflowEvent
    assert db.query(FounderAction).count() == 0, "the pipeline manufactured consent"
    executed = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(
            ("EMAIL_SENT", "WHATSAPP_SENT", "OUTREACH_APPROVED",
             "PRICE_UPDATED", "AD_BUDGET_CHANGED"))).all()
    assert not executed, f"execution events written: {[e.event_type for e in executed]}"
    print(f"  zero execution: founder_actions=0, execution events=0")

    # And the queue the founder actually reads shows the proposal.
    pend = mi.pending_founder_reviews(db)
    assert isinstance(pend, dict)
    print(f"  pending_founder_reviews: {pend.get('pending_count', len(recs))} awaiting")


def test_reevaluating_never_manufactures_consent(mi_db):
    """Whatever else re-evaluation does, it must never write consent."""
    db, mi = mi_db
    from app.models.marketplace_intel import MiRecommendation, SkuChannelMap
    from app.services.founder_actions import FounderAction
    sku = _sku(db, mi)
    cm = (db.query(SkuChannelMap)
          .filter(SkuChannelMap.sku_id == sku.id,
                  SkuChannelMap.channel == CHANNEL).first())
    cm.status = "ACTIVE"
    db.commit()

    for _ in range(3):
        mi.mi_evaluate(db, sku.id, CHANNEL, DATE)

    assert db.query(FounderAction).count() == 0, "consent written on re-evaluation"
    assert all(r.status == "FOUNDER_REVIEW" for r in db.query(MiRecommendation).all())
    print("\n  re-evaluate x3: founder_actions=0, all still FOUNDER_REVIEW")


@pytest.mark.xfail(
    reason="mi_evaluate appends a new mi_recommendation on every call: 5 "
           "evaluations of one SKU/channel/date produce 5 identical proposals. "
           "A scheduled evaluate_all_active would flood the founder's review "
           "queue with duplicates of the same decision. Not a safety defect "
           "(nothing executes, status stays FOUNDER_REVIEW) but it makes the "
           "queue unusable. Fix: upsert on (sku_id, channel, date), or "
           "supersede the previous open proposal.",
    strict=False)
def test_one_open_recommendation_per_sku_channel_date(mi_db):
    db, mi = mi_db
    from app.models.marketplace_intel import MiRecommendation, SkuChannelMap
    sku = _sku(db, mi)
    cm = (db.query(SkuChannelMap)
          .filter(SkuChannelMap.sku_id == sku.id,
                  SkuChannelMap.channel == CHANNEL).first())
    cm.status = "ACTIVE"
    db.commit()

    for _ in range(5):
        mi.mi_evaluate(db, sku.id, CHANNEL, DATE)

    rows = db.query(MiRecommendation).filter(
        MiRecommendation.sku_id == sku.id,
        MiRecommendation.channel == CHANNEL,
        MiRecommendation.date == DATE,
        MiRecommendation.status == "FOUNDER_REVIEW").all()
    assert len(rows) == 1, (
        f"{len(rows)} open proposals for one SKU/channel/date — the founder "
        f"would see the same decision {len(rows)} times")
