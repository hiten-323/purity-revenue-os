from sqlalchemy.orm import Session
from app.services.crm_tracker import CRMTrackerService
from app.models.models import AgentLog
from datetime import datetime

class WinLossAnalysisAgent:
    """
    Dedicated Win-Loss Audit Agent.
    Audits lost opportunities (COLD status) and outputs actionable pricing, sensory,
    and competitor suggestions to maximize revenue and close rates.
    """

    def __init__(self, db: Session):
        self.db = db

    def _log(self, action: str, payload: dict):
        self.db.add(AgentLog(
            agent_name="Win-Loss Analyst AI",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload,
        ))
        self.db.commit()

    def audit_lost_deals(self) -> dict:
        """
        Runs a full audit of all lost deals in the SQLite B2B CRM.
        Logs the audit results and returns analytics + strategic recommendations.
        """
        audit_results = CRMTrackerService.get_win_loss_analytics(self.db)
        
        # Log this audit action in the system logs
        self._log(
            action="Audit B2B Lost Deals",
            payload={
                "total_lost": audit_results["total_lost"],
                "top_reason": audit_results["top_reason"]
            }
        )
        
        return audit_results
