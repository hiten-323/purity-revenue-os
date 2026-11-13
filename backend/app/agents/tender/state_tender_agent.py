class TenderStateTenderAgent:
    def __init__(self, provider):
        self.provider = provider
        
    async def analyze(self) -> dict:
        return {"status": "monitoring", "tenders_found": 1}

    async def scan_state_tenders(self) -> list[dict]:
        return [
            {
                "title": "Karnataka State F&B Procurement Canteen Supply",
                "estimated_value": 3000000,
                "days_to_deadline": 25,
                "win_probability": "medium",
                "purity_beans_eligible": True,
                "deadline": "12 Jul 2026",
                "location": "Bengaluru",
                "department": "Karnataka F&B Board"
            }
        ]

# Alias for V2 import compatibility
StateTenderAgent = TenderStateTenderAgent
