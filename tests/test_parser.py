import pytest
from src.parser import parse_polymarket_question
from datetime import datetime

def test_parse_polymarket_question():
    q1 = "Will Bitcoin be above $70,000 at March 31, 2024?"
    strike1, expiry1 = parse_polymarket_question(q1)
    assert strike1 == 70000.0
    assert expiry1.year == 2024
    assert expiry1.month == 3
    assert expiry1.day == 31

    q2 = "Will ETH be above 3500 at Apr 15, 2024?"
    strike2, expiry2 = parse_polymarket_question(q2)
    assert strike2 == 3500.0
    assert expiry2.month == 4
    assert expiry2.day == 15

    q3 = "Will SOL be above $150.50 at May 1, 2024?"
    strike3, expiry3 = parse_polymarket_question(q3)
    assert strike3 == 150.50
    assert expiry3.month == 5
    assert expiry3.day == 1
