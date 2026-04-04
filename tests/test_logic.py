import pytest
from src.logic import calculate_black_scholes_prob, calculate_edge, calculate_rsi, calculate_annualized_volatility
import pandas as pd
import numpy as np

def test_calculate_rsi():
    prices = pd.Series([10, 11, 10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 9, 8])
    rsi = calculate_rsi(prices, period=14)
    assert len(rsi) == 15
    assert not np.isnan(rsi.iloc[-1])

def test_calculate_black_scholes_prob():
    # S=100, K=105, T=0.5, sigma=0.2, r=0
    # Expected: N(d2)
    prob = calculate_black_scholes_prob(100, 105, 0.5, 0.2, 0)
    assert 0 < prob < 1
    # Prob should be < 0.5 since strike is above current price
    assert prob < 0.5

def test_calculate_edge():
    # Polymarket price $0.40 (40%), Implied prob 60%
    edge = calculate_edge(0.40, 0.60)
    assert edge == pytest.approx(0.20)

def test_calculate_annualized_volatility():
    prices = pd.Series([100, 101, 102, 101, 100, 99, 100] * 200) # Simple series
    hv = calculate_annualized_volatility(prices, sampling_period_minutes=1)
    assert hv > 0
