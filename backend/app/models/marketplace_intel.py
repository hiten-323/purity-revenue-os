"""
Marketplace Intelligence — Phase 0 schema.

First-party sales/ads/inventory are separate from market observations
(competitor price, OSA, SOV). Metrics that cannot be computed stay NULL
with source_status NOT_CONNECTED — never invented zeros.

This module does not decide or execute. mi_evaluate() in services produces
diagnoses; founder_actions / FOUNDER_REVIEW remains the human gate.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.database import Base


class SkuMaster(Base):
    __tablename__ = "sku_master"

    id = Column(Integer, primary_key=True, index=True)
    sku_code = Column(String, unique=True, nullable=False, index=True)
    brand = Column(String, default="Purity Beans")
    product_name = Column(String, nullable=False)
    pack_size_g = Column(Integer, nullable=True)
    mrp_inr = Column(Float, nullable=True)
    cost_inr = Column(Float, nullable=True)
    active = Column(Boolean, default=True)
    shopify_product_id = Column(String, nullable=True)
    shopify_variant_id = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    channel_maps = relationship("SkuChannelMap", back_populates="sku")


class SkuChannelMap(Base):
    __tablename__ = "sku_channel_map"
    __table_args__ = (
        UniqueConstraint("sku_id", "channel", name="uq_sku_channel"),
    )

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)  # shopify|amazon|flipkart|blinkit|zepto
    channel_sku_id = Column(String, nullable=True)        # ASIN / FSN / variant id
    listing_url = Column(String, nullable=True)
    status = Column(String, default="ACTIVE")             # ACTIVE|INACTIVE|NOT_LISTED

    sku = relationship("SkuMaster", back_populates="channel_maps")


class MarketplaceAccount(Base):
    __tablename__ = "marketplace_account"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String, unique=True, nullable=False)
    seller_name = Column(String, nullable=True)
    # Credential presence only — never store secrets here.
    credentials_configured = Column(Boolean, default=False)
    notes = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── First-party ──────────────────────────────────────────────────────────────

class FpSalesDaily(Base):
    __tablename__ = "fp_sales_daily"
    __table_args__ = (
        UniqueConstraint("date", "channel", "sku_id", name="uq_fp_sales_day"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False, index=True)
    units = Column(Integer, nullable=True)
    gmv_inr = Column(Float, nullable=True)
    net_revenue_inr = Column(Float, nullable=True)
    returns_units = Column(Integer, nullable=True)
    source = Column(String, default="manual_csv")  # shopify_api|manual_csv|seller_report


class FpAdsDaily(Base):
    __tablename__ = "fp_ads_daily"
    __table_args__ = (
        UniqueConstraint("date", "channel", "sku_id", name="uq_fp_ads_day"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=True, index=True)
    spend_inr = Column(Float, nullable=True)
    ad_sales_inr = Column(Float, nullable=True)
    clicks = Column(Integer, nullable=True)
    impressions = Column(Integer, nullable=True)
    source = Column(String, default="manual_csv")


class FpInventory(Base):
    __tablename__ = "fp_inventory"

    id = Column(Integer, primary_key=True, index=True)
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False, index=True)
    on_hand = Column(Integer, nullable=True)
    inbound = Column(Integer, nullable=True)
    reserved = Column(Integer, nullable=True)
    dark_store_id = Column(String, nullable=True)  # q-commerce locality
    source = Column(String, default="manual_csv")


class FpOrder(Base):
    __tablename__ = "fp_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=True, index=True)
    units = Column(Integer, nullable=True)
    amount_inr = Column(Float, nullable=True)
    pincode = Column(String, nullable=True)
    status = Column(String, nullable=True)
    ordered_at = Column(DateTime, nullable=True)
    source = Column(String, default="shopify_api")


# ── Market intelligence ──────────────────────────────────────────────────────

class MiPriceSnapshot(Base):
    __tablename__ = "mi_price_snapshot"

    id = Column(Integer, primary_key=True, index=True)
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=True, index=True)
    competitor_sku_id = Column(Integer, ForeignKey("mi_competitor_sku.id"), nullable=True)
    seller_name = Column(String, nullable=True)
    price_inr = Column(Float, nullable=True)
    mrp_inr = Column(Float, nullable=True)
    discount_pct = Column(Float, nullable=True)
    is_self = Column(Boolean, default=False)
    source = Column(String, default="manual")


class MiAvailability(Base):
    __tablename__ = "mi_availability"

    id = Column(Integer, primary_key=True, index=True)
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=True, index=True)
    competitor_sku_id = Column(Integer, ForeignKey("mi_competitor_sku.id"), nullable=True)
    pincode = Column(String, nullable=True, index=True)
    locality = Column(String, nullable=True)
    in_stock = Column(Boolean, nullable=True)  # NULL = unknown, not False
    eta_minutes = Column(Integer, nullable=True)
    is_self = Column(Boolean, default=False)
    source = Column(String, default="manual")


class MiSearchRank(Base):
    __tablename__ = "mi_search_rank"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    keyword = Column(String, nullable=False)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=True, index=True)
    rank = Column(Integer, nullable=True)
    page = Column(Integer, nullable=True)
    placement = Column(String, nullable=True)  # organic|sponsored
    source = Column(String, default="manual")


class MiCompetitorSku(Base):
    __tablename__ = "mi_competitor_sku"

    id = Column(Integer, primary_key=True, index=True)
    brand = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    pack_size_g = Column(Integer, nullable=True)
    channel = Column(String, nullable=True)
    channel_sku_id = Column(String, nullable=True)
    notes = Column(String, nullable=True)


class MiSovDaily(Base):
    __tablename__ = "mi_sov_daily"
    __table_args__ = (
        UniqueConstraint("date", "channel", "keyword", name="uq_mi_sov_day"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    keyword = Column(String, nullable=False)
    self_sov_pct = Column(Float, nullable=True)  # NULL if unknown
    top_competitors = Column(JSON, nullable=True)
    source = Column(String, default="manual")


class MiPromo(Base):
    __tablename__ = "mi_promo"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=True)
    competitor_sku_id = Column(Integer, ForeignKey("mi_competitor_sku.id"), nullable=True)
    deal_type = Column(String, nullable=True)
    discount_pct = Column(Float, nullable=True)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    is_self = Column(Boolean, default=False)
    source = Column(String, default="manual")


class MiMetricDaily(Base):
    """
    Computed metrics. Any field may be NULL = we do not know.
    source_status documents coverage; never coerce missing → 0.
    """
    __tablename__ = "mi_metric_daily"
    __table_args__ = (
        UniqueConstraint("date", "channel", "sku_id", name="uq_mi_metric_day"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False, index=True)

    units = Column(Integer, nullable=True)
    gmv_inr = Column(Float, nullable=True)
    sales_delta_pct = Column(Float, nullable=True)
    osa_pct = Column(Float, nullable=True)
    competitor_osa_pct = Column(Float, nullable=True)
    avg_price_inr = Column(Float, nullable=True)
    competitor_min_price_inr = Column(Float, nullable=True)
    price_gap_inr = Column(Float, nullable=True)
    sov_pct = Column(Float, nullable=True)
    sov_delta_pct = Column(Float, nullable=True)
    tacos_pct = Column(Float, nullable=True)
    tacos_delta_pct = Column(Float, nullable=True)

    source_status = Column(String, default="PARTIAL")  # COMPLETE|PARTIAL|NOT_CONNECTED
    missing_feeds = Column(JSON, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow)


class MiRecommendation(Base):
    """
    Proposed marketplace action for founder review.
    System writes these; founder consent is separate (founder_actions).
    """
    __tablename__ = "mi_recommendation"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    date = Column(String, nullable=False, index=True)
    primary_diagnosis = Column(String, nullable=False)
    secondary_diagnosis = Column(String, nullable=True)
    recommended_action = Column(String, nullable=False)
    status = Column(String, default="FOUNDER_REVIEW", index=True)
    confidence = Column(Integer, default=0)
    evidence = Column(JSON, nullable=True)
    metrics_snapshot = Column(JSON, nullable=True)
    blockers = Column(JSON, nullable=True)
    audit = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
