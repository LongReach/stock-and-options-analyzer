import argparse
import asyncio
import csv
import os
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from core.base_driver import BaseDriver
from core.common import TradeDescriptor, MARKETS_TIMEZONE
from core.schwab.schwab_driver import SchwabDriver
from core.utils import current_datetime, get_datetime, get_datetime_as_str

"""
Fetches the account holder's Schwab trades over a date range and pretty-prints them.

    python -m scripts.get_schwab_trades [START] [END] [--positions-csv PATH]

START and END are dates (YYYY-MM-DD or YYYYMMDD). If START is omitted, today is used. If END is omitted, the
current datetime is used.

With --positions-csv, the fetched trades are also reconciled into a positions CSV (same format as
data/current_positions.csv):

    Position #,Date In,Position Type,Symbol,Quantity,Trade Price,Date Out,Quantity Out,Exit Price

For each trade (processed oldest-first):
  * If it matches an existing row by symbol AND Date In, it's the entry already recorded -> nothing changes.
  * Else if it matches by symbol and the trade is no more recent than that row's Date Out, the exit is already
    recorded -> nothing changes.
  * Else if it matches by symbol (Date Out is empty, or the trade is more recent than it), it's an exit:
    "Date Out" becomes the trade's datetime, the signed trade quantity is added into "Quantity Out" (so a
    partial exit accumulates across trades), and "Exit Price" becomes the quantity-weighted average exit price.
  * Else (no existing row) a new row is created from the trade ("Date In", "Quantity", "Trade Price" from the
    trade; a fresh "Position #"; blank "Position Type"; empty exit fields).
Comparing the trade against Date Out (rather than requiring an exact match) makes re-running the same trades
idempotent: an exit older than or equal to the recorded Date Out is left alone. Because exits are keyed by
datetime, two trades for the same symbol at the exact same datetime can't be told apart; the tool prints a
warning listing any such collisions so they can be reconciled by hand.
Date/time comparisons and the values we write use IB-style datetimes with a time, e.g.
"20260513 09:30:00 US/Eastern". Rows are written back ordered by "Position #".

Note: Schwab only serves transactions from the last 60 days, so START can't be much older than that.

Credentials come from .env (see core/schwab/schwab_driver.py).
"""

# CSV column names, matching data/current_positions.csv exactly.
COL_POSITION_NUM = "Position #"
COL_DATE_IN = "Date In"
COL_POSITION_TYPE = "Position Type"
COL_SYMBOL = "Symbol"
COL_QUANTITY = "Quantity"
COL_TRADE_PRICE = "Trade Price"
COL_DATE_OUT = "Date Out"
COL_QUANTITY_OUT = "Quantity Out"
COL_EXIT_PRICE = "Exit Price"
CSV_COLUMNS = [
    COL_POSITION_NUM,
    COL_DATE_IN,
    COL_POSITION_TYPE,
    COL_SYMBOL,
    COL_QUANTITY,
    COL_TRADE_PRICE,
    COL_DATE_OUT,
    COL_QUANTITY_OUT,
    COL_EXIT_PRICE,
]


def parse_date(text: str) -> datetime:
    """Parses a YYYY-MM-DD or YYYYMMDD date into a market-timezone datetime at midnight."""
    digits = text.replace("-", "")
    try:
        naive = datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{text}'; expected YYYY-MM-DD or YYYYMMDD.")
    return naive.replace(tzinfo=ZoneInfo(MARKETS_TIMEZONE))


def signed_quantity_str(trade: TradeDescriptor) -> str:
    """Returns the quantity with an explicit sign, so buys (+) and sells (-) read at a glance."""
    return f"{trade.quantity:+d}"


def print_trades(trades: List[TradeDescriptor], start_dt: datetime, end_dt: datetime):
    """Pretty-prints the trades as an aligned table, most recent first."""
    print(f"Trades from {start_dt:%Y-%m-%d %H:%M %Z} to {end_dt:%Y-%m-%d %H:%M %Z}\n")
    if not trades:
        print("No trades in this range.\n")
        return

    header = f"{'Trade Time':<20}{'Symbol':<28}{'Quantity':>10}{'Price':>12}"
    print(header)
    print("-" * len(header))
    # Most recent first.
    for trade in sorted(trades, key=lambda t: t.trade_date, reverse=True):
        print(
            f"{trade.trade_date:%Y-%m-%d %H:%M}    "
            f"{trade.security_descriptor.symbol_full:<28}"
            f"{signed_quantity_str(trade):>10}"
            f"{trade.price:>12.2f}"
        )
    print(f"\n{len(trades)} trade(s).\n")


