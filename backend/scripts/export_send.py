import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(sys.path[0])
"""Export email sender (founder-approved copy v1, 21 Oct). Governance: EXPORT_GOVERNANCE.md.
Usage: python scripts/export_send.py UAE[,UK,...] [--dry]"""
from dotenv import load_dotenv; load_dotenv('.env')
import csv, json, random, re, sqlite3, time, datetime as dt
from zoneinfo import ZoneInfo
from app.database.database import SessionLocal
from app.services.email_sender import OutreachEmail, send_email
from app.services.deliverability import check_send_allowed
LEADS='data_export/export_leads_2026-10.csv'; LOG='logs/export_send_log_2026-10.csv'
CAT='https://cdn.shopify.com/s/files/1/0712/6315/8459/files/Purity_Beans_Catalogue_2026-10.pdf'
DAILY_CAP=20; VERSION='export_email_v1_2026-10-21'
TZ={'UAE':('Asia/Dubai',9,18),'Saudi Arabia':('Asia/Riyadh',9,18),'Kuwait':('Asia/Kuwait',9,18),'Malaysia':('Asia/Kuala_Lumpur',9,18),'UK':('Europe/London',9,17),'USA':('America/New_York',9,17)}
CNAME={'UAE':'the UAE','UK':'the UK','USA':'the USA'}
SUBJ='Indian instant coffee in glass jars, distribution enquiry for {country}'
BODY='''Dear Buying Team,

I'm Hiten Jain, founder of Purity Beans (Pure Pantry Provisions), an India-origin instant coffee brand packed in certified food-grade, lead-free glass jars. I came across {company} in {city} and believe our range could fit your portfolio.

Our range:
- Purica: pure Arabica, freeze-dried, no chicory
- Purista: freeze-dried pure coffee, no chicory
- Bold: pure coffee, no chicory, strong cup
- Ultra Blend: smooth coffee-chicory blend (70:30), lower in caffeine, the traditional Indian taste at an accessible price point

Why it works for your customers: a premium, clean-label Indian coffee for shoppers who want to trade up from mainstream brands, plus a familiar coffee-chicory option for the South Asian household.

Here is our catalogue: {cat}

I'm happy to share export pricing and terms on request. Would a 15-minute call next week suit you?

Regards,
Hiten Jain
Founder, Purity Beans | p3online.in
Business phone: +91 90849 58495

If you'd rather not hear from me, just reply "unsubscribe" and I won't write again.'''
FIELDS=['sent_at_ist','market','company','to_email','subject','version','status','detail','message_id']

def log_rows():
    if not os.path.exists(LOG): return []
    return list(csv.DictReader(open(LOG,encoding='utf-8')))
def append(row):
    new=not os.path.exists(LOG)
    with open(LOG,'a',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS)
        if new: w.writeheader()
        w.writerow(row)
def already_contacted(email, company):
    c=sqlite3.connect('file:purity_beans.db?mode=ro',uri=True)
    e=email.lower()
    if c.execute("select 1 from workflow_events where event_type in ('EMAIL_SENT','UNSUBSCRIBED','DO_NOT_CONTACT','COMPLAINT') and lower(payload) like ? limit 1",(f'%{e}%',)).fetchone(): return 'already emailed/suppressed in CRM'
    if c.execute("select 1 from b2b_leads where lower(email)=? and (contact_status in ('OPTED_OUT','DO_NOT_CONTACT') or status='DO_NOT_CONTACT')",(e,)).fetchone(): return 'suppressed'
    for r in log_rows():
        if r['status']=='sent' and (r['to_email'].lower()==e or r['company']==company): return 'already emailed (export log)'
        if r['status'] in ('unsubscribed','bounced') and r['to_email'].lower()==e: return r['status']
    return None
def in_hours(market):
    tz,a,b=TZ[market]; t=dt.datetime.now(ZoneInfo(tz)); return t.weekday()<5 and a<=t.hour<b
def main():
    markets=sys.argv[1].split(','); dry='--dry' in sys.argv
    today=dt.datetime.now().strftime('%Y-%m-%d')
    rows=[r for r in csv.DictReader(open(LEADS,encoding='utf-8')) if r['Market'] in markets and r['Public email'].strip()]
    rows.sort(key=lambda r:(markets.index(r['Market']), r['Priority']))
    db=SessionLocal(); sent=0; fails=0
    for r in rows:
        prior=log_rows(); sent_today=sum(1 for x in prior if x['status']=='sent' and x['sent_at_ist'].startswith(today))
        attempts=sum(1 for x in prior if x['status'] in ('sent','bounced','failed') and x['sent_at_ist'].startswith(today))
        bounced=sum(1 for x in prior if x['status'] in ('bounced','failed') and x['sent_at_ist'].startswith(today))
        if sent_today>=DAILY_CAP: print('daily cap reached',flush=True); break
        if attempts and bounced/attempts>0.05: print('KILL SWITCH: bounce/fail rate',bounced,attempts,flush=True); break
        m=r['Market']; to=r['Public email'].strip(); co=r['Company'].strip()
        why=already_contacted(to,co)
        if why: print('skip',co,why,flush=True); continue
        if not in_hours(m): print('skip (out of local hours)',co,flush=True); continue
        city=re.split(r'[(/]',r['City'])[0].strip() or CNAME.get(m,m)
        subj=SUBJ.format(country=CNAME.get(m,m)); body=BODY.format(company=re.sub(r'\s*\(.*?\)','',co).strip(),city=city,cat=CAT)
        if dry: print('DRY',m,co,to,'|',subj,flush=True); continue
        for _ in range(15):
            v=check_send_allowed(db)
            if v.allowed or 'pacing' not in v.reason: break
            time.sleep(20)
        if not v.allowed: print('HELD',v.reason,flush=True); break
        e=OutreachEmail(to_email=to,to_name=co,company=co,subject=subj,body_text=body,lead_id=None)
        res=send_email(e)
        st='sent' if res.status=='sent' else ('held' if 'HELD' in (res.error or '') or 'business hours' in (res.error or '') else 'failed')
        append({'sent_at_ist':dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'market':m,'company':co,'to_email':to,'subject':subj,'version':VERSION,'status':st,'detail':(res.smtp_response or res.error or '')[:200],'message_id':res.message_id or ''})
        print(dt.datetime.now().strftime('%H:%M:%S'),st,m,co,(res.smtp_response or res.error or '')[:100],flush=True)
        if st=='held': break
        if st=='sent': sent+=1
        time.sleep(random.randint(60,120))
    print('done sent',sent,flush=True)
main()

