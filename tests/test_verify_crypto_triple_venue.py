import pytest

from scripts.verify_crypto_triple_venue import verify_crypto_triple_venue


@pytest.mark.asyncio
async def test_verify_crypto_triple_venue_outputs_proof(tmp_path, capsys):
    db_path = str(tmp_path / "runtime_crypto_triple_venue.db")

    report = await verify_crypto_triple_venue(
        db_path=db_path,
        timeout_seconds=20.0,
        emit_report=True,
    )
    captured = capsys.readouterr().out

    assert "POLYMARKET_WALLET_BEFORE" in captured
    assert "BINANCE_FUTURES_WALLET_BEFORE" in captured
    assert "BINANCE_SPOT_WALLET_BEFORE" in captured
    assert "TRADES" in captured
    assert "POSITIONS" in captured
    assert "ORDERS" in captured
    assert "LOG_EXCERPT" in captured
    assert report["polymarket_wallet_after"] < report["polymarket_wallet_before"]
    assert report["binance_futures_wallet_after"] < report["binance_futures_wallet_before"]
    assert report["binance_spot_wallet_after"] < report["binance_spot_wallet_before"]
    assert any(order["venue"] == "binance_futures" and order["order_type"] == "STOP_LOSS" for order in report["orders"])
    assert any(order["venue"] == "binance_spot" and order["order_type"] == "TAKE_PROFIT" for order in report["orders"])
