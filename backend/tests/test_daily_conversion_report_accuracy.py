from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead
from app.services.daily_conversion_report import (
    generate_definite_answer_followup_report,
    generate_executive_daily_report,
)


def _db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_daily_report_does_not_count_suppressed_or_delivery_as_sales_interest():
    db = _db()
    try:
        rows = [
            B2BLead(company='Contactable discovery', status='DISCOVERED',
                    contact_status='CONTACTABLE', do_not_call=False),
            B2BLead(company='Needs research', status='DISCOVERED',
                    contact_status='NEEDS_RESEARCH', do_not_call=False),
            B2BLead(company='Global DNC', status='DISCOVERED',
                    contact_status='DO_NOT_CONTACT', do_not_call=False),
            B2BLead(company='Suppressed', status='DISCOVERED',
                    contact_status='SUPPRESSED', do_not_call=False),
            B2BLead(company='Voice DNC', status='DISCOVERED',
                    contact_status='CONTACTABLE', do_not_call=True),
            B2BLead(company='Inbound interest', status='INBOUND_INTEREST',
                    contact_status='CONTACTABLE', do_not_call=False),
            B2BLead(company='WhatsApp sent only', status='WHATSAPP_SENT',
                    contact_status='CONTACTABLE', do_not_call=False),
            B2BLead(company='WhatsApp consent only', status='WHATSAPP_OPTED_IN',
                    contact_status='CONTACTABLE', do_not_call=False),
            B2BLead(company='Sample requested', status='SAMPLE_REQUESTED',
                    contact_status='CONTACTABLE', do_not_call=False,
                    sample_requested=True),
            B2BLead(company='Proposal and sample', status='PROPOSAL_SENT',
                    contact_status='CONTACTABLE', do_not_call=False,
                    sample_requested=True),
        ]
        db.add_all(rows)
        db.commit()

        followup = generate_definite_answer_followup_report(db)
        daily = generate_executive_daily_report(db)

        # Only contactable/researchable, non-DNC accounts are open.
        assert followup['1_open_leads_count'] == 7
        # Delivery and WhatsApp consent are not commercial buying intent.
        assert followup['8_positive_response_count'] == 3
        # A proposal+sample on one account is one account, not two.
        assert followup['9_quotes_and_samples_count'] == 2
        # Count the union of global suppression and channel DNC exactly once.
        assert followup['10_opted_out_count'] == 3
        # Suppressed records must not inflate the actionable high-intent pool.
        assert daily['3_contact_eligible_leads'] <= 7
        assert daily['3b_voice_eligible_candidates'] == 0
        assert daily['3c_priced_b2b_proposals'] == 0
        assert 'B2B wholesale price' in daily['10_biggest_bottleneck']
    finally:
        db.close()
