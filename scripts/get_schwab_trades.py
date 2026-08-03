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
from core.utils import current_datetime, get_datetime_as_str

"""
Fetches the account holder's Schwab trades over a date range and pretty-prints them.

    python -m scripts.get_schwab_trades [START] [END] [--positions-csv PATH]

START and END are dates (YYYY-MM-DD or YYYYMMDD). If START is omitted, today is used. If END is omitted, the
current datetime is used.

With --positions-csv, the fetched trades are also reconciled into a positions CSV (same format as
data/current_positions.csv):

    Position #,Date In,Position Type,Symbol,Quantity,Trade Price,Date Out,Quantity Out,Exit Price

For each trade (processed oldest-first), we match existing rows by Symbol only (dates no longer matter). If no
row matches the symbol, a new row is created automatically from the trade ("Date In", "Quantity", "Trade Price"
from the trade; a fresh "Position #"; blank "Position Type"; empty exit fields). If one or more rows match, the
tool prints the matching row(s) and the trade, then prompts the user to choose what to do:
  1. Add to position    -- "Quantity" += the trade's quantity; "Trade Price" becomes the quantity-weighted
                           average of the old trade price and the trade's price.
  2. Exit/partial exit   -- "Quantity Out" += the trade's quantity; "Exit Price" becomes the quantity-weighted
                           average of the old exit price and the trade's price; "Date Out" becomes the trade's
                           datetime.
  3. New leg            -- a new row is created from the trade (as in the no-match case).
  4. Do nothing         -- the trade is discarded.
(When more than one row matches the symbol, the user is first asked which row the choice applies to.)
Before the menu, advisory notices appear when the trade's quantity shares a sign with the row's "Quantity" (it
may already be counted as an entry) or with its "Quantity Out" (it may already be counted as an exit). They do
not change the available choices.
Datetimes we write use IB-style datetimes with a time, e.g. "20260513 09:30:00 US/Eastern". Rows are written
back ordered by "Position #".

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


def _create_new_row(trade: TradeDescriptor, rows: List[dict], next_num: List[int], trade_dt: str):
    """Appends a fresh position row built from the trade (a brand-new position or an explicit new leg)."""
    next_num[0] += 1
    rows.append(
        {
            COL_POSITION_NUM: str(next_num[0]),
            COL_DATE_IN: trade_dt,
            COL_POSITION_TYPE: "",
            COL_SYMBOL: trade.security_descriptor.symbol_full,
            COL_QUANTITY: trade.quantity,
            COL_TRADE_PRICE: round(trade.price, 2),
            COL_DATE_OUT: "",
            COL_QUANTITY_OUT: 0,
            COL_EXIT_PRICE: 0,
        }
    )


def _weighted_average(old_price: float, old_weight: int, new_price: float, new_weight: int) -> float:
    """Quantity-weighted average of two prices; falls back to the old price when both weights are zero."""
    total = old_weight + new_weight
    if total <= 0:
        return old_price
    return (old_price * old_weight + new_price * new_weight) / total


def _add_to_position(row: dict, trade: TradeDescriptor):
    """Choice 1: grow the entry. Quantity accumulates; Trade Price becomes the quantity-weighted average."""
    old_qty = _to_int(row.get(COL_QUANTITY))
    old_price = _to_float(row.get(COL_TRADE_PRICE))
    new_price = _weighted_average(old_price, abs(old_qty), trade.price, abs(trade.quantity))
    row[COL_QUANTITY] = old_qty + trade.quantity
    row[COL_TRADE_PRICE] = round(new_price, 2)


def _exit_position(row: dict, trade: TradeDescriptor, trade_dt: str):
    """Choice 2: record an exit. Quantity Out accumulates; Exit Price is quantity-weighted; Date Out is set."""
    old_qty_out = _to_int(row.get(COL_QUANTITY_OUT))
    old_exit_price = _to_float(row.get(COL_EXIT_PRICE))
    new_exit_price = _weighted_average(old_exit_price, abs(old_qty_out), trade.price, abs(trade.quantity))
    row[COL_QUANTITY_OUT] = old_qty_out + trade.quantity
    row[COL_EXIT_PRICE] = round(new_exit_price, 2)
    row[COL_DATE_OUT] = trade_dt


def _print_row(row: dict):
    """Prints every field of a position row, for the user's reference before the menu."""
    for col in CSV_COLUMNS:
        print(f"    {col}: {row.get(col, '')}")


def _print_trade(trade: TradeDescriptor, trade_dt: str):
    """Prints the trade's fields, for the user's reference before the menu."""
    print(f"    Symbol: {trade.security_descriptor.symbol_full}")
    print(f"    Quantity: {signed_quantity_str(trade)}")
    print(f"    Trade Price: {trade.price:.2f}")
    print(f"    Datetime: {trade_dt}")


