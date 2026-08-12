"""
Programmatic database seeder for Purity Beans CRM.
Generates 1,024 realistic, highly enriched leads, products, and sales logs.
Run: python seed_abohar_full.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app.database.database import SessionLocal, engine, Base
from app.models.models import B2BLead, Product, Sale
from datetime import datetime, timedelta
import random

def seed_db():
    print("Recreating database tables for clean schema updates...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    print("Seeding signature coffee SKUs...")
    products = [
        Product(id=1, sku="PURITY-PURICA-50", product_name="Purica", cost_price=120.0, selling_price=280.0, marketplace="Shopify", inventory=1500),
        Product(id=2, sku="PURITY-ULTRA", product_name="Ultra Blend", cost_price=180.0, selling_price=420.0, marketplace="Shopify", inventory=1200),
        Product(id=3, sku="PURITY-BOLD", product_name="Bold", cost_price=110.0, selling_price=250.0, marketplace="Shopify", inventory=1800),
        Product(id=4, sku="PURITY-PURISTA", product_name="Purista", cost_price=130.0, selling_price=299.0, marketplace="Shopify", inventory=1000)
    ]
    for p in products:
        db.add(p)
    db.commit()

    cities = ["Bengaluru", "Mumbai", "Delhi-NCR", "Pune", "Hyderabad", "Chennai", "Abohar"]
    divisions = ["corporate", "distributor", "retail", "gifting", "horeca", "tender"]
    
    first_names = ["Anil", "Sunita", "Deepak", "Priyanka", "Rajesh", "Sanjay", "Neha", "Amit", "Karan", "Rohan", "Arjun", "Kavita", "Vijay", "Meera", "Suresh", "Aditi", "Rahul", "Pooja", "Vikram", "Shalini", "Sunil", "Preeti", "Mohit", "Ananya", "Rakesh", "Jyoti", "Siddharth", "Ritu", "Harish", "Divya"]
    last_names = ["Sharma", "Rao", "Gupta", "Sen", "Patel", "Verma", "Joshi", "Mishra", "Malhotra", "Mehta", "Nair", "Reddy", "Singh", "Chawla", "Bose", "Das", "Jain", "Bahl", "Goel", "Kapoor", "Bhardwaj", "Trivedi", "Deshmukh", "Narayanan", "Pillai", "Choudhury", "Saxena", "Roy", "Dutta", "Kohli"]
    
    company_prefixes = ["Vertex", "Apex", "Quantum", "Nexus", "Zenith", "Synergy", "Alpha", "Omega", "Dynamic", "Stellar", "Core", "Global", "Nova", "Integra", "Prime", "Matrix", "Elite", "Horizon", "Incite", "Vanguard", "Catalyst", "Empower", "Ascent", "Omni", "Sigma", "Vector", "Optima", "Fusion", "Genesis", "Infinity"]
    company_suffixes = ["Solutions", "Capital", "Enterprises", "Industries", "Group", "Ventures", "Holdings", "Foods", "Distributors", "Logistics", "Services", "Partners", "Co-working", "Consulting", "Retails", "Trading", "Tech", "Systems", "Corp", "Pvt Ltd"]
    
    skus = ["Purica", "Ultra Blend", "Bold", "Purista"]
    
    lead_sources = ["Google Maps", "IndiaMART", "LinkedIn", "TradeIndia", "Tender Portal", "Referral"]
    
    industries_map = {
        "corporate": ["IT & Software", "Financial Services", "Education", "Healthcare"],
        "distributor": ["Wholesale", "Distribution"],
        "retail": ["Retail", "Supermarket"],
        "gifting": ["Manufacturing", "Services"],
        "horeca": ["Hospitality", "Restaurant"],
        "tender": ["Government", "Paramilitary"]
    }
    
    personas_map = {
        "corporate": ["Facilities Head", "HR Manager", "Admin Lead", "Procurement Manager"],
        "distributor": ["Owner", "Managing Partner", "Distribution Head", "Business Owner"],
        "retail": ["Category Buyer", "Store Manager", "Operations Manager", "Purchase Executive"],
        "gifting": ["HR Director", "Admin Manager", "Marketing Lead", "Procurement Manager"],
        "horeca": ["Restaurant Manager", "F&B Director", "Hotel Manager", "General Manager"],
        "tender": ["Canteen Officer", "Procurement Officer", "Quarter Master", "Tender Director"]
    }
    
    statuses = ["DISCOVERED", "QUALIFIED", "EMAIL_SENT", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "COLD"]
    status_weights = [0.52, 0.15, 0.10, 0.08, 0.03, 0.02, 0.03, 0.02, 0.03, 0.02]
    
    reply_templates = [
        "Replied: We consume {volume:.0f}kg coffee monthly. Interested in your low-acid {sku} blend sample. Please ship to our office.",
        "Replied: Looking for a premium FMCG distributor in {city}. Your margins sheet looks promising.",
        "Replied: Our facilities manager wants to set up a tasting session for {sku} next Monday at 3 PM.",
        "Replied: FSSAI credentials look clean. Please send over wholesale quotes for contract supply.",
        "Replied: We have a monthly corporate gifting requirement. Please send the catalog for {sku}."
    ]

    print("Generating 1,024 highly enriched leads...")
    generated_companies = set()
    leads_to_add = []
    
    # Use deterministic seeding for reproducible verification runs
    random.seed(42)
    
    for i in range(1, 1025):
        city = random.choice(cities)
        div = random.choice(divisions)
        
        while True:
            comp_name = f"{random.choice(company_prefixes)} {random.choice(company_suffixes)} {random.randint(10, 9999)}"
            if comp_name not in generated_companies:
                generated_companies.add(comp_name)
                break
                
        c_first = random.choice(first_names)
        c_last = random.choice(last_names)
        contact_name = f"{c_first} {c_last}"
        contact_title = random.choice(personas_map[div])
        
        email = f"{c_first.lower()}.{c_last.lower()}@{comp_name.lower().replace(' ', '')[:15]}.com"
        phone = f"+91-98765-{i:05d}"
        
        is_enriched = random.random() < 0.97
        if not is_enriched:
            email = ""
            phone = ""
            linkedin_url = ""
        else:
            linkedin_url = f"https://linkedin.com/in/{c_first.lower()}-{c_last.lower()}-{comp_name.lower().replace(' ', '-')[:12]}"
            
        industry = random.choice(industries_map[div])
        region = "North" if city in ["Abohar", "Delhi-NCR", "Pune"] else ("South" if city in ["Bengaluru", "Chennai"] else "West")
        comp_size = "Enterprise" if div in ["tender", "distributor"] else (random.choice(["Mid-Market", "SMB"]))
        
        if div in ["distributor", "tender"]:
            monthly_kg = float(random.randint(100, 800))
        elif div == "gifting":
            monthly_kg = float(random.randint(50, 250))
        else:
            monthly_kg = float(random.randint(20, 150))
            
        suggested_price = 900.0 - (monthly_kg * 0.25)
        est_val = monthly_kg * suggested_price * 12
        
        probability = random.uniform(0.1, 0.95)
        status = random.choices(statuses, weights=status_weights)[0]
        
        sample_taste = random.randint(6, 10) if status in ["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"] else None
        sample_aroma = random.randint(6, 10) if status in ["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"] else None
        sample_packaging = random.randint(7, 10) if status in ["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"] else None
        sample_intent = random.randint(6, 10) if status in ["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"] else None
        sample_purchase_again = random.randint(6, 10) if status in ["SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"] else None
        
        sample_sku_val = random.choice(skus)
        purchase_sku_val = sample_sku_val if status == "ORDER_WON" else None
        reorder_sku_val = sample_sku_val if status in ["ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"] else None
        
        email_stage = random.randint(1, 4) if status in ["EMAIL_SENT", "REPLIED", "MEETING_BOOKED", "SAMPLE_SENT", "PROPOSAL_SENT"] else 0
        opens = random.randint(1, 8) if email_stage > 0 else 0
        clicks = random.randint(1, 5) if opens > 0 else 0
        
        # Calculate Intent Score
        intent_score = 0
        if opens > 0:
            intent_score += 5
        if opens >= 2:
            intent_score += 10
        if clicks > 0:
            intent_score += 15 # Catalogue Download
        if clicks >= 2:
            intent_score += 20 # Website Visit
        if clicks >= 3:
            intent_score += 25 # Pricing Page Visit
            
        if status in ["SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"]:
            intent_score += 40
        if status in ["REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"]:
            intent_score += 50
        if status in ["MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"]:
            intent_score += 75
            
        intent_score = min(intent_score, 100)
        
        # Determine Intent Tier
        if intent_score >= 81:
            intent_tier = "Priority"
            priority = "HIGH"
        elif intent_score >= 51:
            intent_tier = "Hot"
            priority = "MEDIUM"
        elif intent_score >= 21:
            intent_tier = "Warm"
            priority = "LOW"
        else:
            intent_tier = "Cold"
            priority = "LOW"
            
        qual_notes = f"Enriched prospect from {city}."
        if status == "REPLIED":
            qual_notes = random.choice(reply_templates).format(volume=monthly_kg, sku=sample_sku_val, city=city)
            
        rec_action = f"Schedule tasting for {sample_sku_val}."
        if status == "DISCOVERED":
            rec_action = f"Pitch {sample_sku_val} low-acid program."
        elif status == "PROPOSAL_SENT":
            rec_action = f"Follow up on wholesale contract terms."
            
        # Add random lost reasons for COLD leads
        lost_reason = None
        if status == "COLD":
            lost_reason = random.choice([
                "Price Too High", "Already Using Nescafe", "No Requirement",
                "Waiting For Approval", "Low Consumption", "Contract Locked"
            ])
 
        lead = B2BLead(
            company=comp_name,
            contact_name=contact_name,
            contact_title=contact_title,
            email=email,
            phone=phone,
            linkedin=linkedin_url,
            decision_maker_score=random.randint(5, 10),
            city=city,
            division=div,
            lead_source=random.choice(lead_sources),
            industry=industry,
            region=region,
            company_size=comp_size,
            contact_persona=contact_title,
            estimated_value=est_val,
            score=intent_score,
            intent_score=intent_score,
            intent_tier=intent_tier,
            probability=probability,
            status=status,
            priority=priority,
            recommended_action=rec_action,
            qualification_notes=qual_notes,
            lost_reason=lost_reason,
            sample_taste=sample_taste,
            sample_aroma=sample_aroma,
            sample_packaging=sample_packaging,
            sample_intent=sample_intent,
            sample_purchase_again=sample_purchase_again,
            expected_monthly_consumption_kg=monthly_kg,
            sample_sku=sample_sku_val,
            purchase_sku=purchase_sku_val,
            reorder_sku=reorder_sku_val,
            email_sequence_stage=email_stage,
            email_opens=opens,
            email_clicks=clicks,
            last_updated=datetime.utcnow() - timedelta(days=random.randint(0, 15))
        )
        leads_to_add.append(lead)

    db.bulk_save_objects(leads_to_add)
    db.commit()
    print(f"Successfully seeded {len(leads_to_add)} leads in SQLite CRM database!")

    # Record some mock closed sales transactions for SKU analytics
    won_leads = db.query(B2BLead).filter(B2BLead.status == "ORDER_WON").all()
    for wl in won_leads:
        sku_to_prod = {
            "Purica": 1,
            "Ultra Blend": 2,
            "Bold": 3,
            "Purista": 4
        }
        prod_id = sku_to_prod.get(wl.sample_sku, 1)
        qty = max(int(wl.expected_monthly_consumption_kg), 5)
        revenue = qty * (280.0 if prod_id == 1 else (420.0 if prod_id == 2 else (250.0 if prod_id == 3 else 299.0)))
        sale = Sale(
            product_id=prod_id,
            marketplace="B2B CRM",
            quantity=qty,
            revenue=revenue,
            sale_date=datetime.utcnow() - timedelta(days=random.randint(0, 10))
        )
        db.add(sale)
    db.commit()
    print(f"Recorded B2B won sales transactions in sales table.")
    
    db.close()

if __name__ == "__main__":
    seed_db()
