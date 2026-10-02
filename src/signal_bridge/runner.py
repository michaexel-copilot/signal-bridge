"""One bridge run: read signals, plan, place market orders, journal the outcome.

Runs are idempotent. Each order carries a client order id derived from the signal
("src-buy-stock:NVDA-20261001"), so a signal is acted on at most once even if runs overlap
or repeat; a rejected attempt gets a numbered retry id on the next run. Orders that cannot
fill now (market closed, no fresh quote) are left for the next run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_FLOOR, Decimal

from . import signals as src
from .catalog import Catalog
from .config import Config
from .ptp import Client, PTPError
from .strategy import Intent, plan

DEFER = {"market_closed", "stale_quote", "no_quote", "no_conversion_rate"}


@dataclass
class Outcome:
    asset_key: str
    signal: str
    signal_date: str
    action: str           # buy, sell or none
    status: str           # filled, rejected, deferred, skipped, planned, done, error
    detail: str = ""
    quantity: str | None = None
    value: str | None = None
    order_id: int | None = None
    extra: dict = field(default_factory=dict)


def floor_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def client_order_id(intent: Intent, attempt: int) -> str:
    base = f"src-{intent.side}-{intent.signal.asset_key}-{intent.signal.date:%Y%m%d}"
    return base if attempt == 1 else f"{base}-{attempt}"


def _next_attempt(intent: Intent, orders_by_client_id: dict[str, dict]) -> int | None:
    """Attempt number to use, or None when this signal was already acted on."""
    attempt = 1
    while (order := orders_by_client_id.get(client_order_id(intent, attempt))) is not None:
        if order["status"] != "rejected":
            return None
        attempt += 1
    return attempt


class Bridge:
    def __init__(self, cfg: Config, client: Client, dry_run: bool = False, today: date | None = None):
        self.cfg, self.client, self.dry_run = cfg, client, dry_run
        self.today = today or date.today()

    def _portfolio(self) -> dict | None:
        for p in self.client.portfolios():
            if p["name"] == self.cfg.portfolio_name:
                return p
        if self.dry_run:
            return None
        return self.client.create_portfolio(
            self.cfg.portfolio_name, self.cfg.base_currency, self.cfg.starting_cash)

    def run(self) -> list[Outcome]:
        self.client.login(self.cfg.email, self.cfg.password)
        summary = self._portfolio()
        if summary is None:
            detail = {"id": None, "positions": [], "available_cash": str(self.cfg.starting_cash),
                      "total_value": str(self.cfg.starting_cash)}
        else:
            detail = self.client.portfolio(summary["id"])
        self.portfolio_id = detail["id"]
        self.available = Decimal(detail["available_cash"])
        orders = self.client.orders(self.portfolio_id) if self.portfolio_id else []
        by_client_id = {o["client_order_id"]: o for o in orders}

        catalog = Catalog(self.client, self.cfg.symbol_map, self.cfg.auto_add_assets, self.dry_run)
        intents, skips = plan(
            src.latest(self.cfg.src_db), detail["positions"], Decimal(detail["total_value"]),
            catalog.find, catalog.find_or_add, self.cfg.strategy, self.today)

        outcomes = [Outcome(s.signal.asset_key, s.signal.signal, s.signal.date.isoformat(),
                            "none", "skipped", s.reason) for s in skips]
        for intent in intents:
            outcomes.append(self._execute(intent, by_client_id))
        if not self.dry_run:
            # Only orders go to the journal; skips and deferrals repeat on every run.
            self._journal([o for o in outcomes if o.order_id is not None or o.status == "error"])
        return outcomes

    def _execute(self, intent: Intent, by_client_id: dict[str, dict]) -> Outcome:
        s = intent.signal
        out = Outcome(s.asset_key, s.signal, s.date.isoformat(), intent.side, "error")
        attempt = _next_attempt(intent, by_client_id)
        if attempt is None:
            out.status, out.detail = "done", "this signal was already traded"
            return out
        try:
            quantity, cost = (self._buy_size(intent) if intent.side == "buy"
                              else (intent.quantity, None))
            out.quantity = f"{quantity.normalize():f}"
            out.value = f"{cost:.2f}" if cost is not None else None
            if quantity <= 0:
                out.status, out.detail = "skipped", "not enough cash for one quantity step"
                return out
            minimum = intent.asset.get("min_order_size")
            if minimum is not None and quantity < Decimal(minimum):
                out.status, out.detail = "skipped", f"below the minimum order size {minimum}"
                return out
            if self.dry_run:
                out.status = "planned"
                return out
            order = self.client.market_order(self.portfolio_id, intent.asset["id"], intent.side,
                                             quantity, client_order_id(intent, attempt))
        except PTPError as exc:
            out.status = "deferred" if exc.code in DEFER else "error"
            out.detail = exc.message
            return out
        out.order_id, out.status = order["id"], order["status"]
        out.detail = order.get("reject_reason") or ""
        if order["status"] == "filled" and cost is not None:
            self.available -= cost
        return out

    def _buy_size(self, intent: Intent) -> tuple[Decimal, Decimal]:
        """Quantity whose cost, fees included, is at most the target value and the free cash."""
        budget = min(intent.target_value, self.available)
        step = Decimal(intent.asset["quantity_step"])
        quote = self.client.quote(intent.asset["id"])
        if not quote["market_open"]:
            raise PTPError(422, "market_closed", f"market closed, next open {quote['next_open']}")
        if not quote["fresh"]:
            raise PTPError(422, "stale_quote", "no fresh quote")
        price = Decimal(quote["ask"] or quote["last"])
        # First guess ignores currency conversion and fees; the preview gives the real cost.
        guess = max(floor_to(budget / price, step), step)
        if self.portfolio_id is None:  # dry run without a portfolio yet: no preview possible
            return floor_to(budget / price, step), budget
        preview = self.client.preview(self.portfolio_id, intent.asset["id"], "buy", guess)
        quantity = floor_to(guess * budget / -Decimal(preview["cash_change"]), step)
        for _ in range(5):  # minimum fees can make cost non-linear; step down until it fits
            if quantity <= 0:
                return quantity, Decimal(0)
            check = self.client.preview(self.portfolio_id, intent.asset["id"], "buy", quantity)
            cost = -Decimal(check["cash_change"])
            if cost <= budget:
                return quantity, cost
            quantity = floor_to(quantity * budget / cost * Decimal("0.995"), step)
        return Decimal(0), Decimal(0)

    def _journal(self, outcomes: list[Outcome]) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.cfg.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.cfg.journal.open("a", encoding="utf-8") as fh:
            for o in outcomes:
                fh.write(json.dumps({"ts": ts, **o.__dict__}) + "\n")
