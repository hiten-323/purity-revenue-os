import sys, os
sys.path.insert(0, r"c:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session\backend")

from app.database.database import SessionLocal
from app.models.models import EmailDraft, B2BLead
from datetime import datetime

def reset():
    db = SessionLocal()
    try:
        # Find all drafts with status SENT but no message_id
        legacy_drafts = db.query(EmailDraft).filter(
            EmailDraft.status == 'SENT',
            (EmailDraft.zoho_message_id == None) | (EmailDraft.zoho_message_id == '')
        ).all()
        
        print(f"Found {len(legacy_drafts)} legacy sent emails with no Zoho Message-ID.")
        
        reset_count = 0
        for draft in legacy_drafts:
            draft.status = 'DRAFT'
            draft.sent_at = None
            
            # Find corresponding B2BLead
            lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
            if lead:
                lead.status = 'DISCOVERED'
                lead.email_approved_by_founder = False
                lead.recommended_action = "Approve introduction email draft to initiate warmup."
                lead.last_updated = datetime.utcnow()
                
            reset_count += 1
            
        db.commit()
        print(f"Successfully reset {reset_count} legacy sent emails back to DRAFT and leads back to DISCOVERED.")
        
    except Exception as e:
        db.rollback()
        print(f"Error resetting legacy emails: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    reset()
