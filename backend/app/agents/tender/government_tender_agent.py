class TenderGovernmentTenderAgent:
    def __init__(self, provider):
        self.provider = provider
        
    async def analyze(self) -> dict:
        return {"status": "monitoring", "tenders_found": 2}

    async def scan_central_tenders(self) -> list[dict]:
        return [
            {
                "title": "Department of Education Pantry Refreshment Bids",
                "estimated_value": 8000000,
                "days_to_deadline": 15,
                "win_probability": "low",
                "purity_beans_eligible": True,
                "deadline": "15 Jun 2026",
                "location": "New Delhi",
                "department": "Department of Education"
            }
        ]

# Alias for V2 import compatibility
GovernmentTenderAgent = TenderGovernmentTenderAgent
