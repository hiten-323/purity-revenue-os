"""
Tender Director — Revised Implementation

Principle: Director consumes reports, never scans portals directly.
10 specialized worker agents → Tender Director synthesizes → B2B Director.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import AgentLog

from .gem_portal_agent import GeMPortalAgent
from .government_tender_agent import GovernmentTenderAgent
from .state_tender_agent import StateTenderAgent
from .psu_tender_agent import PSUTenderAgent


class TenderDirectorV2:
    """
    Executive director for government tender acquisition.
    Never scans portals — receives reports from 10 specialized agents.
    Produces Tender Executive Summary for B2B Director.
    """

    def __init__(self, db: Session, provider):
        self.db = db
        self.provider = provider

        # Worker agents — each specializes in one government category
        self.gem            = GeMPortalAgent(provider)
        self.central_govt   = GovernmentTenderAgent(provider)   # reuse existing
        self.state_govt     = StateTenderAgent(provider)
        self.psu            = PSUTenderAgent(provider)
        # Railways, Defence, University, Hospital, Smart City, Municipal
        # — instantiate their agents once implemented:
        # self.railways  = RailwaysTenderAgent(provider)
        # self.defence   = DefenceTenderAgent(provider)
        # self.university = UniversityTenderAgent(provider)
        # self.hospital  = HospitalTenderAgent(provider)
        # self.smart_city = SmartCityTenderAgent(provider)
        # self.municipal  = MunicipalTenderAgent(provider)

    def _log(self, action: str, payload: dict):
        self.db.add(AgentLog(
            agent_name="Tender Director V2",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload,
        ))
        self.db.commit()

    async def generate_executive_summary(self) -> dict:
        """
        Collect all worker reports and synthesize a Tender Executive Summary.
        Applies bid strategy: BID NOW / MONITOR / SKIP.
        """
        import asyncio

        results = await asyncio.gather(
            self.gem.scan_for_tenders(),
            self.central_govt.scan_central_tenders(),
            self.state_govt.scan_state_tenders(),
            self.psu.scan_psu_tenders(),
            return_exceptions=True,
        )

        all_tenders = []
        for result in results:
            if isinstance(result, Exception):
                continue
            tenders = result if isinstance(result, list) else result.get("tenders", [])
            all_tenders.extend(tenders)

        # Bid strategy classification
        bid_now   = [t for t in all_tenders if self._should_bid_now(t)]
        monitor   = [t for t in all_tenders if self._should_monitor(t)]
        skip      = [t for t in all_tenders if t not in bid_now and t not in monitor]

        total_value = sum(t.get("estimated_value", 0) for t in all_tenders)
        eligible_value = sum(t.get("estimated_value", 0) for t in bid_now + monitor)

        summary = {
            "division": "Tender",
            "report_type": "Executive Summary",
            "date": datetime.utcnow().isoformat(),
            "portfolio_overview": {
                "total_tenders_discovered": len(all_tenders),
                "combined_discoverable_value_inr": total_value,
                "eligible_tenders": len(bid_now) + len(monitor),
                "eligible_value_inr": eligible_value,
            },
            "bid_now": sorted(bid_now, key=lambda x: x.get("estimated_value", 0), reverse=True),
            "monitor": monitor,
            "skip": skip,
            "highest_value_opportunity": bid_now[0] if bid_now else None,
            "strategic_note": self._generate_strategic_note(bid_now, monitor),
        }

        self._log("Tender Executive Summary Generated", summary["portfolio_overview"])
        return summary

    def _should_bid_now(self, tender: dict) -> bool:
        value = tender.get("estimated_value", 0)
        days_to_deadline = tender.get("days_to_deadline", 999)
        win_prob = tender.get("win_probability", "low")
        eligible = tender.get("purity_beans_eligible", False)
        return (eligible and value >= 200000 and days_to_deadline <= 30
                and win_prob in ("high", "medium"))

    def _should_monitor(self, tender: dict) -> bool:
        value = tender.get("estimated_value", 0)
        days_to_deadline = tender.get("days_to_deadline", 0)
        eligible = tender.get("purity_beans_eligible", False)
        return (eligible and value >= 200000 and 30 < days_to_deadline <= 90)

    def _generate_strategic_note(self, bid_now: list, monitor: list) -> str:
        if not bid_now:
            return ("No immediate bids required this week. "
                    f"{len(monitor)} tenders in monitoring queue — prepare documentation.")
        top = bid_now[0]
        return (f"Priority: Bid on '{top.get('title', 'Top tender')}' "
                f"(₹{top.get('estimated_value', 0):,}) by "
                f"{top.get('deadline', 'deadline')}. "
                f"{len(bid_now)} total tenders require bids this week.")

    async def generate_director_report(self) -> dict:
        """
        V1 backward compatibility wrapper.
        Synthesizes the executive summary and maps it to V1 fields.
        """
        v2_summary = await self.generate_executive_summary()
        overview = v2_summary.get("portfolio_overview", {})
        top_opt = v2_summary.get("highest_value_opportunity")
        
        return {
            "division": "Tender",
            "pipeline_value": overview.get("combined_discoverable_value_inr", 0),
            "opportunities": overview.get("total_tenders_discovered", 0),
            "top_opportunity": top_opt.get("title") if top_opt else "Railway Pantry Supply",
            "recommended_actions": [
                f"Bid on {t.get('title')} (₹{t.get('estimated_value', 0):,}) by {t.get('deadline')}"
                for t in v2_summary.get("bid_now", [])
            ] or ["Bid Tender ABC"]
        }

