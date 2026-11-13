import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(sys.path[0])
from dotenv import load_dotenv; load_dotenv('.env')
import csv, random, re, sqlite3, time, datetime as dt
from zoneinfo import ZoneInfo
IST=ZoneInfo('Asia/Kolkata')
HERE=os.path.join(sys.path[0],'scripts')
LEADS=os.path.join(HERE,'diwali_targets.csv'); DRAFTS=os.path.join(HERE,'diwali_drafts.md')
LOG=os.path.join(HERE,'diwali_send_log.csv')
FIELDS=['company','email','subject_variant','subject','time_ist','smtp_result','detail','message_id']
SUBJ={'A':"Diwali gifting for your team — coffee gift sets with corporate pricing",
      'B':"Corporate Diwali coffee gifts — order by 2 Nov",
      'C':"Diwali gift sets from Purity Beans (India's Cleanest Instant Coffee)"}
BANNED=[r'100\s*%',r'\bfresh',r'lab[- ]tested',r'zero acidity',r'\bhealth',r'immun',r'antioxidant',r'weight loss',r'ultra blend',r'variety box',r'₹559',r'gourmet-duo(?!-mini)']
def body_template():
    t=open(DRAFTS,encoding='utf-8').read()
    sec=t.split('## 1) Cold email')[1].split('## 2)')[0]
    b=sec.split('Body:',1)[1].strip()
    return b.rsplit('---',1)[0].strip()
TITLES={'ca','dr','dr.','mr','mr.','ms','mrs','adv','adv.'}
def first_name(cp):
    cp=(cp or '').strip()
    if not cp or '/' in cp or '&' in cp: return 'Team'
    toks=[x for x in cp.replace('.',' . ').split() if x.lower().strip('.') not in TITLES and x!='.']
    return toks[0] if toks and toks[0][0].isupper() and len(toks[0])>1 else 'Team'
def now(): return dt.datetime.now(IST)
def in_window(t=None):
    t=t or now(); return t.weekday()<=5 and dt.time(9,30)<=t.time()<dt.time(18,30)
def log_rows():
    return list(csv.DictReader(open(LOG,encoding='utf-8'))) if os.path.exists(LOG) else []
def append(row):
    new=not os.path.exists(LOG)
    with open(LOG,'a',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS)
        if new: w.writeheader()
        w.writerow(row)
def skip_reason(email, company, seen_e, seen_c):
    e=email.lower().strip()
    if e in seen_e: return 'duplicate address in list'
    if company.lower() in seen_c: return 'duplicate company in list'
    c=sqlite3.connect('file:purity_beans.db?mode=ro',uri=True)
    if c.execute("select 1 from workflow_events where event_type in ('EMAIL_SENT','UNSUBSCRIBED','DO_NOT_CONTACT','COMPLAINT','EMAIL_BOUNCED') and lower(payload) like ? limit 1",(f'%{e}%',)).fetchone(): return 'already emailed/suppressed in outreach DB'
    if c.execute("select 1 from b2b_leads where lower(email)=? limit 1",(e,)).fetchone(): return 'address already in outreach DB (b2b_leads)'
    try:
        if c.execute("select 1 from outreach_events where lower(coalesce(metadata_json,'')||coalesce(company,'')) like ? and channel='email' limit 1",(f'%{e}%',)).fetchone(): return 'already emailed (outreach_events)'
    except Exception: pass
    for r in log_rows():
        if r['email'].lower()==e and r['smtp_result'] in ('sent','failed','bounced','skipped_invalid'): return 'already attempted (diwali log): '+r['smtp_result']
        if r['company'].lower()==company.lower() and r['smtp_result']=='sent': return 'company already emailed (diwali log)'
    return None
def main():
    dry='--dry' in sys.argv
    tmpl=body_template()
    rows=[r for r in csv.DictReader(open(LEADS,encoding='utf-8')) if r['public_email'].strip()]
    print('targets with email:',len(rows),flush=True)
    seen_e=set(); seen_c=set(); sent=0; fails=0; i=0
    if not dry:
        from app.database.database import SessionLocal
        from app.services.email_sender import OutreachEmail, send_email
        from app.services.deliverability import check_send_allowed
        db=SessionLocal()
    for r in rows:
        to=r['public_email'].strip(); co=r['name'].strip()
        why=skip_reason(to,co,seen_e,seen_c); seen_e.add(to.lower()); seen_c.add(co.lower())
        if why: print('SKIP',co,to,'|',why,flush=True); continue
        var='ABC'[i%3]; i+=1
        body=tmpl.replace('{{FirstName}}',first_name(r.get('contact_person')))
        for pat in BANNED:
            if re.search(pat,(SUBJ[var]+'\n'+body),re.I): print('CLAIM VIOLATION',pat,co,flush=True); return
        if 'reply "stop"' not in body: print('MISSING OPT-OUT'); return
        if dry:
            print('DRY',var,co,to,'| Hi',first_name(r.get('contact_person')),flush=True); continue
        if not in_window(): print('OUT OF WINDOW — stopping; scheduled task resumes 9:30 IST',flush=True); break
        for _ in range(15):
            v=check_send_allowed(db)
            if v.allowed or 'pacing' not in v.reason: break
            time.sleep(20)
        if not v.allowed: print('HELD by deliverability guard:',v.reason,flush=True); break
        res=send_email(OutreachEmail(to_email=to,to_name=co,company=co,subject=SUBJ[var],body_text=body,lead_id=None))
        err=res.error or ''
        st='sent' if res.status=='sent' else ('held' if 'HELD' in err or 'business hours' in err else ('skipped_invalid' if err.startswith('BLOCKED') else 'failed'))
        append({'company':co,'email':to,'subject_variant':var,'subject':SUBJ[var],'time_ist':now().strftime('%Y-%m-%d %H:%M:%S'),'smtp_result':st,'detail':(res.smtp_response or res.error or '')[:200],'message_id':res.message_id or ''})
        print(now().strftime('%H:%M:%S'),st,var,co,(res.smtp_response or res.error or '')[:100],flush=True)
        if st=='held': break
        if st=='failed': print('STOP: SMTP error',flush=True); break
        if st=='skipped_invalid': continue
        sent+=1
        time.sleep(random.randint(60,90))
    print('done sent',sent,flush=True)
main()
