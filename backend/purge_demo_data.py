import sys, os
from sqlalchemy import text
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from app.database.database import SessionLocal, engine
import logging

DEMO_NAMES = [
    "Catalyst Trading",
    "Prime Group",
    "Omni Foods",
    "Vertex Capital"
]

def purge():
    db = SessionLocal()
    try:
        print("Purging demo data...")
        
        # 1. Get lead IDs of demo companies
        lead_ids = []
        for name in DEMO_NAMES:
            rows = db.execute(text("SELECT id FROM b2b_leads WHERE company = :name"), {"name": name}).fetchall()
            lead_ids.extend([r[0] for r in rows])
            
        print(f"Matched lead IDs to purge: {lead_ids}")
        
        if lead_ids:
            ids_str = ",".join(str(i) for i in lead_ids)
            
            # Delete related EmailDrafts
            deleted_drafts = db.execute(
                text(f"DELETE FROM email_drafts WHERE lead_id IN ({ids_str})")
            ).rowcount
            print(f"Deleted {deleted_drafts} associated email drafts.")
            
            # Delete related WorkflowEvents
            deleted_events = db.execute(
                text(f"DELETE FROM workflow_events WHERE lead_id IN ({ids_str})")
            ).rowcount
            print(f"Deleted {deleted_events} associated workflow events.")
            
            # Delete related ActionQueue items
            deleted_queue = db.execute(
                text(f"DELETE FROM action_queue WHERE lead_id IN ({ids_str})")
            ).rowcount
            print(f"Deleted {deleted_queue} associated action queue items.")
            
            # Delete related OutreachReminders
            deleted_reminders = db.execute(
                text(f"DELETE FROM outreach_reminders WHERE lead_id IN ({ids_str})")
            ).rowcount
            print(f"Deleted {deleted_reminders} associated outreach reminders.")
            
            # Delete related Revenue Opportunities
            deleted_opps = db.execute(
                text(f"DELETE FROM revenue_opportunities WHERE lead_id IN ({ids_str})")
            ).rowcount
            print(f"Deleted {deleted_opps} associated opportunities.")
            
            # Delete from b2b_leads
            deleted_leads = db.execute(
                text(f"DELETE FROM b2b_leads WHERE id IN ({ids_str})")
            ).rowcount
            print(f"Deleted {deleted_leads} leads from b2b_leads.")
            
        # 2. Delete from organizations
        names_str = ",".join(f"'{name}'" for name in DEMO_NAMES)
        deleted_orgs = db.execute(
            text(f"DELETE FROM organizations WHERE name IN ({names_str})")
        ).rowcount
        print(f"Deleted {deleted_orgs} organizations.")
        
        db.commit()
        print("Demo data purge completed successfully.")
        
    except Exception as e:
        db.rollback()
        print(f"Error purging demo data: {str(e)}")
        logging.error(f"Error purging demo data: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    purge()