def _norm_dt(ib_date: str) -> str:
    """Normalizes an IB-style datetime string for comparison; a blank stays blank."""
    text = (ib_date or "").strip()
    if not text:
        return ""
    try:
        return get_datetime_as_str(text)
    except Exception:
        return text


def _to_int(value) -> int:
    """Parses a CSV cell to int, treating blank/garbage as 0."""
    try:
        return int(str(value).strip())
    except (ValueError, AttributeError):
        return 0


def _to_float(value) -> float:
    """Parses a CSV cell to float, treating blank/garbage as 0.0."""
    try:
        return float(str(value).strip())
    except (ValueError, AttributeError):
        return 0.0


def load_position_rows(csv_path: str) -> List[dict]:
    """Reads a positions CSV into a list of row dicts (empty if the file doesn't exist)."""
    rows: List[dict] = []
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(dict(row))
    return rows


def _max_position_num(rows: List[dict]) -> int:
    """Highest integer Position # among the rows (0 if none)."""
    highest = 0
    for row in rows:
        highest = max(highest, _to_int(row.get(COL_POSITION_NUM)))
    return highest


def _choose_exit_row(matches: List[dict]) -> dict:
    """
    Of the symbol-matching rows, picks the one to record an exit against, preferring a position that isn't
    fully closed yet (|Quantity Out| < |Quantity|), else the first match.
    """
    for row in matches:
        if abs(_to_int(row.get(COL_QUANTITY_OUT))) < abs(_to_int(row.get(COL_QUANTITY))):
            return row
    return matches[0]


def _record_exit(row: dict, trade: TradeDescriptor, trade_dt: str):
    """Records an exit trade against an existing row: accumulates Quantity Out, averages Exit Price, sets Date Out."""
    old_qty_out = _to_int(row.get(COL_QUANTITY_OUT))
    old_exit_price = _to_float(row.get(COL_EXIT_PRICE))
    # Quantity Out accumulates the signed trade quantity, so a partial exit increments/decrements it per the
    # trade's direction (a buy adds, a sell subtracts), matching how "Quantity" is signed for the entry.
    new_qty_out = old_qty_out + trade.quantity
    old_weight = abs(old_qty_out)
    new_weight = abs(trade.quantity)
    if old_weight + new_weight > 0:
        new_exit_price = (old_exit_price * old_weight + trade.price * new_weight) / (old_weight + new_weight)
    else:
        new_exit_price = old_exit_price
    row[COL_DATE_OUT] = trade_dt
    row[COL_QUANTITY_OUT] = new_qty_out
    row[COL_EXIT_PRICE] = round(new_exit_price, 2)


def apply_trade_to_rows(trade: TradeDescriptor, rows: List[dict], next_num: List[int]):
    """
    Reconciles a single trade into the positions rows following the Step 3 rules. `next_num` is a one-element
    list holding the next Position # to hand out (so it survives across calls).
    """
    symbol = trade.security_descriptor.symbol_full
    trade_dt = get_datetime_as_str(trade.trade_date)
    matches = [row for row in rows if (row.get(COL_SYMBOL) or "").strip() == symbol]

    # Brand-new position (no row for this symbol yet).
    if not matches:
        next_num[0] += 1
        rows.append(
            {
                COL_POSITION_NUM: str(next_num[0]),
                COL_DATE_IN: trade_dt,
                COL_POSITION_TYPE: "",
                COL_SYMBOL: symbol,
                COL_QUANTITY: trade.quantity,
                COL_TRADE_PRICE: round(trade.price, 2),
                COL_DATE_OUT: "",
                COL_QUANTITY_OUT: 0,
                COL_EXIT_PRICE: 0,
            }
        )
        return

    # Already recorded as this position's entry.
    if any(_norm_dt(row.get(COL_DATE_IN)) == trade_dt for row in matches):
        return

    target = _choose_exit_row(matches)
    date_out = (target.get(COL_DATE_OUT) or "").strip()
    # Exit already recorded that's at or after this trade: nothing to do. An empty Date Out never blocks
    # (any exit is "more recent" than no exit), so the exit gets recorded below.
    if date_out and trade.trade_date <= get_datetime(date_out):
        return

    # Record this exit (Date Out empty, or this trade is more recent than the recorded Date Out).
    _record_exit(target, trade, trade_dt)


