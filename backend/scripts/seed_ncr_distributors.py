"""
Delhi NCR distributor discovery — ingest.

Where this data came from
-------------------------
The system's own discovery could not produce it, and that is not a bug to route
around:

* OSM classifies into exactly five entities (cafe, restaurant, hotel, hospital,
  canteen_org). "distributor" is not one of them, and classify_osm_entity()
  fails closed to "unknown" rather than echoing the search term back — the guard
  that stopped 75 Abohar villages being ingested as businesses.
* The IndiaMART scraper returns 0 results for every distributor query. It is
  broken, not empty.

So these were gathered by web search and structured extraction, and every row
below is a contact the business (or its principal) publishes itself.

Provenance, per row
-------------------
NESTLE_DISTRIBUTOR_LOCATOR
    Nestle Professional's public distributor locator. These are authorised
    distributors who already move Nescafe into offices and HoReCa — they carry
    the competing product into exactly the accounts we want, which is why they
    lead the queue. Contact person and phone are published by Nestle.
    No email is published, so none is set. phone_source records the origin.

WEBSITE
    The business's own site. Only here is an email set, because
    trust_promoter treats a self-published address as ownership evidence.

Nothing is inferred. A city is normalised only where the published ADDRESS
names the city outright (e.g. city "G.B.NAGAR" with an address reading
"SECTOR 41, NOIDA" becomes Noida) — that is reading the record, not guessing at
it. Zirakpur is dropped: it is near Chandigarh, not in NCR.

Usage
-----
    python scripts/seed_ncr_distributors.py            # dry run
    python scripts/seed_ncr_distributors.py --commit
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

LOCATOR = "https://www.nestleprofessional.in/distributor-locator"

# (company, contact_person, address, city, pincode, phone)
NESTLE = [
    ("Food Service International", "Sanjay Paul", "D-11, Jungpura Extn.", "Delhi", "110014", "9871499884"),
    ("Sheetal Enterprises", "Pankaj Thakkar", "B-18, Second Floor, Commercial Complex, Dr. Mukherjee Nagar", "Delhi", "110009", "9818925934"),
    ("Fountain Head Enterprises", "Rohan Shourie", "Gali No.2, Madhu Vihar, near Dilli Darbar", "Delhi", "", "9871219994"),
    ("Suntime Traders", "Vishwajeet Singh", "22-23, Rani Garden, Shastri Nagar", "Delhi", "110031", "9312064004"),
    ("J M D Enterprises", "Bharat Bhushan", "5260, Chandrawal Road, Kamla Nagar", "Delhi", "110007", "8527573322"),
    ("Sai Vending Private Limited", "Ashish Vig", "F-1, D-16, City Chamber, D Block, Central Market, Prashant Vihar", "Delhi", "110085", "9873903766"),
    ("R R Associates", "JP Singh", "A-89, Lajpat Nagar - I", "Delhi", "110024", "9810063712"),
    ("Pioneer Vending Services", "Neeraj Negi", "67-D/1, Om Shanti Complex, Laxmi Market, opposite Canara Bank, Munirka", "Delhi", "110067", "9873334393"),
    ("Sai Enterprises", "Sanjay Saini", "Basement at 143, Samman Bazar and Basement at 84, Church Road, Bhogal, Jangpura", "Delhi", "110014", "9911018590"),
    ("Fairdeal Distributors", "Kapil Kshetrapal", "26 A, Krishna Market, Ground Floor, near MCD Community Centre, Lajpat Nagar-1", "Delhi", "110024", "9911136366"),
    ("Laxmi Narayan And Sons", "Vikram Duggal", "3F/162, N.I.T.", "Faridabad", "121001", "9810599551"),
    ("Moonlight Distributors", "Mayank", "Plot no 11, Sector 15A, Industrial Area", "Faridabad", "121007", "8800763764"),
    ("Impact", "Pradeep Mahajan", "B-11, Lohia Nagar Market", "Ghaziabad", "201001", "9818493150"),
    ("Shrinath Enterprises", "Sanjay Mahajan", "Pradhan Bhawan, c/o Jai Durga Motors, Noida - Dadri Road, Surajpur", "Greater Noida", "201306", "9810259331"),
    ("Suntime Traders Private Limited", "Vishwajeet", "619, Hans Plaza Market, Village Atta, Sector 27", "Noida", "201301", "9312064004"),
    ("Suntime Traders Private Limited", "Ranjan Talwar", "619, Hans Plaza Market, Village Atta, Sector 27", "Noida", "201301", "9212108882"),
    ("Vipra Vending Services", "Vikas Bhalla", "B-18, Sector 7", "Noida", "201301", "9810011013"),
    ("A S Marketing And Advertising", "Atul Sharma", "24/11, Sangam Complex, Sharma Market, Sector 5", "Noida", "201301", "9811160780"),
    ("Beans And More", "N.C. Jain", "A-83, Sector 58", "Noida", "201301", "9818153002"),
    # city published as G.B.NAGAR / GAUTAM BUDDHA NAGAR; the address itself says Noida
    ("Kohli Marketing", "Col. G.S. Kohli (Retd)", "Gali No.2, opposite Summerfield School, Village Aggahapur, Sector 41", "Noida", "201303", "9811019933"),
    ("Shree Shyam Foods", "Priyank Garg", "Opposite C-82, Chauhan Market, Village Gijhor, Sector 53", "Noida", "201301", "9818630900"),
    ("Modview Vending Services", "H D Sharma", "A-27-L, Second Floor, Sector 16", "Noida", "201301", "9212579727"),
    ("Sai Vending Services Private", "Ashish Vig", "Plot No.214, Sector 22, near Palam Vihar", "Gurugram", "122017", "9873903766"),
    ("Vpj Creations Enterprises", "Adesh", "Plot No F-2564-A, Ansals Palam Vihar, near Carterpuri Road", "Gurugram", "122017", "9811408580"),
    ("Suntime Commercial", "Vishwajeet", "99/277/2M, Atul Kataria Marg", "Gurugram", "122001", "9312064004"),
    ("RGVS Gezellig Pvt. Ltd.", "Tushar", "Plot No.1528/2, Baliyawas", "Gurugram", "122003", "7503181267"),
    ("SMS Marketing Solution", "Pulkit", "42C, Surya Vihar, Sector 4, near Police Chowki", "Gurugram", "122001", "9811535000"),
]

# Self-published on the company's own website; email allowed because the
# business publishing its address IS the ownership evidence trust_promoter
# accepts.
WEBSITE_SOURCED = [
    {
        "company": "BROOT Coffee Roasters",
        "contact_name": "Tamandeep",
        "address": "New Delhi",
        "city": "Delhi",
        "pincode": "",
        "phone": "8368118107",
        "whatsapp": "8368118107",
        "email": "tamandeep@brootcoffee.com",
        "website": "https://brootcoffee.com",
        "note": "wholesale roaster; serves cafes, restaurants, hotels",
    },
]


def _digits(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else d


def rows():
    out = []
    for company, person, addr, city, pin, phone in NESTLE:
        out.append({
            "company": company, "contact_name": person,
            "address": ", ".join(x for x in (addr, city, pin) if x),
            "city": city, "phone": phone, "whatsapp": phone,
            "email": "", "website": "",
            "source": "NESTLE_DISTRIBUTOR_LOCATOR", "source_url": LOCATOR,
            "note": "authorised Nestle Professional distributor",
        })
    for w in WEBSITE_SOURCED:
        out.append({
            "company": w["company"], "contact_name": w.get("contact_name", ""),
            "address": ", ".join(x for x in (w["address"], w["city"], w["pincode"]) if x),
            "city": w["city"], "phone": w["phone"], "whatsapp": w.get("whatsapp", ""),
            "email": w.get("email", ""), "website": w.get("website", ""),
            "source": "WEBSITE", "source_url": w.get("website", ""),
            "note": w.get("note", ""),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    from app.database.database import SessionLocal
    from app.models.models import B2BLead, LeadEvidence
    from app.services import territory

    db = SessionLocal()
    added = skipped = 0
    report = []
    try:
        existing_phone = {_digits(l.phone) for l in
                          db.query(B2BLead).filter(B2BLead.phone.isnot(None)).all()}
        existing_name = {(l.company or "").strip().lower()
                         for l in db.query(B2BLead).all()}

        for r in rows():
            key = _digits(r["phone"])
            if key and key in existing_phone:
                skipped += 1
                report.append(("dupe phone", r["company"]))
                continue
            if r["company"].strip().lower() in existing_name:
                skipped += 1
                report.append(("dupe name", r["company"]))
                continue

            lead = B2BLead(
                company=r["company"],
                contact_name=r["contact_name"] or None,
                address=r["address"],
                city=r["city"],
                state="Delhi" if r["city"] == "Delhi" else
                      ("Haryana" if r["city"] in ("Gurugram", "Faridabad") else "Uttar Pradesh"),
                phone=r["phone"],
                whatsapp_number=r["whatsapp"] or None,
                phone_source=r["source"],
                phone_collected_at=datetime.utcnow(),
                website=r["website"] or None,
                segment="distributor",
                division="RETAIL",
                status="NEW",
            )
            # Email only where the business publishes it. The 27 locator rows
            # get none, and that is the honest state — they are a call target,
            # not a send target.
            if r["email"]:
                lead.email = r["email"]
                lead.email_source = "WEBSITE"

            if args.commit:
                db.add(lead)
                db.flush()
                db.add(LeadEvidence(
                    lead_id=lead.id, signal_type="DISCOVERY_SOURCE",
                    value=r["note"] or r["source"], source=r["source"],
                    source_url=r["source_url"], collected_at=datetime.utcnow(),
                    verified=False,
                ))
            added += 1
            try:
                terr = territory.territory_of(lead)
            except Exception:
                terr = "?"
            report.append((f"add [{terr}]", f"{r['company']} — {r['city']}"))
            existing_phone.add(key)
            existing_name.add(r["company"].strip().lower())

        if args.commit:
            db.commit()
    finally:
        db.close()

    for tag, what in report:
        print(f"  {tag:<22}{what}")
    print(f"\nadded {added}  skipped {skipped}")
    if not args.commit:
        print("DRY RUN — nothing written. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
