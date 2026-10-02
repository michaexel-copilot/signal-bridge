"""Map SRC asset keys to Paper Trading Platform assets."""

from __future__ import annotations

from .ptp import Client, PTPError
from .signals import Signal

# SRC class -> platform classes it may appear under.
CLASSES = {"crypto": ("crypto",), "stock": ("stocks", "etfs")}


def platform_symbol(signal: Signal, symbol_map: dict[str, str]) -> str:
    # Yahoo, the platform's stock source, writes share classes with a dash (BRK-B).
    return symbol_map.get(signal.asset_key) or signal.symbol.upper().replace(".", "-")


def _base(symbol: str) -> str:
    return symbol.upper().split("/")[0].split(":")[0]


class Catalog:
    def __init__(self, client: Client, symbol_map: dict[str, str], auto_add: bool, dry_run: bool):
        self.client, self.symbol_map = client, symbol_map
        self.auto_add, self.dry_run = auto_add, dry_run
        self._refresh()

    def _refresh(self) -> None:
        self.by_id = {a["id"]: a for a in self.client.assets()}
        self.by_symbol = {(a["asset_class"], a["symbol"].upper()): a for a in self.by_id.values()}

    def find(self, signal: Signal) -> dict | None:
        symbol = platform_symbol(signal, self.symbol_map)
        for cls in CLASSES.get(signal.asset_class, ()):
            if (cls, symbol) in self.by_symbol:
                return self.by_symbol[(cls, symbol)]
        return None

    def find_or_add(self, signal: Signal) -> dict | str:
        """The asset, or the reason it cannot be traded."""
        asset = self.find(signal)
        if asset is not None:
            return asset if asset["available"] else "no price source lists it"
        if not self.auto_add:
            return "not in the platform catalog (auto_add_assets is off)"
        symbol = platform_symbol(signal, self.symbol_map)
        classes = CLASSES.get(signal.asset_class, ())
        try:
            hits = self.client.search(symbol)
        except PTPError as exc:
            return f"catalog search failed: {exc.message}"
        for hit in hits:
            if not hit["supported"] or hit["asset_class"] not in classes or _base(hit["symbol"]) != symbol:
                continue
            if hit["asset_id"] is not None:
                self._refresh()
                return self.by_id.get(hit["asset_id"]) or "search hit is not in the catalog"
            if self.dry_run:
                return f"not in the catalog yet; a real run adds it from {hit['source']}"
            try:
                asset = self.client.add_asset(hit["source"], hit["symbol"])
            except PTPError as exc:
                return f"could not add it from {hit['source']}: {exc.message}"
            self._refresh()
            return asset
        return f"no {'/'.join(classes)} instrument {symbol} found by the platform's search"
