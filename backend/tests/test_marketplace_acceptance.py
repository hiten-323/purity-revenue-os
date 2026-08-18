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
from datetime import datetime, timedelta

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

CHANNEL = "amazon"
SKU_CODE = "PURISTA_50G"
DATE = "2026-08-18"
PREV = "2026-08-11"  # 7 days earlier — sales_delta baseline


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


def _activate_amazon(db, sku):
    from app.models.marketplace_intel import SkuChannelMap
    cm = (db.query(SkuChannelMap)
          .filter(SkuChannelMap.sku_id == sku.id,
                  SkuChannelMap.channel == CHANNEL).first())
    cm.status, cm.channel_sku_id = "ACTIVE", "B0TESTASIN"
    db.commit()
    return cm


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
        MiMetricDaily, MiRecommendation)

    sku = _sku(db, mi)
    _activate_amazon(db, sku)

    # ---- ingest -------------------------------------------------------
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

    m = mi.recompute_metrics(db, sku.id, CHANNEL, DATE)
    assert isinstance(m, MiMetricDaily)
    stored = (db.query(MiMetricDaily)
              .filter(MiMetricDaily.sku_id == sku.id,
                      MiMetricDaily.channel == CHANNEL,
                      MiMetricDaily.date == DATE).all())
    assert len(stored) == 1, f"expected 1 metric row, got {len(stored)}"

    res = mi.mi_evaluate(db, sku.id, CHANNEL, DATE)
    assert isinstance(res, dict)
    for key in ("metrics", "audit"):
        assert key in res, f"mi_evaluate result missing {key!r}"
    print(f"  evaluate: diagnosis={res.get('primary_diagnosis')} "
          f"action={res.get('recommended_action')} conf={res.get('confidence')}")

    recs = db.query(MiRecommendation).all()
    if res.get("recommended_action") not in (None, "NONE"):
        assert recs, "an action was recommended but no mi_recommendation row exists"
    for r in recs:
        assert r.status == "FOUNDER_REVIEW", (
            f"recommendation {r.id} status={r.status}, expected FOUNDER_REVIEW")

    from app.services.founder_actions import FounderAction
    from app.models.models import WorkflowEvent
    assert db.query(FounderAction).count() == 0, "the pipeline manufactured consent"
    executed = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(
            ("EMAIL_SENT", "WHATSAPP_SENT", "OUTREACH_APPROVED",
             "PRICE_UPDATED", "AD_BUDGET_CHANGED"))).all()
    assert not executed, f"execution events written: {[e.event_type for e in executed]}"

    pend = mi.pending_founder_reviews(db)
    assert isinstance(pend, dict)


def test_osa_low_with_complete_commercial_observations(mi_db):
    """
    Business acceptance: sales decline + OSA 80% + competitor OSA 100%
    → OSA_LOW → RESTORE_OSA_ABOVE_95 → FOUNDER_REVIEW → zero execution.

    Missing SOV stays NULL (not zero). Price gap is secondary while OSA is low.
    """
    db, mi = mi_db
    from app.models.marketplace_intel import (
        FpSalesDaily, MiPriceSnapshot, MiAvailability, MiRecommendation,
    )
    from app.services.founder_actions import FounderAction
    from app.models.models import WorkflowEvent

    sku = _sku(db, mi)
    _activate_amazon(db, sku)

    # Prior week baseline + current decline
    db.add(FpSalesDaily(date=PREV, channel=CHANNEL, sku_id=sku.id,
                        units=20, gmv_inr=6380.0, source="manual_csv"))
    db.add(FpSalesDaily(date=DATE, channel=CHANNEL, sku_id=sku.id,
                        units=12, gmv_inr=3828.0, source="manual_csv"))

    # Self OSA = 4/5 = 80% < 95
    for pin, stock in (
        ("110001", True),
        ("400001", True),
        ("560001", True),
        ("700001", True),
        ("600001", False),
    ):
        db.add(MiAvailability(
            channel=CHANNEL, sku_id=sku.id, pincode=pin,
            in_stock=stock, is_self=True, source="manual",
        ))
    # Competitor fully available
    db.add(MiAvailability(
        channel=CHANNEL, sku_id=None, pincode="110001",
        in_stock=True, is_self=False, source="manual",
    ))

    # Self + competitor price (gap secondary while OSA low)
    db.add(MiPriceSnapshot(
        channel=CHANNEL, sku_id=sku.id, price_inr=319.0,
        is_self=True, source="manual",
    ))
    db.add(MiPriceSnapshot(
        channel=CHANNEL, sku_id=None, price_inr=299.0,
        is_self=False, seller_name="comp_x", source="manual",
    ))
    db.commit()

    res = mi.mi_evaluate(db, sku.id, CHANNEL, DATE)

    assert res["metrics"]["osa_pct"] == 80.0, res["metrics"]
    assert res["metrics"]["competitor_osa_pct"] == 100.0, res["metrics"]
    assert res["metrics"]["sales_delta_pct"] is not None
    assert res["metrics"]["sales_delta_pct"] < 0
    # SOV not ingested — must stay NULL, never fabricated 0
    assert res["metrics"]["sov_pct"] is None

    assert res["primary_diagnosis"] == "OSA_LOW", res
    assert res["recommended_action"] == "RESTORE_OSA_ABOVE_95", res
    assert res["status"] == "FOUNDER_REVIEW"
    assert "OSA_LOW" in (res.get("blockers") or [])

    # Price gap may be secondary; never primary while OSA is broken
    if res.get("secondary_diagnosis"):
        assert res["secondary_diagnosis"] == "PRICE_UNDERCUT"

    open_rows = db.query(MiRecommendation).filter(
        MiRecommendation.status == "FOUNDER_REVIEW").all()
    assert len(open_rows) == 1
    assert open_rows[0].primary_diagnosis == "OSA_LOW"
    assert open_rows[0].recommended_action == "RESTORE_OSA_ABOVE_95"

    assert db.query(FounderAction).count() == 0
    executed = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(
            ("EMAIL_SENT", "WHATSAPP_SENT", "PRICE_UPDATED", "AD_BUDGET_CHANGED")
        )
    ).all()
    assert not executed

    # Re-evaluate must reuse the same open proposal
    res2 = mi.mi_evaluate(db, sku.id, CHANNEL, DATE)
    assert res2.get("recommendation_reused") is True
    assert res2["recommendation_id"] == res["recommendation_id"]
    assert db.query(MiRecommendation).filter(
        MiRecommendation.status == "FOUNDER_REVIEW").count() == 1

    print(
        f"\n  OSA_LOW path: osa={res['metrics']['osa_pct']}% "
        f"sales_delta={res['metrics']['sales_delta_pct']}% "
        f"→ {res['recommended_action']} FOUNDER_REVIEW id={res['recommendation_id']}"
    )


