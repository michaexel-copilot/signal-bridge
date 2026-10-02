"""Latest SRC signal per asset, read from the dashboard snapshot."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

LATEST = """
SELECT asset_key, date, signal, composite, confidence
FROM signals
QUALIFY row_number() OVER (PARTITION BY asset_key ORDER BY date DESC) = 1
ORDER BY asset_key
"""


@dataclass(frozen=True)
class Signal:
    asset_key: str          # "<class>:<symbol>", e.g. "stock:NVDA"
    date: date
    signal: str             # BUY, HOLD, SELL or NO SIGNAL
    composite: float | None
    confidence: float | None

    @property
    def asset_class(self) -> str:
        return self.asset_key.split(":", 1)[0]

    @property
    def symbol(self) -> str:
        return self.asset_key.split(":", 1)[1]


def latest(db: Path, retries: int = 10) -> list[Signal]:
    if not db.is_file():
        raise FileNotFoundError(f"SRC snapshot not found: {db}")
    # The pipeline swaps the snapshot file in when a run ends; retry if we catch that moment.
    for attempt in range(retries):
        try:
            con = duckdb.connect(str(db), read_only=True)
            break
        except duckdb.IOException:
            if attempt == retries - 1:
                raise
            time.sleep(1)
    try:
        return [Signal(*row) for row in con.execute(LATEST).fetchall()]
    finally:
        con.close()