def warn_on_duplicate_trade_datetimes(trades: List[TradeDescriptor]):
    """
    Prints a warning if two or more trades share the exact same symbol AND datetime. Reconciliation
    distinguishes exits by their datetime (compared against a row's Date Out), so same-symbol/same-datetime
    fills are indistinguishable and all but one would be silently dropped -- the user must reconcile those by
    hand. (Different symbols sharing a datetime is normal, e.g. the legs of one multi-leg order, so it's not
    flagged.)
    """
    groups: dict = {}
    for trade in trades:
        key = (trade.security_descriptor.symbol_full, get_datetime_as_str(trade.trade_date))
        groups.setdefault(key, []).append(trade)

    collisions = {key: group for key, group in groups.items() if len(group) > 1}
    if not collisions:
        return

    print("WARNING: multiple trades share the same symbol and datetime. Reconciliation can't tell them apart,")
    print("         so only one trade per group will be recorded. Reconcile these by hand:")
    for (symbol, dt_str), group in collisions.items():
        quantities = ", ".join(f"{trade.quantity:+d}" for trade in group)
        print(f"  * {symbol} @ {dt_str}: {len(group)} trades (quantities {quantities})")
    print()


def reconcile_trades_into_csv(trades: List[TradeDescriptor], csv_path: str):
    """
    Applies the trades (oldest-first) to the positions CSV per the Step 3/4 rules and writes it back, ordered
    by Position #.
    """
    warn_on_duplicate_trade_datetimes(trades)

    rows = load_position_rows(csv_path)
    next_num = [_max_position_num(rows)]

    for trade in sorted(trades, key=lambda t: t.trade_date):
        apply_trade_to_rows(trade, rows, next_num)

    # Order by Position # (numeric where possible), keeping insertion order within a group.
    def sort_key(item: tuple) -> tuple:
        order, row = item
        return _to_int(row.get(COL_POSITION_NUM)) or 10**9, order

    rows = [row for _, row in sorted(enumerate(rows), key=sort_key)]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Reconciled {len(trades)} trade(s) into {csv_path} ({len(rows)} row(s)).\n")


async def main(start_dt: datetime, end_dt: datetime, csv_path: Optional[str]):
    driver: BaseDriver = SchwabDriver.create()
    if not driver.connect():
        print("Failed to connect to Schwab. Check credentials in .env and the token file.")
        return

    try:
        trades_info, error_str = await driver.get_trades(start_dt, end_dt)
        if error_str:
            print(f"Error getting trades: {error_str}")
            return
        trades = trades_info.get_trades()
        print_trades(trades, start_dt, end_dt)
        if csv_path:
            reconcile_trades_into_csv(trades, csv_path)
    finally:
        driver.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and print Schwab trades over a date range.")
    parser.add_argument(
        "start_date",
        nargs="?",
        default=None,
        type=parse_date,
        help="start date (YYYY-MM-DD or YYYYMMDD); defaults to today",
    )
    parser.add_argument(
        "end_date",
        nargs="?",
        default=None,
        type=parse_date,
        help="end date (YYYY-MM-DD or YYYYMMDD); defaults to the current datetime",
    )
    parser.add_argument(
        "--positions-csv",
        "-p",
        default=None,
        help="optional path to a positions CSV (current_positions.csv format) to reconcile the trades into",
    )
    args = parser.parse_args()

    # Default start = midnight today (market time); default end = right now.
    now = current_datetime()
    start = args.start_date if args.start_date is not None else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = args.end_date if args.end_date is not None else now
    asyncio.run(main(start, end, args.positions_csv))
