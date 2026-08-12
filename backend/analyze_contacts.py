import sqlite3
import pandas as pd

def analyze_leads():
    conn = sqlite3.connect("purity_beans.db")
    df = pd.read_sql_query("SELECT * FROM b2b_leads", conn)
    conn.close()
    
    total_leads = len(df)
    print(f"Total Leads in Database: {total_leads}\n")
    
    # Analyze names
    has_real_name = df['contact_name'].apply(lambda x: bool(x and str(x).strip() not in ['', 'Owner / Manager', 'Branch Manager', 'Owner', 'Manager']))
    has_email = df['email'].apply(lambda x: bool(x and isinstance(x, str) and '@' in x))
    has_phone = df['phone'].apply(lambda x: bool(x and isinstance(x, str) and len(str(x).strip()) >= 10))
    
    print("=== Contact Completeness Summary ===")
    print(f"Leads with real/specific contact name: {has_real_name.sum()} ({has_real_name.sum()/total_leads*100:.1f}%)")
    print(f"Leads with valid email: {has_email.sum()} ({has_email.sum()/total_leads*100:.1f}%)")
    print(f"Leads with valid phone number: {has_phone.sum()} ({has_phone.sum()/total_leads*100:.1f}%)")
    
    # Fully complete (name + email + phone)
    fully_complete = has_real_name & has_email & has_phone
    print(f"Leads with fully complete contact details (specific name, email, & phone): {fully_complete.sum()} ({fully_complete.sum()/total_leads*100:.1f}%)")
    print()
    
    # Division breakdown
    print("=== Breakdown by Division / Channel ===")
    divisions = df['division'].unique()
    breakdown_data = []
    for div in divisions:
        div_df = df[df['division'] == div]
        div_total = len(div_df)
        div_real_name = div_df['contact_name'].apply(lambda x: bool(x and str(x).strip() not in ['', 'Owner / Manager', 'Branch Manager', 'Owner', 'Manager'])).sum()
        div_email = div_df['email'].apply(lambda x: bool(x and isinstance(x, str) and '@' in x)).sum()
        div_phone = div_df['phone'].apply(lambda x: bool(x and isinstance(x, str) and len(str(x).strip()) >= 10)).sum()
        
        d_name = div_df['contact_name'].apply(lambda x: bool(x and str(x).strip() not in ['', 'Owner / Manager', 'Branch Manager', 'Owner', 'Manager']))
        d_email = div_df['email'].apply(lambda x: bool(x and isinstance(x, str) and '@' in x))
        d_phone = div_df['phone'].apply(lambda x: bool(x and isinstance(x, str) and len(str(x).strip()) >= 10))
        div_complete = (d_name & d_email & d_phone).sum()
        
        breakdown_data.append({
            'Division': div,
            'Total Leads': div_total,
            'Real Name': f"{div_real_name} ({div_real_name/div_total*100:.0f}%)",
            'Valid Email': f"{div_email} ({div_email/div_total*100:.0f}%)",
            'Valid Phone': f"{div_phone} ({div_phone/div_total*100:.0f}%)",
            'Fully Complete': f"{div_complete} ({div_complete/div_total*100:.0f}%)"
        })
    
    breakdown_df = pd.DataFrame(breakdown_data)
    print(breakdown_df.to_string(index=False))
    print()
    
    # Let's inspect some of the specific leads with real/complete details
    print("=== Sample Leads with Specific Contact Details ===")
    complete_leads = df[fully_complete].head(10)
    for idx, row in complete_leads.iterrows():
        print(f"- Company: {row['company']} | Division: {row['division']} | Contact: {row['contact_name']} ({row['contact_title']}) | Email: {row['email']} | Phone: {row['phone']}")
        
    print("\n=== Sample Leads with Missing/Placeholder Details ===")
    missing_leads = df[~fully_complete].head(10)
    for idx, row in missing_leads.iterrows():
        print(f"- Company: {row['company']} | Division: {row['division']} | Contact: {row['contact_name']} | Email: '{row['email']}' | Phone: '{row['phone']}'")

if __name__ == "__main__":
    analyze_leads()
