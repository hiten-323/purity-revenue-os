"""
Wipes all mock/demo leads and seeds ONLY real leads at honest stages.
Run once: python reset_crm_real.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database.database import SessionLocal, engine, Base
import app.models.models
from app.models.models import B2BLead
from datetime import datetime

db = SessionLocal()

# Wipe everything
deleted = db.query(B2BLead).delete()
db.commit()
print(f"Deleted {deleted} mock leads.")

# Real leads only — status = DISCOVERED (nothing has happened yet)
real_leads = [
    # Abohar HORECA — walk-in targets
    {
        "company": "Hotel Giorgio",
        "contact_name": "Owner / Manager",
        "contact_title": "Owner",
        "email": "giorgiohotel@gmail.com",
        "phone": "+91 98761 27620",
        "city": "Abohar",
        "division": "horeca",
        "lead_source": "Google Maps",
        "industry": "Hospitality",
        "region": "North",
        "company_size": "SMB",
        "contact_persona": "Owner",
        "estimated_value": 270000.0,
        "score": 90,
        "probability": 0.3,
        "status": "DISCOVERED",
        "priority": "HIGH",
        "recommended_action": "Walk in TODAY — bring 5 sample sachets. Call first: +91 98761 27620",
        "qualification_notes": "Top HORECA lead in Abohar. Premium hotel, likely serves 50+ covers/day.",
    },
    {
        "company": "JBM Resorts",
        "contact_name": "Owner / Manager",
        "contact_title": "Owner",
        "email": "",
        "phone": "",
        "city": "Abohar",
        "division": "horeca",
        "lead_source": "Google Maps",
        "industry": "Hospitality",
        "region": "North",
        "company_size": "SMB",
        "contact_persona": "Owner",
        "estimated_value": 180000.0,
        "score": 80,
        "probability": 0.25,
        "status": "DISCOVERED",
        "priority": "HIGH",
        "recommended_action": "Walk in this week — find contact number on Google Maps",
        "qualification_notes": "Resort property in Abohar, good potential for bulk coffee.",
    },
    {
        "company": "Brighton Pear Resort",
        "contact_name": "Owner / Manager",
        "contact_title": "Owner",
        "email": "",
        "phone": "",
        "city": "Abohar",
        "division": "horeca",
        "lead_source": "Google Maps",
        "industry": "Hospitality",
        "region": "North",
        "company_size": "SMB",
        "contact_persona": "Owner",
        "estimated_value": 144000.0,
        "score": 75,
        "probability": 0.25,
        "status": "DISCOVERED",
        "priority": "MEDIUM",
        "recommended_action": "Walk in this week",
        "qualification_notes": "Resort in Abohar area.",
    },
    {
        "company": "Grand Hotel Abohar",
        "contact_name": "Owner / Manager",
        "contact_title": "Owner",
        "email": "",
        "phone": "",
        "city": "Abohar",
        "division": "horeca",
        "lead_source": "Google Maps",
        "industry": "Hospitality",
        "region": "North",
        "company_size": "SMB",
        "contact_persona": "Owner",
        "estimated_value": 144000.0,
        "score": 70,
        "probability": 0.2,
        "status": "DISCOVERED",
        "priority": "MEDIUM",
        "recommended_action": "Walk in this week",
        "qualification_notes": "Hotel in Abohar.",
    },
    # Abohar CORPORATE
    {
        "company": "HDFC Bank Abohar",
        "contact_name": "Branch Manager",
        "contact_title": "Branch Manager",
        "email": "",
        "phone": "",
        "city": "Abohar",
        "division": "corporate",
        "lead_source": "Google Maps",
        "industry": "Corporate",
        "region": "North",
        "company_size": "SMB",
        "contact_persona": "Admin Manager",
        "estimated_value": 90000.0,
        "score": 65,
        "probability": 0.2,
        "status": "DISCOVERED",
        "priority": "MEDIUM",
        "recommended_action": "Walk in — speak to branch manager about staff pantry coffee",
        "qualification_notes": "Bank branch, ~20 staff. Good for monthly pantry supply.",
    },
    {
        "company": "DAV College Abohar",
        "contact_name": "Principal / Admin",
        "contact_title": "Admin Manager",
        "email": "",
        "phone": "",
        "city": "Abohar",
        "division": "corporate",
        "lead_source": "Google Maps",
        "industry": "Corporate",
        "region": "North",
        "company_size": "SMB",
        "contact_persona": "Admin Manager",
        "estimated_value": 216000.0,
        "score": 70,
        "probability": 0.2,
        "status": "DISCOVERED",
        "priority": "MEDIUM",
        "recommended_action": "Walk in — speak to admin/canteen about faculty lounge supply",
        "qualification_notes": "Large college, faculty canteen + staff. High volume potential.",
    },
]

for data in real_leads:
    lead = B2BLead(
        company=data["company"],
        contact_name=data["contact_name"],
        contact_title=data["contact_title"],
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        city=data["city"],
        division=data["division"],
        lead_source=data["lead_source"],
        industry=data["industry"],
        region=data["region"],
        company_size=data["company_size"],
        contact_persona=data["contact_persona"],
        estimated_value=data["estimated_value"],
        score=data["score"],
        probability=data["probability"],
        status=data["status"],
        priority=data["priority"],
        recommended_action=data["recommended_action"],
        qualification_notes=data["qualification_notes"],
        last_updated=datetime.utcnow(),
    )
    db.add(lead)

db.commit()
print(f"Seeded {len(real_leads)} real leads.")
print()
print("Lead summary:")
for l in db.query(B2BLead).all():
    print(f"  {l.company} [{l.city}] [{l.status}] Rs{l.estimated_value:.0f}/yr")

db.close()
print()
print("Done. Refresh your dashboard — KPIs will now show 0/0/0 (honest).")
