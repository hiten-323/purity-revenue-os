import sys
import os
import asyncio
from pathlib import Path

# Adjust path to backend root
backend_path = Path(__file__).parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from app.database.database import SessionLocal, engine, Base
import app.models.models
from app.models.models import B2BLead, Sale, Product
from app.services.crm_tracker import CRMTrackerService
from app.agents.b2b.director_ai_v2 import B2BDirectorAIV2

def clean_database(db):
    print("Clearing test database tables...")
    db.query(Sale).delete()
    db.query(B2BLead).delete()
    db.query(Product).delete()
    db.commit()

def seed_products(db):
    print("Seeding premium coffee products...")
    db.add(Product(
        id=1,
        sku="PURITY-INSTANT-CLASSIC-50",
        product_name="Purity Classic Instant Coffee (50g)",
        cost_price=120.0,
        selling_price=280.0,
        marketplace="Shopify",
        inventory=1500
    ))
    db.add(Product(
        id=2,
        sku="PURITY-INSTANT-DARK-50",
        product_name="Purity Dark Instant Coffee (50g)",
        cost_price=130.0,
        selling_price=299.0,
        marketplace="Shopify",
        inventory=1200
    ))
    db.commit()

async def test_crm_funnel():
    print("Initializing Database tables...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    clean_database(db)
    seed_products(db)
    
    director = B2BDirectorAIV2(db, provider=None)
    
    print("\n--- SIMULATING DAILY REVENUE LOOP (12 DAYS) ---")
    
    for day in range(1, 13):
        print(f"\n[Day {day}] Running B2B Sales OS daily pipeline scan...")
        # Run B2B executive report which scans (adds new leads) and advances existing leads
        report = await director.generate_b2b_executive_report()
        
        # Query database CRM state
        leads = CRMTrackerService.get_all_leads(db)
        kpis = CRMTrackerService.get_kpis(db)
        sales_count = db.query(Sale).count()
        sales = db.query(Sale).all()
        total_sales_val = sum(s.revenue for s in sales)
        
        print(f"  CRM Leads: {len(leads)} leads active in funnel")
        for lead in leads:
            print(f"    - {lead.company} ({lead.division}): status={lead.status}, score={lead.score}, value=Rs. {lead.estimated_value:,.0f}")
            
        print(f"  KPIs: pipeline_val=Rs. {kpis['pipeline_value_inr']:,.0f}, meetings={kpis['meetings_booked']}, samples={kpis['samples_sent']}, orders_won={kpis['orders_won']}")
        print(f"  Sales Captured: {sales_count} orders won, Total Closed Revenue MTD: Rs. {total_sales_val:,.2f}")
        
        # Verify stateful progress
        techcorp = db.query(B2BLead).filter(B2BLead.company == "TechCorp Solutions").first()
        globaltraders = db.query(B2BLead).filter(B2BLead.company == "Global Traders Inc").first()
        
        if day == 1:
            assert techcorp.status == "QUALIFIED", f"Expected TechCorp to be QUALIFIED, got {techcorp.status}"
            assert globaltraders.status == "COLD", f"Expected Global Traders to be COLD, got {globaltraders.status}"
            print("  [OK] Day 1 assertions passed: leads correctly qualified/archived based on opportunity score.")
            
        elif day == 2:
            assert techcorp.status == "EMAIL_SENT", f"Expected TechCorp to be EMAIL_SENT, got {techcorp.status}"
            print("  [OK] Day 2 assertions passed: qualified leads advanced to email outreach sent.")
            
        elif day == 3:
            assert techcorp.status == "REPLIED", f"Expected TechCorp to be REPLIED, got {techcorp.status}"
            print("  [OK] Day 3 assertions passed: outreach email reply detected.")
            
        elif day == 4:
            assert techcorp.status == "MEETING_BOOKED", f"Expected TechCorp to be MEETING_BOOKED, got {techcorp.status}"
            print("  [OK] Day 4 assertions passed: replied leads advanced to meeting booked.")
            
        elif day == 5:
            assert techcorp.status == "MEETING_COMPLETED", f"Expected TechCorp to be MEETING_COMPLETED, got {techcorp.status}"
            print("  [OK] Day 5 assertions passed: meeting show rate checked and completed.")
            
        elif day == 6:
            assert techcorp.status == "SAMPLE_SENT", f"Expected TechCorp to be SAMPLE_SENT, got {techcorp.status}"
            print("  [OK] Day 6 assertions passed: meetings advanced to coffee sample dispatch.")
            
        elif day == 7:
            assert techcorp.status == "PROPOSAL_SENT", f"Expected TechCorp to be PROPOSAL_SENT, got {techcorp.status}"
            print("  [OK] Day 7 assertions passed: sample feedback advanced to proposal sent.")
            
        elif day == 8:
            assert techcorp.status == "ORDER_WON", f"Expected TechCorp to be ORDER_WON, got {techcorp.status}"
            assert sales_count > 0, "Expected at least 1 closed sale in sales table"
            assert total_sales_val > 0, "Expected positive closed sales revenue"
            print("  [OK] Day 8 assertions passed: proposals closed-won and sales successfully recorded!")
            
        elif day == 9:
            assert techcorp.status == "ONBOARDED", f"Expected TechCorp to be ONBOARDED, got {techcorp.status}"
            print("  [OK] Day 9 assertions passed: closed-won leads advanced to onboarded.")
            
        elif day == 10:
            assert techcorp.status == "REORDER_PREDICTED", f"Expected TechCorp to be REORDER_PREDICTED, got {techcorp.status}"
            print("  [OK] Day 10 assertions passed: stock prediction triggers reorder forecast.")
            
        elif day == 11:
            assert techcorp.status == "UPSELL_OFFERED", f"Expected TechCorp to be UPSELL_OFFERED, got {techcorp.status}"
            print("  [OK] Day 11 assertions passed: predictive alerts trigger upsell offer.")
            
        elif day == 12:
            assert techcorp.status == "ACCOUNT_GROWTH", f"Expected TechCorp to be ACCOUNT_GROWTH, got {techcorp.status}"
            print("  [OK] Day 12 assertions passed: account growth contract successfully closed!")
            
    db.close()
    print("\n--- ALL CRM FUNNEL REVENUE LOOP TESTS PASSED SUCCESSFULLY! ---")

if __name__ == "__main__":
    asyncio.run(test_crm_funnel())
