class MarketplaceProvider:
    async def get_sales(self):
        pass
    async def get_inventory(self):
        pass

class AmazonProvider(MarketplaceProvider):
    async def get_sales(self):
        return {"revenue": 22100, "roas": 4.8, "orders": 120}
    
    async def get_inventory(self):
        return {"days_remaining": 18, "stock": 500}
        
    async def get_ads(self):
        return {"spend": 4500, "acos": 20.5}

    async def get_competitors(self):
        return [{"name": "Nescafe", "price": 400}, {"name": "Bru", "price": 380}]

class FlipkartProvider(MarketplaceProvider):
    async def get_sales(self):
        return {"revenue": 8900, "roas": 3.2, "orders": 45}

    async def get_inventory(self):
        return {"days_remaining": 22, "stock": 350}

    async def get_ads(self):
        return {"spend": 1200, "acos": 28.1}

    async def get_competitors(self):
        return [{"name": "Nescafe", "price": 395}, {"name": "Bru", "price": 375}]

class BlinkitProvider(MarketplaceProvider):
    async def get_sales(self):
        return {"revenue": 11800, "roas": 0, "orders": 85}

    async def get_inventory(self):
        return {"days_remaining": 4, "stock": 40}

class B2BProvider:
    async def get_pipeline(self):
        return {
            "value": 1800000,
            "new_leads": 73,
            "hot_leads": 17,
            "corporate_opportunities": 9
        }

class TenderProvider:
    async def get_pipeline(self):
        return {
            "value": 4200000,
            "opportunities": 4
        }
