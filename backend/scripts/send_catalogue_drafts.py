import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); os.chdir(sys.path[0])
from dotenv import load_dotenv; load_dotenv('.env')
import time, datetime as dt, sys
from app.database.database import SessionLocal
from app.models.models import EmailDraft, B2BLead, WorkflowEvent
from app.services.email_sender import build_outreach_email, send_email
from app.services.deliverability import check_send_allowed
db=SessionLocal()
for did in [int(x) for x in sys.argv[1:]]:
    for _ in range(15):
        v=check_send_allowed(db)
        if v.allowed or 'pacing' not in v.reason: break
        time.sleep(20)
    if not v.allowed: print('HELD',v.reason, flush=True); break
    d=db.get(EmailDraft,did); l=db.get(B2BLead,d.lead_id)
    if d.status=='SENT': continue
    e=build_outreach_email(to_email=l.email,to_name=l.contact_name or l.company,company=l.company,subject=d.subject,body=d.body,lead_id=l.id)
    r=send_email(e)
    print(dt.datetime.now().strftime('%H:%M:%S'), l.company, r.status, (r.error or r.smtp_response or '')[:120], flush=True)
    if r.status=='sent':
        d.status='SENT'; d.sent_at=dt.datetime.utcnow(); d.zoho_message_id=r.message_id or d.zoho_message_id; d.smtp_response=(r.smtp_response or '')[:300]
        db.add(WorkflowEvent(lead_id=l.id,event_type='EMAIL_SENT',actor='FOUNDER',channel='email',payload={'to':l.email,'subject':d.subject,'message_id':r.message_id,'smtp_response':r.smtp_response,'authorised_by':'founder approval 21 Oct (catalogue link)','draft_id':did},occurred_at=dt.datetime.utcnow()))
    db.commit(); db.expire_all()
    time.sleep(60)
db.close(); print('done', flush=True)

