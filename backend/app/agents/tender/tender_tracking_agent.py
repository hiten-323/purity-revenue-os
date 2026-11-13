class TenderTenderTrackingAgent:
    def __init__(self, provider):
        self.provider = provider
        
    async def analyze(self):
        return {"status": "monitoring", "tenders_found": 2}
