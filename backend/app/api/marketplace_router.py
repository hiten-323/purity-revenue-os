"""
Marketplace Intelligence API — Phase 0.

Facts and founder-review recommendations only. No autonomous price/ad execution.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.marketplace_intel import (
    FpAdsDaily,
    FpSalesDaily,
    MiAvailability,
    MiPriceSnapshot,
    MiRecommendation,
    SkuMaster,
)
from app.services import marketplace_intel as mi

router = APIRouter(prefix="/marketplace", tags=["marketplace-intelligence"])


class EvaluateBody(BaseModel):
    sku_code: str
    channel: str = "amazon"
    date: str | None = None


class SalesIngestRow(BaseModel):
    date: str
    channel: str
    sku_code: str
    units: int | None = None
    gmv_inr: float | None = None
    net_revenue_inr: float | None = None
    source: str = "manual_csv"


class SalesIngestBody(BaseModel):
    rows: list[SalesIngestRow] = Field(default_factory=list)


class AvailabilityIngestRow(BaseModel):
    channel: str
    sku_code: str | None = None
    pincode: str | None = None
    in_stock: bool | None = None
    is_self: bool = True
    source: str = "manual"


class AvailabilityIngestBody(BaseModel):
    rows: list[AvailabilityIngestRow] = Field(default_factory=list)


class PriceIngestRow(BaseModel):
    channel: str
    sku_code: str | None = None
    price_inr: float | None = None
    is_self: bool = True
    seller_name: str | None = None
    source: str = "manual"


class PriceIngestBody(BaseModel):
    rows: list[PriceIngestRow] = Field(default_factory=list)


class AdsIngestRow(BaseModel):
    date: str
    channel: str
    sku_code: str
    spend_inr: float | None = None
    ad_sales_inr: float | None = None
    source: str = "manual_csv"


class AdsIngestBody(BaseModel):
    rows: list[AdsIngestRow] = Field(default_factory=list)


def _sku_by_code(db: Session, code: str) -> SkuMaster:
    sku = db.query(SkuMaster).filter(SkuMaster.sku_code == code.upper()).first()
    if not sku:
        sku = db.query(SkuMaster).filter(SkuMaster.sku_code == code).first()
    if not sku:
        raise HTTPException(404, f"sku_code {code} not in sku_master — run POST /marketplace/seed")
    return sku


@router.post("/seed")
def post_seed(db: Session = Depends(get_db)):
    """Idempotent seed of live Purity Beans SKUs + channel map placeholders."""
    mi.ensure_tables(db.get_bind())
    return mi.seed_skus(db)


@router.get("/skus")
def get_skus(db: Session = Depends(get_db)):
    rows = db.query(SkuMaster).filter(SkuMaster.active.is_(True)).all()
    return {
        "count": len(rows),
        "skus": [
            {
                "id": s.id,
                "sku_code": s.sku_code,
                "product_name": s.product_name,
                "pack_size_g": s.pack_size_g,
                "mrp_inr": s.mrp_inr,
                "shopify_variant_id": s.shopify_variant_id,
            }
            for s in rows
        ],
    }


@router.post("/ingest/sales")
def post_ingest_sales(body: SalesIngestBody, db: Session = Depends(get_db)):
    n = 0
    for r in body.rows:
        sku = _sku_by_code(db, r.sku_code)
        existing = (
            db.query(FpSalesDaily)
            .filter(
                FpSalesDaily.date == r.date,
                FpSalesDaily.channel == r.channel,
                FpSalesDaily.sku_id == sku.id,
            )
            .first()
        )
        if existing:
            existing.units = r.units
            existing.gmv_inr = r.gmv_inr
            existing.net_revenue_inr = r.net_revenue_inr
            existing.source = r.source
        else:
            db.add(
                FpSalesDaily(
                    date=r.date,
                    channel=r.channel,
                    sku_id=sku.id,
                    units=r.units,
                    gmv_inr=r.gmv_inr,
                    net_revenue_inr=r.net_revenue_inr,
                    source=r.source,
                )
            )
        n += 1
    db.commit()
    return {"upserted": n, "note": "first-party only — does not invent market metrics"}


@router.post("/ingest/ads")
def post_ingest_ads(body: AdsIngestBody, db: Session = Depends(get_db)):
    n = 0
    for r in body.rows:
        sku = _sku_by_code(db, r.sku_code)
        existing = (
            db.query(FpAdsDaily)
            .filter(
                FpAdsDaily.date == r.date,
                FpAdsDaily.channel == r.channel,
                FpAdsDaily.sku_id == sku.id,
            )
            .first()
        )
        if existing:
            existing.spend_inr = r.spend_inr
            existing.ad_sales_inr = r.ad_sales_inr
            existing.source = r.source
        else:
            db.add(
                FpAdsDaily(
                    date=r.date,
                    channel=r.channel,
                    sku_id=sku.id,
                    spend_inr=r.spend_inr,
                    ad_sales_inr=r.ad_sales_inr,
                    source=r.source,
                )
            )
        n += 1
    db.commit()
    return {"upserted": n}


@router.post("/ingest/availability")
def post_ingest_availability(body: AvailabilityIngestBody, db: Session = Depends(get_db)):
    n = 0
    for r in body.rows:
        sku_id = None
        if r.sku_code:
            sku_id = _sku_by_code(db, r.sku_code).id
        db.add(
            MiAvailability(
                channel=r.channel,
                sku_id=sku_id,
                pincode=r.pincode,
                in_stock=r.in_stock,
                is_self=r.is_self,
                source=r.source,
            )
        )
        n += 1
    db.commit()
    return {"inserted": n, "note": "in_stock=null means unknown, not out of stock"}


@router.post("/ingest/prices")
def post_ingest_prices(body: PriceIngestBody, db: Session = Depends(get_db)):
    n = 0
    for r in body.rows:
        sku_id = None
        if r.sku_code:
            sku_id = _sku_by_code(db, r.sku_code).id
        db.add(
            MiPriceSnapshot(
                channel=r.channel,
                sku_id=sku_id,
                price_inr=r.price_inr,
                is_self=r.is_self,
                seller_name=r.seller_name,
                source=r.source,
            )
        )
        n += 1
    db.commit()
    return {"inserted": n}


@router.post("/evaluate")
def post_evaluate(body: EvaluateBody, db: Session = Depends(get_db)):
    sku = _sku_by_code(db, body.sku_code)
    return mi.mi_evaluate(db, sku.id, body.channel, body.date)


@router.post("/evaluate-all")
def post_evaluate_all(channel: str = "amazon", date: str | None = None, db: Session = Depends(get_db)):
    return mi.evaluate_all_active(db, channel=channel, date=date)


@router.get("/pending")
def get_pending(limit: int = 40, db: Session = Depends(get_db)):
    """Marketplace recommendations waiting on founder review."""
    return mi.pending_founder_reviews(db, limit=limit)


@router.post("/recommendations/{rec_id}/dismiss")
def dismiss_recommendation(rec_id: int, note: str = "", db: Session = Depends(get_db)):
    """Founder rejects / dismisses a recommendation without executing."""
    row = db.query(MiRecommendation).filter(MiRecommendation.id == rec_id).first()
    if not row:
        raise HTTPException(404, f"recommendation {rec_id} not found")
    row.status = "DISMISSED"
    db.commit()
    return {"id": rec_id, "status": "DISMISSED", "note": note or None}
