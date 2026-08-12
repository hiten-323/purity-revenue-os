class TenderGemPortalAgent:
    def __init__(self, provider):
        self.provider = provider
        
    async def analyze(self) -> dict:
        return {"status": "monitoring", "tenders_found": 2}

    async def scan_for_tenders(self) -> list[dict]:
        return [
            {
                "title": "Ministry of Railways Pantry Coffee Supply",
                "estimated_value": 25000000,
                "days_to_deadline": 20,
                "win_probability": "high",
                "purity_beans_eligible": True,
                "deadline": "20 Jul 2026",
                "location": "New Delhi",
                "department": "Ministry of Railways"
            },
            {
                "title": "Department of Health Services Instant Coffee Canteen Supply",
                "estimated_value": 12000000,
                "days_to_deadline": 12,
                "win_probability": "medium",
                "purity_beans_eligible": True,
                "deadline": "30 Jun 2026",
                "location": "Mumbai",
                "department": "Department of Health Services"
            }
        ]

# Alias for V2 import compatibility
GeMPortalAgent = TenderGemPortalAgent
