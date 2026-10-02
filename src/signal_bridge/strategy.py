"""Turn the latest SRC signals and the current portfolio into order intents.

Rules (one portfolio, long only, market orders):
- SELL on a held asset: sell the whole position.
- BUY on an asset not held: open a position worth `position_size_pct` of the portfolio value,
  strongest composite first, while fewer than `max_positions` are held.
- HOLD, NO SIGNAL, stale signals and BUYs below `min_confidence` change nothing.
Pure: no I/O, so the rules are testable on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable

from .config import Strategy
from .signals import Signal


@dataclass
class Intent:
    signal: Signal
    side: str                         # "buy" or "sell"
    asset: dict                       # platform asset (id, symbol, quantity_step, ...)
    quantity: Decimal | None = None   # sells: the free quantity held
    target_value: Decimal | None = None  # buys: value in the portfolio's base currency


@dataclass
class Skip:
    signal: Signal
    reason: str


def held_quantities(positions: list[dict]) -> dict[int, Decimal]:
    """Asset id -> quantity not committed to open sell orders."""
    out = {}
    for p in positions:
        free = Decimal(p["quantity"]) - Decimal(p.get("committed") or 0)
        if free > 0:
            out[p["asset"]["id"]] = free
    return out


def plan(
    signals: list[Signal],
    positions: list[dict],
    total_value: Decimal,
    held_asset: Callable[[Signal], dict | None],
    buyable_asset: Callable[[Signal], dict | str],
    strategy: Strategy,
    today: date,
) -> tuple[list[Intent], list[Skip]]:
    """`held_asset` finds a signal's asset among existing positions' assets; `buyable_asset`
    finds (or adds) it in the catalog and returns the asset or a reason it cannot be traded."""
    held = held_quantities(positions)
    intents: list[Intent] = []
    skips: list[Skip] = []

    fresh = []
    for s in signals:
        if s.asset_class not in strategy.asset_classes or s.signal not in ("BUY", "SELL"):
            continue
        age = (today - s.date).days
        if age > strategy.max_signal_age_days:
            skips.append(Skip(s, f"signal is {age} days old"))
        else:
            fresh.append(s)

    sold = 0
    for s in (s for s in fresh if s.signal == "SELL"):
        asset = held_asset(s)
        if asset is not None and asset["id"] in held:
            intents.append(Intent(s, "sell", asset, quantity=held[asset["id"]]))
            sold += 1

    slots = strategy.max_positions - (len(held) - sold)
    target = (total_value * strategy.position_size_pct / 100).quantize(Decimal("0.01"))
    buys = sorted((s for s in fresh if s.signal == "BUY"), key=lambda s: -(s.composite or 0))
    for s in buys:
        asset = held_asset(s)
        if asset is not None and asset["id"] in held:
            continue  # already in the portfolio
        if (s.confidence or 0) < strategy.min_confidence:
            skips.append(Skip(s, f"confidence {s.confidence} below {strategy.min_confidence}"))
            continue
        if slots <= 0:
            skips.append(Skip(s, f"max_positions ({strategy.max_positions}) reached"))
            continue
        found = buyable_asset(s)
        if isinstance(found, str):
            skips.append(Skip(s, found))
            continue
        if found["id"] in held:
            continue
        intents.append(Intent(s, "buy", found, target_value=target))
        slots -= 1
    return intents, skips