def test_reevaluating_never_manufactures_consent(mi_db):
    """Whatever else re-evaluation does, it must never write consent."""
    db, mi = mi_db
    from app.models.marketplace_intel import MiRecommendation
    from app.services.founder_actions import FounderAction
    sku = _sku(db, mi)
    _activate_amazon(db, sku)

    for _ in range(3):
        mi.mi_evaluate(db, sku.id, CHANNEL, DATE)

    assert db.query(FounderAction).count() == 0, "consent written on re-evaluation"
    assert all(r.status == "FOUNDER_REVIEW" for r in db.query(MiRecommendation).all())
    print("\n  re-evaluate x3: founder_actions=0, all still FOUNDER_REVIEW")


def test_one_open_recommendation_per_sku_channel_date(mi_db):
    """Re-evaluating must not grow the founder's queue."""
    db, mi = mi_db
    from app.models.marketplace_intel import MiRecommendation
    sku = _sku(db, mi)
    _activate_amazon(db, sku)

    ids = []
    for _ in range(5):
        r = mi.mi_evaluate(db, sku.id, CHANNEL, DATE)
        if r.get("recommendation_id"):
            ids.append(r["recommendation_id"])

    open_rows = db.query(MiRecommendation).filter(
        MiRecommendation.sku_id == sku.id,
        MiRecommendation.channel == CHANNEL,
        MiRecommendation.date == DATE,
        MiRecommendation.status == "FOUNDER_REVIEW").all()
    assert len(open_rows) == 1, (
        f"{len(open_rows)} open proposals for one SKU/channel/date")
    assert len(set(ids)) == 1, f"recommendation id churned across runs: {ids}"

    pend = mi.pending_founder_reviews(db)
    assert pend["pending_count"] == 1, pend["pending_count"]
    print(f"\n  5 evaluations -> {len(open_rows)} open proposal, "
          f"stable id={ids[0]}, queue shows {pend['pending_count']}")


def test_changed_decision_supersedes_rather_than_overwrites(mi_db):
    """A changed decision must stay inspectable, not silently replace the old."""
    db, mi = mi_db
    from app.models.marketplace_intel import MiRecommendation
    sku = _sku(db, mi)
    _activate_amazon(db, sku)

    mi.mi_evaluate(db, sku.id, CHANNEL, DATE)
    first = db.query(MiRecommendation).filter(
        MiRecommendation.status == "FOUNDER_REVIEW").one()

    first.recommended_action = "SOMETHING_ELSE"
    first.primary_diagnosis = "OTHER_DIAGNOSIS"
    db.commit()

    mi.mi_evaluate(db, sku.id, CHANNEL, DATE)

    open_rows = db.query(MiRecommendation).filter(
        MiRecommendation.status == "FOUNDER_REVIEW").all()
    superseded = db.query(MiRecommendation).filter(
        MiRecommendation.status == "SUPERSEDED").all()
    assert len(open_rows) == 1, f"{len(open_rows)} open after a changed decision"
    assert len(superseded) == 1, "the previous proposal was overwritten, not superseded"
    print(f"\n  changed decision -> 1 open, {len(superseded)} superseded")
