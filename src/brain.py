from src.decision_engine import DecisionEngine, DecisionInputs


class Brain:
    def __init__(self, model: str = "deterministic-local", decision_engine: DecisionEngine | None = None):
        self.model = model
        self.decision_engine = decision_engine or DecisionEngine()

    async def get_confidence(
        self,
        edge,
        rsi,
        volume_24h,
        price_delta_5m=0.0,
        category: str = "CRYPTO",
        spread_pct: float = 0.0,
    ) -> float:
        decision = self.decision_engine.score_discovery(
            DecisionInputs(
                source="brain",
                category=category,
                market_id="brain-evaluation",
                volume_24h=volume_24h,
                mid_price=0.5,
                spread_pct=spread_pct,
                edge=edge,
                rsi=rsi,
            )
        )
        return decision.score
