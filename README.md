# Signal Bridge

Paper-trades the BUY / SELL signals of the [Sentiment Research Center](../sentiment-research-center) (SRC) on the [Paper Trading Platform](../paper-trading-platform) (PTP). No real orders are ever placed: the bridge only talks to the platform's REST API.

```
SRC pipeline ──► data/src_dashboard.duckdb ──► bridge ──► PTP REST API (/api/...) ──► portfolio "SRC signals"
                 (read-only snapshot)
```

The bridge reads the snapshot SRC publishes after each run, never the live `src.duckdb`, which is locked while the pipeline writes. It imports neither codebase.

## Rules

The bridge trades one dedicated portfolio, long only, with market orders:

| Latest SRC signal | Held? | Action |
|---|---|---|
| BUY | no | Buy a position worth `position_size_pct` (5 %) of the portfolio value, fees included |
| BUY | yes | nothing (no pyramiding) |
| SELL | yes | Sell the whole position |
| SELL | no | nothing (no shorting) |
| HOLD, NO SIGNAL | — | nothing |

Some limits apply:
- Only the latest signal per asset counts, and only if it is at most `max_signal_age_days` old.
- When BUYs exceed the free slots (`max_positions`), the highest composite score wins. Assets sold in the same run free their slots first.
- A buy never spends more than the available cash.
- An order that cannot fill now (market closed, no fresh quote) is **deferred**: the next run tries again. SRC's stock run ends near midnight, so its stock BUYs fill after the next US open.
- Each order's client order id comes from its signal (`src-buy-stock:NVDA-20261001`). The platform deduplicates on that id, so a signal is traded at most once however often the bridge runs. A rejected order is retried with `-2`, `-3`, …
- SRC assets missing from the platform's catalog (e.g. `stock:MU`) are found through the platform's instrument search and added (`auto_add_assets`). `symbol_map` fixes wrong matches. SRC's `BRK.B` becomes Yahoo's `BRK-B` automatically.

Keep manual trades out of the bridge's portfolio. The bridge treats every position in it as its own, and SELL signals close them.

## Set up

```powershell
uv sync
copy bridge.example.yaml bridge.yaml   # paths, portfolio, strategy
copy .env.example .env                 # PTP_EMAIL / PTP_PASSWORD of a platform account
```

Create the account in the platform UI first, or register it with `POST /api/auth/register`. The first real run creates the portfolio (`portfolio.name`, `base_currency`, `starting_cash`). `SRC_DB` and `PTP_URL` in the environment override the config file.

## Use

```powershell
uv run bridge run --dry-run   # show what it would do: sizes via order previews, creates/adds/places nothing
uv run bridge run             # trade
uv run bridge status          # portfolio value, each position's latest SRC signal, and signals not traded yet
uv run pytest
```

`run` prints one line per BUY/SELL signal, with its status:
- `filled`: the order went through.
- `deferred`: the order will be tried again on the next run.
- `skipped`: the reason is shown.
- `done`: this signal was already traded.
- `rejected` or `error`: the order failed.

Placed orders and errors are appended to `journal.jsonl`. The exit code is 1 if any order failed with an error. The platform's own trade history (`/api/portfolios/{id}/trades.csv`) remains the record of truth.

## Run it on the server

Both apps run in Proxmox container 108. There the bridge runs every 30 minutes as `signal-bridge.timer`, as the `srcenter` user so it can read SRC's snapshot. Inside the container:

```sh
install -d /opt/signal-bridge /etc/signal-bridge
install -d -o srcenter -g srcenter /var/lib/signal-bridge
# copy this repo to /opt/signal-bridge/src, then:
uv venv /opt/signal-bridge/venv && uv pip install --python /opt/signal-bridge/venv /opt/signal-bridge/src
cp /opt/signal-bridge/src/bridge.example.yaml /etc/signal-bridge/bridge.yaml   # then edit, see below
printf 'PTP_EMAIL=...\nPTP_PASSWORD=...\n' > /etc/signal-bridge/.env
chown root:srcenter /etc/signal-bridge/.env && chmod 640 /etc/signal-bridge/.env
cp /opt/signal-bridge/src/deploy/systemd/signal-bridge.* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now signal-bridge.timer
```

In `/etc/signal-bridge/bridge.yaml`:
- `src_db: /var/lib/sentiment-research-center/data/src_dashboard.duckdb`
- `ptp_url: http://127.0.0.1:8000`
- `journal: /var/lib/signal-bridge/journal.jsonl`

Check it with `systemctl list-timers signal-bridge.timer` and `journalctl -u signal-bridge -n 50`.
