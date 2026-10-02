"""Settings from bridge.yaml, with credentials from the environment or .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass
class Strategy:
    position_size_pct: Decimal = Decimal("5")
    max_positions: int = 20
    min_confidence: float = 0.0
    max_signal_age_days: int = 3
    asset_classes: list[str] = field(default_factory=lambda: ["crypto", "stock"])


@dataclass
class Config:
    src_db: Path
    ptp_url: str
    email: str
    password: str
    portfolio_name: str = "SRC signals"
    base_currency: str = "USD"
    starting_cash: Decimal = Decimal("100000")
    strategy: Strategy = field(default_factory=Strategy)
    auto_add_assets: bool = True
    symbol_map: dict[str, str] = field(default_factory=dict)
    journal: Path = Path("journal.jsonl")


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(f"{path} not found; copy bridge.example.yaml to {path.name}")
    base = path.resolve().parent
    _load_dotenv(base / ".env")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def rel(value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else base / p

    email, password = os.environ.get("PTP_EMAIL", ""), os.environ.get("PTP_PASSWORD", "")
    if not email or not password:
        raise ConfigError("set PTP_EMAIL and PTP_PASSWORD (environment or .env)")

    portfolio = raw.get("portfolio") or {}
    strat = raw.get("strategy") or {}
    defaults = Strategy()
    strategy = Strategy(
        position_size_pct=Decimal(str(strat.get("position_size_pct", defaults.position_size_pct))),
        max_positions=int(strat.get("max_positions", defaults.max_positions)),
        min_confidence=float(strat.get("min_confidence", defaults.min_confidence)),
        max_signal_age_days=int(strat.get("max_signal_age_days", defaults.max_signal_age_days)),
        asset_classes=list(strat.get("asset_classes", defaults.asset_classes)),
    )
    if not 0 < strategy.position_size_pct <= 100:
        raise ConfigError("strategy.position_size_pct must be between 0 and 100")

    cfg = Config(
        src_db=rel(os.environ.get("SRC_DB") or raw.get("src_db", "")),
        ptp_url=(os.environ.get("PTP_URL") or raw.get("ptp_url", "http://127.0.0.1:8000")).rstrip("/"),
        email=email,
        password=password,
        portfolio_name=portfolio.get("name", "SRC signals"),
        base_currency=portfolio.get("base_currency", "USD"),
        starting_cash=Decimal(str(portfolio.get("starting_cash", "100000"))),
        strategy=strategy,
        auto_add_assets=bool(raw.get("auto_add_assets", True)),
        symbol_map=dict(raw.get("symbol_map") or {}),
        journal=rel(raw.get("journal", "journal.jsonl")),
    )
    if cfg.base_currency not in ("EUR", "USD"):
        raise ConfigError("portfolio.base_currency must be EUR or USD")
    return cfg
