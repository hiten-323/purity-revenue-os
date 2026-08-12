class TenderPsuTenderAgent:
    def __init__(self, provider):
        self.provider = provider
        
    async def analyze(self) -> dict:
        return {"status": "monitoring", "tenders_found": 1}

    async def scan_psu_tenders(self) -> list[dict]:
        return [
            {
                "title": "ONGC Corporate Headquarters Office Coffee Procurement",
                "estimated_value": 4200000,
                "days_to_deadline": 8,
                "win_probability": "high",
                "purity_beans_eligible": True,
                "deadline": "25 Jun 2026",
                "location": "Dehradun",
                "department": "ONGC India"
            }
        ]

# Alias for V2 import compatibility
PSUTenderAgent = TenderPsuTenderAgent
