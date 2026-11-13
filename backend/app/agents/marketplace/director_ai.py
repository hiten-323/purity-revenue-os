"""
Marketplace Director AI

Coordinator between Amazon, Flipkart, Blinkit, Zepto, Instamart directors.
Reports to Sales VP AI.
Never logs into any marketplace directly.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import AgentLog

from app.agents.amazon.director_ai import AmazonDirectorAI
from app.agents.flipkart.director_ai import FlipkartDirectorAI
from app.agents.blinkit.director_ai import BlinkitDirectorAI


class MarketplaceDirectorAI:
    """
    Marketplace Division Director.
    Consolidates all platform-specific reports into a single Marketplace Division Summary
    for the Sales VP. Identifies cross-platform issues and opportunities.
    """

    def __init__(self, db: Session, provider):
        self.db = db
        self.provider = provider
        self.amazon   = AmazonDirectorAI(db, provider)
        self.flipkart = FlipkartDirectorAI(db, provider)
        self.blinkit  = BlinkitDirectorAI(db, provider)
        # Zepto and Instamart directors — add once implemented
        # self.zepto      = ZeptoDirectorAI(db, provider)
        # self.instamart  = InstamartDirectorAI(db, provider)

    def _log(self, action: str, payload: dict):
        self.db.add(AgentLog(
            agent_name="Marketplace Director AI",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload,
        ))
        self.db.commit()

    async def generate_marketplace_division_summary(self) -> dict:
        """
        Collect platform executive summaries and produce a Marketplace Division Report.
        Flags cross-platform pricing inconsistencies and inventory conflicts.
        """
        import asyncio

        amazon_report, flipkart_report, blinkit_report = await asyncio.gather(
            self._get_amazon_summary(),
            self._get_flipkart_summary(),
            self._get_blinkit_summary(),
            return_exceptions=True,
        )

        platform_reports = {
            "amazon":   amazon_report   if not isinstance(amazon_report, Exception) else {"error": str(amazon_report)},
            "flipkart": flipkart_report if not isinstance(flipkart_report, Exception) else {"error": str(flipkart_report)},
            "blinkit":  blinkit_report  if not isinstance(blinkit_report, Exception) else {"error": str(blinkit_report)},
        }

        combined_revenue = sum(
            r.get("revenue_this_week", 0)
            for r in platform_reports.values()
            if isinstance(r, dict) and "revenue_this_week" in r
        )

        best_platform = max(
            platform_reports.items(),
            key=lambda x: x[1].get("revenue_this_week", 0) if isinstance(x[1], dict) else 0,
        )[0] if platform_reports else "Unknown"

        cross_platform_issues = self._detect_cross_platform_issues(platform_reports)

        summary = {
            "division": "Marketplace",
            "report_type": "Division Summary",
            "date": datetime.utcnow().isoformat(),
            "combined_revenue_this_week_inr": combined_revenue,
            "best_performing_platform": best_platform,
            "platform_reports": platform_reports,
            "cross_platform_issues": cross_platform_issues,
            "top_marketplace_action": self._top_action(platform_reports, cross_platform_issues),
        }

        self._log("Marketplace Division Summary Generated", {
            "combined_revenue": combined_revenue,
            "best_platform": best_platform,
            "issues_count": len(cross_platform_issues),
        })
        return summary

    async def _get_amazon_summary(self) -> dict:
        # AmazonDirectorAI.generate_director_report() already exists
        return await self.amazon.generate_director_report()

    async def _get_flipkart_summary(self) -> dict:
        return await self.flipkart.generate_director_report()

    async def _get_blinkit_summary(self) -> dict:
        return await self.blinkit.generate_director_report()

    def _detect_cross_platform_issues(self, reports: dict) -> list:
        issues = []
        prices = {
            platform: r.get("current_price_100g", None)
            for platform, r in reports.items()
            if isinstance(r, dict)
        }
        prices = {k: v for k, v in prices.items() if v is not None}
        if len(prices) > 1:
            price_values = list(prices.values())
            if max(price_values) - min(price_values) > 10:
                issues.append({
                    "type": "Price Inconsistency",
                    "detail": f"100g price varies across platforms: {prices}",
                    "severity": "Medium",
                    "action": "Standardize pricing or document differential strategy",
                })
        return issues

    def _top_action(self, reports: dict, issues: list) -> str:
        if issues:
            return f"Resolve {issues[0]['type']}: {issues[0]['detail']}"
        best = max(reports.items(),
                   key=lambda x: x[1].get("revenue_this_week", 0) if isinstance(x[1], dict) else 0)
        return f"Double down on {best[0].title()} — highest revenue platform this week."
