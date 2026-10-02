"""bridge run [--dry-run] | bridge status"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from . import signals as src
from .catalog import Catalog
from .config import ConfigError, load
from .ptp import Client, PTPError
from .runner import Bridge


def _table(rows: list[list[str]], header: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    for row in [header, *rows]:
        print("  ".join(str(x).ljust(w) for x, w in zip(row, widths)).rstrip())


def cmd_run(cfg, client: Client, dry_run: bool) -> int:
    outcomes = Bridge(cfg, client, dry_run=dry_run).run()
    if not outcomes:
        print("No BUY or SELL signal calls for a trade.")
        return 0
    _table([[o.asset_key, o.signal, o.signal_date, o.action, o.status, o.quantity or "",
             o.value or "", o.detail] for o in outcomes],
           ["asset", "signal", "date", "action", "status", "qty", "cost", "detail"])
    return 1 if any(o.status == "error" for o in outcomes) else 0


def cmd_status(cfg, client: Client) -> int:
    client.login(cfg.email, cfg.password)
    match = [p for p in client.portfolios() if p["name"] == cfg.portfolio_name]
    if not match:
        print(f'No portfolio named "{cfg.portfolio_name}" yet; the first `bridge run` creates it.')
        return 0
    d = client.portfolio(match[0]["id"])
    cur = d["base_currency"]
    print(f'{d["name"]} (id {d["id"]}): value {Decimal(d["total_value"]):,.2f} {cur}, '
          f'return {Decimal(d["total_return_pct"]):.2f} %, cash {Decimal(d["cash"]):,.2f}, '
          f'fees {Decimal(d["fees_paid"]):,.2f}')
    latest = {s.asset_key: s for s in src.latest(cfg.src_db)}
    catalog = Catalog(client, cfg.symbol_map, auto_add=False, dry_run=True)
    by_asset = {catalog.find(s)["id"]: s for s in latest.values() if catalog.find(s)}
    rows = []
    for p in d["positions"]:
        s = by_asset.get(p["asset"]["id"])
        rows.append([p["asset"]["symbol"], f'{Decimal(p["quantity"]).normalize():f}',
                     f'{Decimal(p["market_value"]):,.2f}', f'{Decimal(p["unrealized_pct"] or 0):.2f} %',
                     f"{s.signal} ({s.date})" if s else "-"])
    if rows:
        print()
        _table(rows, ["asset", "qty", "value", "unrealized", "latest SRC signal"])

    # What the next run would do with signals not traded yet (deferred orders, skips).
    waiting = [o for o in Bridge(cfg, client, dry_run=True).run() if o.status != "done"]
    if waiting:
        print("\nNot traded yet")
        _table([[o.asset_key, o.signal, o.signal_date, o.action,
                 "next run" if o.status == "planned" else o.status, o.detail] for o in waiting],
               ["asset", "signal", "date", "action", "status", "detail"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bridge", description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=Path("bridge.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="trade the latest SRC signals")
    run.add_argument("--dry-run", action="store_true",
                     help="plan and preview only; place no orders, create nothing")
    sub.add_parser("status", help="show the bridge portfolio next to the latest signals")
    args = parser.parse_args(argv)

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2
    client = Client(cfg.ptp_url)
    try:
        if args.command == "run":
            return cmd_run(cfg, client, args.dry_run)
        return cmd_status(cfg, client)
    except PTPError as exc:
        print(f"platform: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
