"""
B2B Director AI — Version 2

Structural fix: Director coordinates active pipeline stages (Discovery -> Qualification -> Appointment Setting -> Sampling -> Conversion)
rather than just collecting static reports.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import AgentLog, B2BLead, Sale, Product
from app.services.crm_tracker import CRMTrackerService
from app.agents.b2b.appointment_setting_agent import AppointmentSettingAgent, AppointmentRequest
from app.agents.b2b.sampling_workflow_agent import SamplingWorkflowAgent, SampleRequest

class B2BDirectorAIV2:
    """
    Executive B2B Director.
    Orchestrates the active B2B sales pipeline stages and coordinates tender tracking.
    """

    def __init__(self, db: Session, provider):
        self.db = db
        self.provider = provider

        # Import division directors (lazy to avoid circular deps)
        # DemandDiscoveryDirector removed: it existed only to run six agents
        # that emitted hand-written businesses with invented contact names,
        # phones and emails, plus a seed file of 10 fabricated leads. Real
        # discovery is services/lead_discovery.py against the Google Places API.
        from app.agents.tender.tender_director_v2 import TenderDirectorV2

        self.tender           = TenderDirectorV2(db, provider)
        self.appointment_agent = AppointmentSettingAgent()
        self.sampling_agent    = SamplingWorkflowAgent()

    def _log(self, action: str, payload: dict):
        self.db.add(AgentLog(
            agent_name="B2B Director AI v2",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload,
        ))
        self.db.commit()

    async def run_pipeline_step(self):
        """
        DISABLED — this was a pipeline SIMULATOR, not a pipeline.

        It walked every lead through the funnel on random draws and wrote the
        results to the CRM as fact:

            EMAIL_SENT  -> REPLIED           random.random() < 0.65, with an
                                             invented customer quote
            REPLIED     -> MEETING_BOOKED    < 0.20
            MEETING     -> COMPLETED         < 0.75
            COMPLETED   -> SAMPLE_SENT       < 0.80
            PROPOSAL    -> ORDER_WON         < lead.probability

        The non-random steps were no better: QUALIFIED -> EMAIL_SENT stamped
        "Outbound Email Sent" on a lead without an email ever being sent.

        It was reachable from a live endpoint —
        GET /founder/dashboard -> FounderDashboardAI -> SalesVPAI ->
        generate_director_report -> generate_b2b_executive_report -> here —
        so simply loading the founder dashboard would have fabricated replies,
        meetings, samples and WON ORDERS across the whole database, and every
        revenue figure reads those statuses.

        Real stage transitions happen only through recorded founder actions:
        /b2b/outreach/execute, /b2b/leads/{id}/confirm-action,
        /b2b/leads/{id}/record-outcome and /b2b/leads/{id}/mark-won, each of
        which writes an immutable WorkflowEvent.

        Kept as a no-op rather than deleted because it sits on a live call
        chain; the caller still works, it just no longer invents a funnel.
        """
        return {"status": "disabled",
                "reason": "pipeline simulation removed — stages advance only on real founder actions"}

    async def generate_b2b_executive_report(self) -> dict:
        """
        Collect all division reports and synthesize the B2B Executive Report.
        """
        # Demand discovery scan removed with the fabricated-lead agents.
        # Discovery now runs through POST /discovery/run (Google Places).
        demand_report = {}
        
        # 2. Advance the entire pipeline loop statefully
        await self.run_pipeline_step()
        
        # 3. Retrieve active leads from database
        leads = CRMTrackerService.get_all_leads(self.db)
        
        # Gather top opportunities to display
        top_5_opps = []
        for lead in leads[:5]:
            top_5_opps.append({
                "source": "B2B - " + lead.division.upper(),
                "company": lead.company,
                "city": lead.city,
                "estimated_annual_value": lead.estimated_value,
                "action": lead.qualification_notes or "Review prospect",
                "owner_division": lead.division.upper(),
                "status": lead.status,
                "score": lead.score
            })
            
        kpis = CRMTrackerService.get_kpis(self.db)
        
        # 4. Run Tender Director Scan
        tender_summary = await self.tender.generate_executive_summary()
        tender_bids = tender_summary.get("bid_now", []) if isinstance(tender_summary, dict) else []

        report = {
            "division": "B2B",
            "report_type": "Executive Report",
            "date": datetime.utcnow().isoformat(),
            "pipeline_summary": {
                "total_pipeline_value_inr": kpis["pipeline_value_inr"],
                "hot_leads_requiring_action": kpis["leads_discovered"],
                "tender_bids_due_this_week": len(tender_bids),
                "meetings_booked": kpis["meetings_booked"],
                "samples_sent": kpis["samples_sent"],
                "orders_won": kpis["orders_won"]
            },
            "top_5_opportunities": top_5_opps,
            # Empty rather than invented: this fed off the fabricated-lead scan.
            "demand_discovery_highlight": demand_report.get("summary", {}),
            "tender_summary": {
                "overview": tender_summary.get("portfolio_overview", {}) if isinstance(tender_summary, dict) else {},
                "strategic_note": tender_summary.get("strategic_note", "") if isinstance(tender_summary, dict) else "",
            },
        }

        self._log("B2B Executive Report Generated", report["pipeline_summary"])
        return report

    async def generate_director_report(self) -> dict:
        """
        V1 backward compatibility wrapper.
        """
        v2_report = await self.generate_b2b_executive_report()
        summary = v2_report.get("pipeline_summary", {})
        top_opps = v2_report.get("top_5_opportunities", [])
        
        top_opp_name = "Corporate Canteen Contract - Tech Park"
        if top_opps:
            top_opp_name = f"{top_opps[0].get('company', '')} ({top_opps[0].get('status')}) - {top_opps[0].get('action', '')}"
            
        return {
            "division": "B2B",
            "pipeline_value": summary.get("total_pipeline_value_inr", 0),
            "new_leads": summary.get("hot_leads_requiring_action", 0),
            "hot_leads": summary.get("hot_leads_requiring_action", 0),
            "top_opportunity": top_opp_name,
            "recommended_actions": [
                f"{o.get('company')} ({o.get('status')}): {o.get('action')} (₹{o.get('estimated_annual_value', 0):,})"
                for o in top_opps
            ] or ["Meet Distributor XYZ"]
        }
