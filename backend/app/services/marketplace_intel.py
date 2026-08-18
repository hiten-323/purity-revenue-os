"""
Marketplace Intelligence — Phase 0.

mi_evaluate() returns facts, diagnoses, confidence, blockers, recommendation.
It does NOT execute pricing, ads, or inventory changes.

Action hierarchy (never recommend ads while OSA is broken):

  DATA_GAP → FIX_DATA
       ↓
  OSA_LOW → RESTORE_OSA_ABOVE_95
       ↓
  PRICE_UNDERCUT → TEST_PRICE_CORRECTION (founder)
       ↓
  SOV_DROP → REVIEW_VISIBILITY (founder)
       ↓
  TACOS_SPIKE → TRIM_INEFFICIENT_ADS (founder)

No autonomous growth optimizer in Phase 0.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from app.models.marketplace_intel import (
    FpAdsDaily,
    FpSalesDaily,
    MiAvailability,
    MiMetricDaily,
    MiPriceSnapshot,
    MiRecommendation,
    MiSovDaily,
    SkuChannelMap,
    SkuMaster,
)

# Thresholds — explicit, tunable later from evidence not intuition alone.
OSA_HEALTHY = 95.0
PRICE_GAP_ALERT_INR = 15.0
SOV_DROP_ALERT_PCT = 8.0
TACOS_SPIKE_ALERT_PCT = 3.0
SALES_DROP_ALERT_PCT = -10.0

DIAGNOSES = ("DATA_GAP", "OSA_LOW", "PRICE_UNDERCUT", "SOV_DROP", "TACOS_SPIKE")

# Live Shopify catalogue (Aug 2026) — active single-SKU packs only (not bundles).
SEED_SKUS = [
    {
        "sku_code": "PURICA_50G",
        "product_name": "Purica Freeze Dried Arabica Instant Coffee 50g",
        "pack_size_g": 50,
        "mrp_inr": 329.0,
        "shopify_product_id": "8358286459067",
        "shopify_variant_id": "47583629050043",
    },
    {
        "sku_code": "PURICA_100G",
        "product_name": "Purica Freeze Dried Arabica Instant Coffee 100g",
        "pack_size_g": 100,
        "mrp_inr": 559.0,
        "shopify_product_id": "8358286459067",
        "shopify_variant_id": "47583629082811",
    },
    {
        "sku_code": "PURISTA_50G",
        "product_name": "Purista Freeze Dried Robusta Instant Coffee 50g",
        "pack_size_g": 50,
        "mrp_inr": 319.0,
        "shopify_product_id": "8358285246651",
        "shopify_variant_id": "47583626789051",
    },
    {
        "sku_code": "PURISTA_100G",
        "product_name": "Purista Freeze Dried Robusta Instant Coffee 100g",
        "pack_size_g": 100,
        "mrp_inr": 509.0,
        "shopify_product_id": "8358285246651",
        "shopify_variant_id": "47583626821819",
    },
    {
        "sku_code": "BOLD_50G",
        "product_name": "Bold Pure Instant Coffee 50g",
        "pack_size_g": 50,
        "mrp_inr": 239.0,
        "shopify_product_id": "8358285115579",
        "shopify_variant_id": "47583626363067",
    },
    {
        "sku_code": "BOLD_100G",
        "product_name": "Bold Pure Instant Coffee 100g",
        "pack_size_g": 100,
        "mrp_inr": 369.0,
        "shopify_product_id": "8358285115579",
        "shopify_variant_id": "47583626395835",
    },
    {
        "sku_code": "ULTRA_50G",
        "product_name": "Ultra Blend Premium Instant Coffee 50g",
        "pack_size_g": 50,
        "mrp_inr": 209.0,
        "shopify_product_id": "8358284918971",
        "shopify_variant_id": "47583625806011",
    },
    {
        "sku_code": "ULTRA_100G",
        "product_name": "Ultra Blend Premium Instant Coffee 100g",
        "pack_size_g": 100,
        "mrp_inr": 309.0,
        "shopify_product_id": "8358284918971",
        "shopify_variant_id": "47583625838779",
    },
]


def ensure_tables(engine) -> None:
    from app.models import marketplace_intel as _mi  # noqa: F401
    from app.database.database import Base

    Base.metadata.create_all(bind=engine)


def seed_skus(db) -> dict:
    """Idempotent seed of active Purity Beans SKUs + Shopify channel map."""
    inserted = 0
    for row in SEED_SKUS:
        existing = db.query(SkuMaster).filter(SkuMaster.sku_code == row["sku_code"]).first()
        if existing:
            sku = existing
        else:
            sku = SkuMaster(
                sku_code=row["sku_code"],
                product_name=row["product_name"],
                pack_size_g=row["pack_size_g"],
                mrp_inr=row["mrp_inr"],
                shopify_product_id=row["shopify_product_id"],
                shopify_variant_id=row["shopify_variant_id"],
                active=True,
            )
            db.add(sku)
            db.flush()
            inserted += 1
        cmap = (
            db.query(SkuChannelMap)
            .filter(SkuChannelMap.sku_id == sku.id, SkuChannelMap.channel == "shopify")
            .first()
        )
        if not cmap:
            db.add(
                SkuChannelMap(
                    sku_id=sku.id,
                    channel="shopify",
                    channel_sku_id=row["shopify_variant_id"],
                    listing_url=None,
                    status="ACTIVE",
                )
            )
        # Placeholder maps for other channels — NOT_LISTED until real IDs exist.
        for ch in ("amazon", "flipkart", "blinkit", "zepto"):
            if not (
                db.query(SkuChannelMap)
                .filter(SkuChannelMap.sku_id == sku.id, SkuChannelMap.channel == ch)
                .first()
            ):
                db.add(
                    SkuChannelMap(
                        sku_id=sku.id,
                        channel=ch,
                        channel_sku_id=None,
                        status="NOT_LISTED",
                    )
                )
    db.commit()
    return {"inserted": inserted, "total": db.query(SkuMaster).count()}


def _pct(n: Optional[float], d: Optional[float]) -> Optional[float]:
    if n is None or d is None or d == 0:
        return None
    return round((n / d) * 100.0, 2)


def _delta_pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev == 0:
        return None
    return round(((cur - prev) / prev) * 100.0, 2)


def recompute_metrics(db, sku_id: int, channel: str, date: str) -> MiMetricDaily:
    """
    Build/refresh mi_metric_daily for one sku×channel×day.
    Missing observations → NULL fields + missing_feeds list.
    """
    missing: list[str] = []

    sales = (
        db.query(FpSalesDaily)
        .filter(
            FpSalesDaily.sku_id == sku_id,
            FpSalesDaily.channel == channel,
            FpSalesDaily.date == date,
        )
        .first()
    )
    units = sales.units if sales else None
    gmv = sales.gmv_inr if sales else None
    if sales is None:
        missing.append("fp_sales_daily")

    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        prev_date = (d - timedelta(days=7)).strftime("%Y-%m-%d")
    except ValueError:
        prev_date = date
    prev_sales = (
        db.query(FpSalesDaily)
        .filter(
            FpSalesDaily.sku_id == sku_id,
            FpSalesDaily.channel == channel,
            FpSalesDaily.date == prev_date,
        )
        .first()
    )
    sales_delta = _delta_pct(
        float(units) if units is not None else None,
        float(prev_sales.units) if prev_sales and prev_sales.units is not None else None,
    )

    # OSA from availability checks in last 7 days ending on date
    self_avail = (
        db.query(MiAvailability)
        .filter(
            MiAvailability.sku_id == sku_id,
            MiAvailability.channel == channel,
            MiAvailability.is_self.is_(True),
            MiAvailability.in_stock.isnot(None),
        )
        .all()
    )
    osa = None
    if self_avail:
        known = [a for a in self_avail if a.in_stock is not None]
        if known:
            osa = round(100.0 * sum(1 for a in known if a.in_stock) / len(known), 2)
    else:
        missing.append("mi_availability_self")

    comp_avail = (
        db.query(MiAvailability)
        .filter(
            MiAvailability.channel == channel,
            MiAvailability.is_self.is_(False),
            MiAvailability.in_stock.isnot(None),
        )
        .all()
    )
    competitor_osa = None
    if comp_avail:
        competitor_osa = round(
            100.0 * sum(1 for a in comp_avail if a.in_stock) / len(comp_avail), 2
        )

    self_prices = (
        db.query(MiPriceSnapshot)
        .filter(
            MiPriceSnapshot.sku_id == sku_id,
            MiPriceSnapshot.channel == channel,
            MiPriceSnapshot.is_self.is_(True),
            MiPriceSnapshot.price_inr.isnot(None),
        )
        .all()
    )
    avg_price = (
        round(sum(p.price_inr for p in self_prices) / len(self_prices), 2)
        if self_prices
        else None
    )
    if not self_prices:
        missing.append("mi_price_self")

    comp_prices = (
        db.query(MiPriceSnapshot)
        .filter(
            MiPriceSnapshot.channel == channel,
            MiPriceSnapshot.is_self.is_(False),
            MiPriceSnapshot.price_inr.isnot(None),
        )
        .all()
    )
    competitor_min = min((p.price_inr for p in comp_prices), default=None)
    if competitor_min is None:
        missing.append("mi_price_competitor")
    price_gap = (
        round(avg_price - competitor_min, 2)
        if avg_price is not None and competitor_min is not None
        else None
    )

    sov_row = (
        db.query(MiSovDaily)
        .filter(MiSovDaily.channel == channel, MiSovDaily.date == date)
        .first()
    )
    sov = sov_row.self_sov_pct if sov_row else None
    if sov is None:
        missing.append("mi_sov_daily")

    prev_sov = (
        db.query(MiSovDaily)
        .filter(MiSovDaily.channel == channel, MiSovDaily.date == prev_date)
        .first()
    )
    sov_delta = _delta_pct(
        sov, prev_sov.self_sov_pct if prev_sov else None
    )

    ads = (
        db.query(FpAdsDaily)
        .filter(
            FpAdsDaily.sku_id == sku_id,
            FpAdsDaily.channel == channel,
            FpAdsDaily.date == date,
        )
        .first()
    )
    tacos = None
    if ads and ads.spend_inr is not None and gmv and gmv > 0:
        tacos = round((ads.spend_inr / gmv) * 100.0, 2)
    elif ads is None:
        missing.append("fp_ads_daily")

    prev_ads = (
        db.query(FpAdsDaily)
        .filter(
            FpAdsDaily.sku_id == sku_id,
            FpAdsDaily.channel == channel,
            FpAdsDaily.date == prev_date,
        )
        .first()
    )
    prev_gmv = prev_sales.gmv_inr if prev_sales else None
    prev_tacos = None
    if prev_ads and prev_ads.spend_inr is not None and prev_gmv and prev_gmv > 0:
        prev_tacos = (prev_ads.spend_inr / prev_gmv) * 100.0
    tacos_delta = (
        round(tacos - prev_tacos, 2)
        if tacos is not None and prev_tacos is not None
        else None
    )

    if not missing:
        status = "COMPLETE"
    elif len(missing) >= 4:
        status = "NOT_CONNECTED"
    else:
        status = "PARTIAL"

    row = (
        db.query(MiMetricDaily)
        .filter(
            MiMetricDaily.date == date,
            MiMetricDaily.channel == channel,
            MiMetricDaily.sku_id == sku_id,
        )
        .first()
    )
    if not row:
        row = MiMetricDaily(date=date, channel=channel, sku_id=sku_id)
        db.add(row)

    row.units = units
    row.gmv_inr = gmv
    row.sales_delta_pct = sales_delta
    row.osa_pct = osa
    row.competitor_osa_pct = competitor_osa
    row.avg_price_inr = avg_price
    row.competitor_min_price_inr = competitor_min
    row.price_gap_inr = price_gap
    row.sov_pct = sov
    row.sov_delta_pct = sov_delta
    row.tacos_pct = tacos
    row.tacos_delta_pct = tacos_delta
    row.source_status = status
    row.missing_feeds = missing
    row.computed_at = datetime.utcnow()
    db.commit()
    return row


def mi_evaluate(db, sku_id: int, channel: str, date: Optional[str] = None) -> dict[str, Any]:
    """
    Single evaluation entrypoint. Facts only — no execution.

    Returns metrics, diagnoses, recommendation, blockers, confidence, audit.
    Recommendation status is always FOUNDER_REVIEW when an action is proposed.
    """
    date = date or datetime.utcnow().strftime("%Y-%m-%d")
    sku = db.query(SkuMaster).filter(SkuMaster.id == sku_id).first()
    if not sku:
        return {
            "action": "NONE",
            "primary_diagnosis": "DATA_GAP",
            "reason": "sku not found",
            "status": "FOUNDER_REVIEW",
            "blockers": ["DATA_GAP"],
            "confidence": 100,
            "metrics": {},
            "audit": ["sku_id missing from sku_master"],
        }

    m = recompute_metrics(db, sku_id, channel, date)
    audit: list[str] = [
        f"sku={sku.sku_code} channel={channel} date={date}",
        f"source_status={m.source_status}",
        f"missing={m.missing_feeds or []}",
    ]
    metrics = {
        "units": m.units,
        "gmv_inr": m.gmv_inr,
        "sales_delta_pct": m.sales_delta_pct,
        "osa_pct": m.osa_pct,
        "competitor_osa_pct": m.competitor_osa_pct,
        "avg_price_inr": m.avg_price_inr,
        "competitor_min_price_inr": m.competitor_min_price_inr,
        "price_gap_inr": m.price_gap_inr,
        "sov_pct": m.sov_pct,
        "sov_delta_pct": m.sov_delta_pct,
        "tacos_pct": m.tacos_pct,
        "tacos_delta_pct": m.tacos_delta_pct,
        "source_status": m.source_status,
        "missing_feeds": m.missing_feeds or [],
    }

    # Hierarchy: DATA_GAP → OSA → PRICE → SOV → TACOS
    primary = None
    secondary = None
    action = "NONE"
    blockers: list[str] = []
    evidence: list[str] = []
    confidence = 40

    # DATA_GAP only when commercial signals are absent. Missing SOV/ads alone
    # must not suppress a known OSA — otherwise sales+availability always
    # becomes FIX_DATA and the hierarchy never reaches OSA_LOW.
    has_commercial_signal = any(
        v is not None
        for v in (
            m.osa_pct,
            m.units,
            m.price_gap_inr,
            m.sov_delta_pct,
            m.tacos_delta_pct,
        )
    )
    hard_data_gap = m.source_status == "NOT_CONNECTED" and not has_commercial_signal
    soft_data_gap = (
        bool(m.missing_feeds)
        and len(m.missing_feeds) >= 3
        and m.units is None
        and m.osa_pct is None
    )

    if hard_data_gap or soft_data_gap:
        primary = "DATA_GAP"
        action = "FIX_DATA"
        blockers.append("DATA_GAP")
        evidence.append(
            f"Missing feeds: {', '.join(m.missing_feeds or ['all'])}. "
            "Do not invent OSA/SOV/price; connect reports first."
        )
        confidence = 95
        audit.append("DATA_GAP outranks every commercial hypothesis")
    else:
        if m.source_status == "NOT_CONNECTED" and has_commercial_signal:
            audit.append(
                "source_status=NOT_CONNECTED but commercial signals present — "
                "evaluate hierarchy on known metrics; missing feeds stay NULL"
            )

        if m.osa_pct is not None and m.osa_pct < OSA_HEALTHY:
            primary = "OSA_LOW"
            action = "RESTORE_OSA_ABOVE_95"
            blockers.append("OSA_LOW")
            evidence.append(
                f"OSA {m.osa_pct}% < {OSA_HEALTHY}%"
                + (
                    f"; competitor OSA {m.competitor_osa_pct}%"
                    if m.competitor_osa_pct is not None
                    else ""
                )
            )
            confidence = 85
            audit.append("OSA before price/ads — never scale spend on a stockout")

        if (
            m.price_gap_inr is not None
            and m.price_gap_inr >= PRICE_GAP_ALERT_INR
            and (m.sales_delta_pct is None or m.sales_delta_pct <= 0)
        ):
            if primary is None:
                primary = "PRICE_UNDERCUT"
                action = "TEST_PRICE_CORRECTION"
            else:
                secondary = "PRICE_UNDERCUT"
            evidence.append(f"Price gap +₹{m.price_gap_inr} vs cheapest observed competitor")
            confidence = max(confidence, 70)
            audit.append("price gap is secondary while OSA is low")

        if m.sov_delta_pct is not None and m.sov_delta_pct <= -SOV_DROP_ALERT_PCT:
            if primary is None:
                primary = "SOV_DROP"
                action = "REVIEW_VISIBILITY"
            elif secondary is None:
                secondary = "SOV_DROP"
            evidence.append(f"SOV Δ {m.sov_delta_pct}%")
            confidence = max(confidence, 65)

        if m.tacos_delta_pct is not None and m.tacos_delta_pct >= TACOS_SPIKE_ALERT_PCT:
            if primary is None:
                primary = "TACOS_SPIKE"
                action = "TRIM_INEFFICIENT_ADS"
            elif secondary is None:
                secondary = "TACOS_SPIKE"
            evidence.append(f"TACOS Δ +{m.tacos_delta_pct} pp")
            confidence = max(confidence, 65)

        if (
            primary is None
            and m.sales_delta_pct is not None
            and m.sales_delta_pct <= SALES_DROP_ALERT_PCT
        ):
            # Sales dropped but no diagnostic signal — still a data/review issue.
            primary = "DATA_GAP"
            action = "FIX_DATA"
            blockers.append("DATA_GAP")
            evidence.append(
                f"Sales Δ {m.sales_delta_pct}% without OSA/price/SOV/TACOS signal — "
                "need more observations before acting"
            )
            confidence = 60
            audit.append("sales drop without causal metrics → FIX_DATA, not ads")

    if primary is None:
        primary = "DATA_GAP" if m.source_status != "COMPLETE" else "NONE"
        action = "NONE" if primary == "NONE" else "FIX_DATA"
        if primary == "DATA_GAP":
            blockers.append("DATA_GAP")
        evidence.append("No actionable diagnosis under current thresholds")
        confidence = 50 if primary == "NONE" else 70

    # Plain-language summary — the operational product of this module.
    summary_parts = [f"{sku.sku_code} / {channel}"]
    if m.sales_delta_pct is not None:
        summary_parts.append(f"Sales Δ {m.sales_delta_pct}%")
    if m.osa_pct is not None:
        summary_parts.append(f"OSA {m.osa_pct}%")
    if m.competitor_osa_pct is not None:
        summary_parts.append(f"Competitor OSA {m.competitor_osa_pct}%")
    if m.price_gap_inr is not None:
        summary_parts.append(f"Price gap ₹{m.price_gap_inr}")
    if m.sov_delta_pct is not None:
        summary_parts.append(f"SOV Δ {m.sov_delta_pct}%")

    result = {
        "sku_id": sku_id,
        "sku_code": sku.sku_code,
        "channel": channel,
        "date": date,
        "metrics": metrics,
        "primary_diagnosis": primary,
        "secondary_diagnosis": secondary,
        "recommended_action": action,
        "status": "FOUNDER_REVIEW" if action not in ("NONE",) else "NO_ACTION",
        "confidence": confidence,
        "blockers": blockers,
        "evidence": evidence,
        "audit": audit,
        "summary": "; ".join(summary_parts),
        "hierarchy": [
            "DATA_GAP",
            "OSA_LOW",
            "PRICE_UNDERCUT",
            "SOV_DROP",
            "TACOS_SPIKE",
        ],
        "note": (
            "System proposes only. Founder approves before any price, ad, or "
            "inventory execution. evaluate_next_action remains B2B authority; "
            "this path never auto-spends."
        ),
    }

    if action not in ("NONE",):
        # One OPEN proposal per (sku, channel, date).
        #
        # Unchanged decision  -> refresh evidence on the existing row.
        # Changed decision    -> SUPERSEDE old, open new (history preserved).
        existing = (
            db.query(MiRecommendation)
            .filter(
                MiRecommendation.sku_id == sku_id,
                MiRecommendation.channel == channel,
                MiRecommendation.date == date,
                MiRecommendation.status == "FOUNDER_REVIEW",
            )
            .order_by(MiRecommendation.created_at.desc())
            .all()
        )

        same = [
            r for r in existing
            if r.recommended_action == action
            and (r.primary_diagnosis or "") == (primary or "DATA_GAP")
        ]

        if same:
            keep = same[0]
            keep.confidence = confidence
            keep.secondary_diagnosis = secondary
            keep.evidence = evidence
            keep.metrics_snapshot = metrics
            keep.blockers = blockers
            keep.audit = audit
            for dupe in existing:
                if dupe.id != keep.id:
                    dupe.status = "SUPERSEDED"
            db.commit()
            result["recommendation_id"] = keep.id
            result["recommendation_reused"] = True
        else:
            for stale in existing:
                stale.status = "SUPERSEDED"
            rec = MiRecommendation(
                sku_id=sku_id,
                channel=channel,
                date=date,
                primary_diagnosis=primary or "DATA_GAP",
                secondary_diagnosis=secondary,
                recommended_action=action,
                status="FOUNDER_REVIEW",
                confidence=confidence,
                evidence=evidence,
                metrics_snapshot=metrics,
                blockers=blockers,
                audit=audit,
            )
            db.add(rec)
            db.commit()
            result["recommendation_id"] = rec.id
            result["recommendation_reused"] = False
            if existing:
                result["superseded"] = [r.id for r in existing]

    return result


def evaluate_all_active(db, channel: str = "amazon", date: Optional[str] = None) -> dict:
    """Run mi_evaluate for every active SKU on one channel."""
    date = date or datetime.utcnow().strftime("%Y-%m-%d")
    skus = db.query(SkuMaster).filter(SkuMaster.active.is_(True)).all()
    results = [mi_evaluate(db, s.id, channel, date) for s in skus]
    by_diag: dict[str, int] = {}
    for r in results:
        d = r.get("primary_diagnosis") or "NONE"
        by_diag[d] = by_diag.get(d, 0) + 1
    return {
        "date": date,
        "channel": channel,
        "evaluated": len(results),
        "by_diagnosis": by_diag,
        "results": results,
    }


def pending_founder_reviews(db, limit: int = 40) -> dict:
    rows = (
        db.query(MiRecommendation)
        .filter(MiRecommendation.status == "FOUNDER_REVIEW")
        .order_by(MiRecommendation.created_at.desc())
        .limit(limit)
        .all()
    )
    items = []
    for r in rows:
        sku = db.query(SkuMaster).filter(SkuMaster.id == r.sku_id).first()
        items.append(
            {
                "id": r.id,
                "sku_code": sku.sku_code if sku else None,
                "product_name": sku.product_name if sku else None,
                "channel": r.channel,
                "date": r.date,
                "primary_diagnosis": r.primary_diagnosis,
                "secondary_diagnosis": r.secondary_diagnosis,
                "recommended_action": r.recommended_action,
                "confidence": r.confidence,
                "evidence": r.evidence,
                "metrics": r.metrics_snapshot,
                "status": r.status,
                "founder_action_required": "Approve or reject before execution",
            }
        )
    return {
        "pending_count": len(items),
        "role": "Founder reviews marketplace recommendations. System does not auto-execute.",
        "items": items,
    }
