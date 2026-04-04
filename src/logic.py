import numpy as np
import scipy.stats as si
import pandas as pd

def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_black_scholes_prob(S, K, T, sigma, r=0):
    """
    Calculates the probability of S > K at time T.
    Using the d2 component of Black-Scholes for risk-neutral probability.
    Units for T and sigma must match (e.g., both annualized).
    """
    if T <= 0:
        return 1.0 if S > K else 0.0

    if sigma <= 0:
        return 1.0 if S > K else 0.0

    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    return float(si.norm.cdf(d2))

def calculate_annualized_volatility(prices, sampling_period_minutes=1):
    """
    Annualizes historical volatility.
    returns = ln(P_t / P_{t-1})
    annual_vol = std(returns) * sqrt(minutes_in_year / sampling_period_minutes)
    """
    returns = np.log(prices / prices.shift(1)).dropna()
    minutes_in_year = 365 * 24 * 60
    return float(returns.std() * np.sqrt(minutes_in_year / sampling_period_minutes))

def calculate_edge(polymarket_price, implied_prob):
    return implied_prob - polymarket_price
