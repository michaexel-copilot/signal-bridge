from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from signal_bridge.config import Config
from signal_bridge.ptp import PTPError
from signal_bridge.runner import Bridge, floor_to

TODAY = date(2026, 10, 2)
SIGNAL_DAY = date(2026, 10, 1)


def make_db(path: Path, rows):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE signals (asset_key VARCHAR, date DATE, signal VARCHAR, composite DOUBLE, "
                "confidence DOUBLE, subscores VARCHAR, drivers VARCHAR, flags VARCHAR, facts VARCHAR, "
                "narrative VARCHAR, created_at TIMESTAMP)")
    for row in rows:
        con.execute("INSERT INTO signals (asset_key, date, signal, composite, confidence) "
                    "VALUES (?, ?, ?, ?, ?)", row)
    con.close()


class FakeClient:
    """Enough of the platform: fee 0.1 %, NVDA at 100, BTC at 50000."""

    def __init__(self, market_open=True, held=()):
        self.market_open = market_open
        self.placed, self.orders_list = [], []
        self.assets_list = [
            {"id": 1, "symbol": "BTC", "asset_class": "crypto", "quantity_step": "0.00000001",
             "min_order_size": None, "available": True},
            {"id": 2, "symbol": "NVDA", "asset_class": "stocks", "quantity_step": "1",
             "min_order_size": None, "available": True},
        ]
        self.positions = [{"asset": {"id": a}, "quantity": q, "committed": "0"} for a, q in held]

    def login(self, *_):
        pass

    def portfolios(self):
        return [{"id": 7, "name": "SRC signals"}]

    def portfolio(self, _):
        return {"id": 7, "available_cash": "100000", "total_value": "100000",
                "positions": self.positions}

    def orders(self, _):
        return self.orders_list

    def assets(self):
        return self.assets_list

    def search(self, q):
        return []

    def price(self, asset_id):
        return Decimal(100 if asset_id == 2 else 50000)

    def quote(self, asset_id):
        return {"market_open": self.market_open, "fresh": True, "ask": str(self.price(asset_id)),
                "last": str(self.price(asset_id)), "next_open": "2026-10-02T13:30:00Z"}

    def preview(self, pid, asset_id, side, qty):
        return {"cash_change": str(-(self.price(asset_id) * qty * Decimal("1.001")))}

    def market_order(self, pid, asset_id, side, qty, client_order_id):
        self.placed.append((asset_id, side, qty, client_order_id))
        order = {"id": len(self.placed), "status": "filled", "client_order_id": client_order_id}
        self.orders_list.append(order)
        return order


def config(tmp_path, rows):
    db = tmp_path / "src.duckdb"
    make_db(db, rows)
    return Config(src_db=db, ptp_url="http://x", email="e", password="p", journal=tmp_path / "j.jsonl")


def test_buys_within_budget_and_is_idempotent(tmp_path):
    cfg = config(tmp_path, [("stock:NVDA", SIGNAL_DAY, "BUY", 70, 0.7),
                            ("crypto:BTC", SIGNAL_DAY, "BUY", 66, 0.7)])
    client = FakeClient()
    outcomes = Bridge(cfg, client, today=TODAY).run()
    assert [o.status for o in outcomes] == ["filled", "filled"]
    nvda, btc = client.placed
    assert nvda[:3] == (2, "buy", Decimal(49))  # 49 * 100 * 1.001 <= 5000 < 50 * 100 * 1.001
    assert btc[2] * 50000 * Decimal("1.001") <= 5000
    assert nvda[3] == "src-buy-stock:NVDA-20261001"
    # Same signals again, before the positions show up: nothing new is placed.
    again = Bridge(cfg, client, today=TODAY).run()
    assert [o.status for o in again] == ["done", "done"] and len(client.placed) == 2
    assert len(cfg.journal.read_text().splitlines()) == 2


def test_market_closed_defers(tmp_path):
    cfg = config(tmp_path, [("stock:NVDA", SIGNAL_DAY, "BUY", 70, 0.7)])
    outcomes = Bridge(cfg, FakeClient(market_open=False), today=TODAY).run()
    assert (outcomes[0].action, outcomes[0].status) == ("buy", "deferred")


def test_sell_closes_position_and_dry_run_places_nothing(tmp_path):
    cfg = config(tmp_path, [("stock:NVDA", SIGNAL_DAY, "SELL", 30, 0.7)])
    client = FakeClient(held=[(2, "12")])
    dry = Bridge(cfg, client, dry_run=True, today=TODAY).run()
    assert dry[0].status == "planned" and client.placed == []
    Bridge(cfg, client, today=TODAY).run()
    assert client.placed == [(2, "sell", Decimal(12), "src-sell-stock:NVDA-20261001")]


def test_rejected_order_is_retried_with_new_id(tmp_path):
    cfg = config(tmp_path, [("stock:NVDA", SIGNAL_DAY, "SELL", 30, 0.7)])
    client = FakeClient(held=[(2, "1")])
    client.orders_list = [{"id": 9, "status": "rejected",
                           "client_order_id": "src-sell-stock:NVDA-20261001"}]
    Bridge(cfg, client, today=TODAY).run()
    assert client.placed[0][3] == "src-sell-stock:NVDA-20261001-2"


def test_platform_refusal_is_reported_as_error(tmp_path):
    cfg = config(tmp_path, [("stock:NVDA", SIGNAL_DAY, "SELL", 30, 0.7)])
    client = FakeClient(held=[(2, "1")])

    def refuse(*_):
        raise PTPError(422, "insufficient_holdings", "nope")

    client.market_order = refuse
    outcome = Bridge(cfg, client, today=TODAY).run()[0]
    assert (outcome.status, outcome.detail) == ("error", "nope")


def test_status_lists_signals_not_traded_yet(tmp_path, capsys):
    from signal_bridge.cli import cmd_status

    cfg = config(tmp_path, [("stock:NVDA", date.today(), "BUY", 70, 0.7),
                            ("crypto:BTC", date.today(), "BUY", 66, 0.7)])
    client = FakeClient(market_open=False)
    client.portfolio = lambda _: {
        "id": 7, "name": "SRC signals", "base_currency": "USD", "total_value": "100000",
        "total_return_pct": "0", "cash": "100000", "fees_paid": "0", "available_cash": "100000",
        "positions": []}
    assert cmd_status(cfg, client) == 0
    out = capsys.readouterr().out
    assert "Not traded yet" in out
    assert "stock:NVDA" in out and "deferred" in out
    assert client.placed == []


def test_floor_to():
    assert floor_to(Decimal("1.23456789"), Decimal("0.001")) == Decimal("1.234")
