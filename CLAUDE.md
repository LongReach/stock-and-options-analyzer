# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An async Python trading system for Interactive Brokers (IB) that provides market data collection, options analysis, and algorithmic day trading via the **GuidedMissile** app. Requires IB Gateway or Trader Workstation running locally.

## Environment Setup

Use Anaconda Powershell Prompt.

```bash
conda activate options_2025_1
```

Install IB Python API (from the ibapi package directory):
```bash
python setup.py install
```

Formatting with black:
```bash
black -l 120
```

## Running the Project

```bash
# Launch the day-trading REPL app
python -m scripts.missile_launcher

# Fetch/cache historical data
python -m scripts.cache_data

# Examples
python -m scripts.basic_example
python -m scripts.options_driver_example
```

## IB Connection Ports

| Environment | Gateway | TWS |
|---|---|---|
| Live | 4001 | 7496 |
| Paper/Sim | 4002 | 7497 |

Scripts default to `sim_account=True` (paper) and `gateway_connection=True`.

## Running Tests

```bash
# Tests are in tests/ directory
python -m pytest tests/
```

## Architecture

### Layered Design

```
GuidedMissile (REPL app)
    └── PositionManager (tracks positions, account cash)
            └── Position (state machine: NONE→CREATED→ENTERED→CLOSED)
                    └── IBDriver (async IB API wrapper)
                            └── IBWrapper (EWrapper callbacks)
                                    └── IB EClient (ibapi socket connection)
```

Data managers sit alongside the trading layer:
- `StockDataManager` — caches multiple `StockData` (pandas) objects, smart-scrapes to avoid redundant IB requests
- `OptionDataManager` — fetches options chains with parallel Greeks retrieval (up to 15 concurrent requests)

### Key Files

| File | Purpose |
|---|---|
| `core/ib_driver.py` | Main async API: `get_historical_data()`, `get_greeks()`, `place_order()`, etc. |
| `core/ib_wrapper.py` | IB EWrapper callback implementation; dispatches to IBDriver |
| `core/common.py` | All shared enums/dataclasses: `BarSize`, `SecurityDescriptor`, `OrderInfo`, `HistoricalData`, etc. |
| `core/stock_data_manager.py` | Scrapes/caches OHLCV bars; splits into 200-bar tranches (IB limit) |
| `core/option_data_manager.py` | Fetches options chains; filters by delta; batches concurrent Greeks requests |
| `guided_missile/guided_missile_app.py` | REPL command loop + async main loop (10ms polling) |
| `guided_missile/position_manager.py` | Manages multiple positions; `BAR_SIZE=2min`, `MAX_LOSS=$100` |
| `guided_missile/position.py` | Per-position state machine with entry/stop-loss/take-profit `OrderGroup` |

### Async Pattern

IBDriver uses a **request-ID mapping** pattern: each IB request gets a unique integer ID. The request object is stored in a dict (e.g., `_request_bardata_objects`), IB's callback populates it, and the async method polls `wait_for_condition()` until a completion flag is set.

### Data Storage

Market data cached as `.zip` pickle files in `data/`. `StockData` and `OptionData` wrap pandas DataFrames. `scrape_data_smart()` checks existing data coverage before issuing new IB requests.

### SecurityDescriptor Format

Options are identified by strings like `"SPY-C-20250627-600.0"` (ticker-right-expiration-strike). Parsed by `SecurityDescriptor` in `core/common.py`.

## GuidedMissile Commands

| Command | Effect |
|---|---|
| `al SPY [n]` | Activate long — waits for entry trigger based on last `n` bars |
| `as SPY [n]` | Activate short |
| `el SPY` | Enter long immediately |
| `es SPY` | Enter short immediately |
| `exit SPY` | Exit active position |
| `can SPY` | Cancel pending orders |
| `clear SPY` | Clear/close position |
| `adjust SPY` | Adjust position parameters |
| `positions` | List all positions |
| `info SPY` | Show position details |
