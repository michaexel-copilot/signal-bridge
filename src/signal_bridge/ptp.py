"""Thin client for the Paper Trading Platform REST API (cookie session)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx


class PTPError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.detail = status, code, message, detail or {}


def _json_safe(body: dict) -> dict:
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in body.items()}


class Client:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base_url, timeout=60, transport=transport)

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, **kwargs) -> Any:
        if "json" in kwargs:
            kwargs["json"] = _json_safe(kwargs["json"])
        response = self.http.request(method, path, **kwargs)
        if response.is_success:
            return response.json() if response.content else None
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict):
            raise PTPError(response.status_code, detail.get("code", "error"),
                           detail.get("message", ""), detail)
        raise PTPError(response.status_code, "http_error", str(detail or response.text)[:300])

    # --- account and portfolio ---------------------------------------------------------------

    def login(self, email: str, password: str) -> None:
        self._call("POST", "/api/auth/login", json={"email": email, "password": password})

    def portfolios(self) -> list[dict]:
        return self._call("GET", "/api/portfolios")

    def create_portfolio(self, name: str, base_currency: str, starting_cash: Decimal) -> dict:
        return self._call("POST", "/api/portfolios", json={
            "name": name, "base_currency": base_currency, "starting_cash": starting_cash})

    def portfolio(self, portfolio_id: int) -> dict:
        return self._call("GET", f"/api/portfolios/{portfolio_id}")

    def orders(self, portfolio_id: int) -> list[dict]:
        return self._call("GET", f"/api/portfolios/{portfolio_id}/orders")

    # --- catalog and prices ------------------------------------------------------------------

    def assets(self) -> list[dict]:
        return self._call("GET", "/api/assets")

    def search(self, query: str) -> list[dict]:
        return self._call("GET", "/api/assets/search", params={"q": query})

    def add_asset(self, source: str, symbol: str) -> dict:
        return self._call("POST", "/api/assets", json={"source": source, "symbol": symbol})

    def quote(self, asset_id: int) -> dict:
        return self._call("GET", f"/api/market/quote/{asset_id}")

    # --- orders ------------------------------------------------------------------------------

    def preview(self, portfolio_id: int, asset_id: int, side: str, quantity: Decimal) -> dict:
        return self._call("POST", f"/api/portfolios/{portfolio_id}/orders/preview", json={
            "asset_id": asset_id, "side": side, "type": "market", "quantity": quantity})

    def market_order(self, portfolio_id: int, asset_id: int, side: str, quantity: Decimal,
                     client_order_id: str) -> dict:
        return self._call("POST", f"/api/portfolios/{portfolio_id}/orders", json={
            "asset_id": asset_id, "side": side, "type": "market", "quantity": quantity,
            "client_order_id": client_order_id})
