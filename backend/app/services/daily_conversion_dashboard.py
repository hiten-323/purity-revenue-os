"""
Daily Conversion Dashboard for Purity Beans B2B Outreach.
Outputs daily conversions tiered into:
  - Tier 1: Catalogue & Wholesale Rates Requested (Commercial Consent)
  - Tier 2: 12-Jar Starter Carton Orders Placed (Direct Factory Rates: 25% trade + 5% cash = 30% margin)
  - Tier 3: Institutional Supply / Distributor Agreements Signed
"""
import sqlite3
import json
from datetime import datetime, timezone

DB_PATH = "/opt/purity-revenue-os/purity-revenue-os/backend/purity_beans.db"

def run_dashboard():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 70)
    print("      PURITY BEANS — DAILY CONVERSION DASHBOARD")
    print(f"      Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 70)

    # 1. Conversions by Category
    CATEGORY_SQL = """
    CASE 
        WHEN segment = 'grocery' AND industry IN ('Supermarket', 'Grocery Chain') THEN 'Retail Chains & Supermarkets'
        WHEN segment = 'grocery' AND industry = 'Retail Kirana' THEN 'Kirana'
        WHEN segment = 'corporate' AND industry = 'Modern Trade' THEN 'Retail Chains & Supermarkets'
        WHEN segment IN ('distributor', 'wholesaler') OR industry IN ('Distributor', 'Wholesaler') THEN 'Distributors & Wholesalers'
        WHEN segment IN ('horeca', 'cafe') OR industry IN ('Hotel', 'Restaurant', 'Cafe', 'Caterer') THEN 'HoReCa'
        WHEN segment IN ('corporate_office', 'facility_management') OR industry IN ('Corporate Office', 'Corporate', 'Facility Management', 'Manufacturing') THEN 'Offices & Corporate'
        WHEN segment = 'hospital' OR industry IN ('Hospital', 'School', 'Government') THEN 'Institutions'
        WHEN segment = 'corporate_gifting' OR industry = 'Corporate Gifting' THEN 'Gifting'
        ELSE 'Other / Unclassified'
    END
    """

    cur.execute(f"""
        SELECT 
            {CATEGORY_SQL} as category,
            COUNT(*) as total_leads,
            SUM(CASE WHEN call_outcome_last LIKE '%CATALOGUE%' OR call_outcome_last LIKE '%WHATSAPP_OPT_IN%' THEN 1 ELSE 0 END) as tier1_catalogue_requests,
            SUM(CASE WHEN status LIKE '%ORDER%' OR status LIKE '%WON%' THEN 1 ELSE 0 END) as tier2_starter_orders,
            SUM(CASE WHEN status LIKE '%CONTRACT%' OR status LIKE '%DISTRIBUTOR_SIGNED%' THEN 1 ELSE 0 END) as tier3_contracts
        FROM b2b_leads
        GROUP BY category
        ORDER BY tier1_catalogue_requests DESC, total_leads DESC
    """)
    rows = cur.fetchall()

    print("\n--- 1. CONVERSIONS BY CATEGORY ---")
    print(f"{'Category':<30} | {'Total Leads':<12} | {'Catalogue Reqs':<15} | {'Starter Orders':<15} | {'Contracts':<10}")
    print("-" * 92)
    for r in rows:
        print(f"{r['category']:<30} | {r['total_leads']:<12} | {r['tier1_catalogue_requests']:<15} | {r['tier2_starter_orders']:<15} | {r['tier3_contracts']:<10}")

    # 2. Conversions by Channel
    print("\n--- 2. CONVERSIONS BY CHANNEL ---")
    cur.execute("""
        SELECT 
            opener_variant as channel_opener,
            COUNT(*) as dials,
            SUM(CASE WHEN connected = 1 OR duration_seconds > 0 THEN 1 ELSE 0 END) as connects,
            SUM(CASE WHEN outcome LIKE '%CATALOGUE%' OR outcome LIKE '%WHATSAPP_OPT_IN%' THEN 1 ELSE 0 END) as catalogue_conversions
        FROM call_results
        WHERE opener_variant IS NOT NULL
        GROUP BY opener_variant
        ORDER BY catalogue_conversions DESC
    """)
    call_channels = cur.fetchall()
    print(f"{'Call Opener':<25} | {'Total Dials':<12} | {'Connects':<10} | {'Catalogue Wins':<15}")
    print("-" * 68)
    for c in call_channels:
        print(f"{c['channel_opener']:<25} | {c['dials']:<12} | {c['connects']:<10} | {c['catalogue_conversions']:<15}")

    # 3. High Intent Hot Leads (Action Items)
    print("\n--- 3. HIGH INTENT LEADS (TODAY'S ACTION PATH) ---")
    cur.execute("""
        SELECT id, company, contact_name, phone, segment, call_outcome_last, status, recommended_action
        FROM b2b_leads
        WHERE call_outcome_last LIKE '%CATALOGUE%' 
           OR call_outcome_last LIKE '%WHATSAPP_OPT_IN%'
           OR priority = 'HIGH'
        ORDER BY id DESC
        LIMIT 10
    """)
    hot = cur.fetchall()
    for h in hot:
        p = h['phone']
        masked_p = p[:4] + "****" + p[-2:] if p and len(p) >= 6 else "****"
        print(f"• [Lead {h['id']}] {h['company']} ({h['contact_name'] or 'Founder'}) | {masked_p} | Outcome: {h['call_outcome_last']} | Action: {h['recommended_action'] or 'Follow-up'}")

    conn.close()
    print("=" * 70)

if __name__ == "__main__":
    run_dashboard()
