from pydantic import BaseModel
from typing import List, Dict, Any

class VPSummary(BaseModel):
    best_marketplace: str
    highest_growth: str
    highest_risk: str

class FounderDashboardResponse(BaseModel):
    total_revenue: float
    marketplace_revenue: float
    b2b_pipeline: float
    tender_opportunities: float
    b2b_hot_leads: int
    b2b_corporate_opps: int
    total_profit: float
    inventory_risk: str
    growth_rate: str
    top_opportunity: str
    top_threat: str
    ceo_action: str
    vp_summary: VPSummary

class ProductBase(BaseModel):
    sku: str
    product_name: str
    cost_price: float
    selling_price: float
    marketplace: str
    inventory: int

class ProductCreate(ProductBase):
    pass

class Product(ProductBase):
    id: int

    class Config:
        from_attributes = True