def _same_sign(first: int, second: int) -> bool:
    """True when both values are non-zero and point the same way (zero has no sign, so it never matches)."""
    return first > 0 and second > 0 or first < 0 and second < 0


def _print_notices(row: dict, trade: TradeDescriptor):
    """
    Warns when the trade looks like it may already be reflected in the row, so the user doesn't double-count it.

    A trade whose quantity has the same sign as the row's "Quantity" moves the entry in the direction the entry
    was already built, which is what a re-imported entry fill would look like; likewise for "Quantity Out" and
    exits. Both notices can fire at once. Neither blocks the menu -- they are advisory, and the user still picks
    the action.
    """
    trade_qty = trade.quantity
    if _same_sign(trade_qty, _to_int(row.get(COL_QUANTITY))):
        print("    *** NOTICE: this trade might have already been accounted for as a position entry. ***")
    # A zero "Quantity Out" means nothing has been exited yet, so there is nothing to have double-counted.
    if _same_sign(trade_qty, _to_int(row.get(COL_QUANTITY_OUT))):
        print("    *** NOTICE: this trade might have already been accounted for as a position exit. ***")


def _prompt(message: str, valid: set) -> str:
    """Prompts until the user enters one of the valid responses (matched case-insensitively, trimmed)."""
    while True:
        response = input(message).strip().lower()
        if response in valid:
            return response
        print(f"    Please enter one of: {', '.join(sorted(valid))}")


def _select_target_row(matches: List[dict]) -> Optional[dict]:
    """
    When more than one row matches the symbol, asks which one the choice applies to. Returns the chosen row, or
    None if the user opts to skip (do nothing) for this trade.
    """
    if len(matches) == 1:
        return matches[0]
    print(f"    {len(matches)} existing rows match this symbol:")
    for i, row in enumerate(matches, start=1):
        print(
            f"      [{i}] Position #{row.get(COL_POSITION_NUM, '')}, "
            f"Qty {row.get(COL_QUANTITY, '')}, In {row.get(COL_DATE_IN, '')}, "
            f"Out {row.get(COL_DATE_OUT, '') or '-'}"
        )
    valid = {str(i) for i in range(1, len(matches) + 1)} | {"s"}
    choice = _prompt(f"    Which row does this trade apply to? [1-{len(matches)}, or s to skip]: ", valid)
    if choice == "s":
        return None
    return matches[int(choice) - 1]


def apply_trade_to_rows(trade: TradeDescriptor, rows: List[dict], next_num: List[int]):
    """
    Reconciles a single trade into the positions rows following the Step 7 rules. If no row matches the trade's
    symbol, a new row is created automatically. If one or more do, the user is shown the row(s) and the trade
    and prompted to add to the position, exit/partially exit it, add a new leg, or discard the trade. `next_num`
    is a one-element list holding the next Position # to hand out (so it survives across calls).
    """
    symbol = trade.security_descriptor.symbol_full
    trade_dt = get_datetime_as_str(trade.trade_date)
    matches = [row for row in rows if (row.get(COL_SYMBOL) or "").strip() == symbol]

    # No existing row for this symbol -> create one automatically.
    if not matches:
        _create_new_row(trade, rows, next_num, trade_dt)
        print(f"  New position added for {symbol} (Position #{next_num[0]}).")
        return

    print(f"\n  Trade matches an existing position by symbol ({symbol}):")
    print("  Trade:")
    _print_trade(trade, trade_dt)

    target = _select_target_row(matches)
    if target is None:
        print("  Skipped.")
        return

    print("  Matching position row:")
    _print_row(target)
    _print_notices(target, trade)
    print("  Choose an action:")
    print("    1. Add to position (increase Quantity, average Trade Price)")
    print("    2. Exit / partially exit (increase Quantity Out, average Exit Price, set Date Out)")
    print("    3. New leg (create a new position row from this trade)")
    print("    4. Do nothing (discard this trade)")
    choice = _prompt("    Selection [1-4]: ", {"1", "2", "3", "4"})

    if choice == "1":
        _add_to_position(target, trade)
        print(f"  Added to Position #{target.get(COL_POSITION_NUM, '')}.")
    elif choice == "2":
        _exit_position(target, trade, trade_dt)
        print(f"  Recorded exit against Position #{target.get(COL_POSITION_NUM, '')}.")
    elif choice == "3":
        _create_new_row(trade, rows, next_num, trade_dt)
        print(f"  New leg added for {symbol} (Position #{next_num[0]}).")
    else:
        print("  Discarded.")


def reconcile_trades_into_csv(trades: List[TradeDescriptor], csv_path: str):
    """
    Applies the trades (oldest-first) to the positions CSV per the Step 7 rules and writes it back, ordered by
    Position #. Symbol-matching trades prompt the user interactively for how to reconcile them.
    """
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
