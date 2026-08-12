class ProductAgent:
    def __init__(self, sku: str, name: str):
        self.sku = sku
        self.name = name

    def analyze_performance(self):
        pass

class PurityBeansInstantCoffeeAgent(ProductAgent):
    def __init__(self):
        super().__init__(sku="PB-INST-50G", name="Purity Beans Instant Coffee 50g")

    def analyze_performance(self):
        return {
            "sku": self.sku,
            "status": "Healthy",
            "notes": "Best seller across all regions."
        }
