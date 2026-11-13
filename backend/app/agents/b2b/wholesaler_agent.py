class B2BWholesalerAgent:
    def __init__(self, provider):
        self.provider = provider
        
    async def analyze(self):
        return {"status": "healthy", "leads": 5}
