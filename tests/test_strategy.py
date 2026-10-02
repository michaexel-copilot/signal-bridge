from datetime import date
from decimal import Decimal

from signal_bridge.config import Strategy
from signal_bridge.signals import Signal
from signal_bridge.strategy import plan

TODAY = date(2026, 10, 2)
ASSETS = {"crypto:BTC": {"id": 1, "symbol": "BTC"}, "stock:NVDA": {"id": 2, "symbol": "NVDA"},
          "stock:MU": {"id": 3, "symbol": "MU"}, "stock:XOM": {"id": 4, "symbol": "XOM"}}


def sig(key, signal, composite=70.0, confidence=0.7, day=date(2026, 10, 1)):
    return Signal(key, day, signal, composite, confidence)


def position(asset_id, quantity, committed="0"):
    return {"asset": {"id": asset_id}, "quantity": quantity, "committed": committed}


def run(signals, positions=(), strategy=None, value="100000"):
    def find(s):
        return ASSETS.get(s.asset_key)

    def find_or_add(s):
        return ASSETS.get(s.asset_key) or "unknown"

    return plan(signals, list(positions), Decimal(value), find, find_or_add,
                strategy or Strategy(), TODAY)


def test_buy_opens_position_sized_by_portfolio_value():
    intents, skips = run([sig("stock:NVDA", "BUY")])
    assert [(i.side, i.asset["id"], i.target_value) for i in intents] == [("buy", 2, Decimal("5000.00"))]
    assert skips == []


def test_buy_on_held_asset_does_nothing():
    intents, _ = run([sig("stock:NVDA", "BUY")], [position(2, "10")])
    assert intents == []


def test_sell_closes_free_quantity_and_ignores_unheld():
    intents, _ = run([sig("stock:NVDA", "SELL"), sig("crypto:BTC", "SELL")],
                     [position(2, "10", committed="4")])
    assert [(i.side, i.asset["id"], i.quantity) for i in intents] == [("sell", 2, Decimal("6"))]


def test_hold_and_no_signal_change_nothing():
    intents, skips = run([sig("stock:NVDA", "HOLD"), sig("crypto:BTC", "NO SIGNAL")], [position(2, "1")])
    assert intents == [] and skips == []


def test_stale_signals_are_skipped():
    intents, skips = run([sig("stock:NVDA", "BUY", day=date(2026, 9, 25))])
    assert intents == [] and "days old" in skips[0].reason


def test_max_positions_prefers_strongest_and_counts_sells():
    signals = [sig("stock:MU", "BUY", 66), sig("stock:XOM", "BUY", 71),
               sig("crypto:BTC", "SELL"), sig("stock:NVDA", "HOLD")]
    intents, skips = run(signals, [position(1, "1"), position(2, "1")], Strategy(max_positions=2))
    assert [(i.side, i.signal.asset_key) for i in intents] == [("sell", "crypto:BTC"), ("buy", "stock:XOM")]
    assert [s.signal.asset_key for s in skips] == ["stock:MU"]


def test_min_confidence_and_unknown_assets_are_skipped():
    intents, skips = run([sig("stock:NVDA", "BUY", confidence=0.5), sig("stock:ZZZZ", "BUY")],
                         strategy=Strategy(min_confidence=0.6))
    assert intents == []
    assert {s.signal.asset_key: s.reason for s in skips}["stock:ZZZZ"] == "unknown"


def test_classes_outside_the_config_are_ignored():
    intents, _ = run([sig("crypto:BTC", "BUY")], strategy=Strategy(asset_classes=["stock"]))
    assert intents == []
