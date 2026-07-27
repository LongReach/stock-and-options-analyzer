import argparse
import asyncio
import csv
import os
from typing import List, Optional

from core.base_driver import BaseDriver
from core.common import PositionDescriptor
from core.schwab.schwab_driver import SchwabDriver
from core.utils import current_datetime, get_datetime_as_str

"""
Fetches all of the user's current Schwab positions and pretty-prints them.

With an optional CSV file path, also writes the positions to that file using the same column format as
data/current_positions.csv:

    Position #,Date In,Position Type,Symbol,Quantity,Trade Price,Date Out,Quantity Out,Exit Price

Notes on the CSV:
  * Schwab reports flat positions (individual legs), not the logical structures (Iron Condor, Double
    Calendar, ...) that a human groups them into. So "Position Type" can't be inferred here.
  * The CSV is only ever ADDED to: existing rows are never modified. A held symbol that isn't in the file
    yet is appended as a new row (a fresh "Position #", blank "Position Type", "Date In" of now, and empty
    exit fields: "Date Out" blank, "Quantity Out" 0, "Exit Price" 0). A held symbol that already has a row --
    a symbol conflict -- is left completely alone (nothing added, nothing changed). Existing rows for symbols
    no longer held are also left in place.
  * "Quantity" is signed: negative for short positions, matching current_positions.csv.
  * "Date In"/"Date Out" use IB-style datetimes, e.g. "20260513 09:30:00 US/Eastern", with a time for
    disambiguation.

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


def signed_quantity(position: PositionDescriptor) -> int:
    """Returns the position quantity, negative for a short position (as in options_trades_2026.csv)."""
    return -position.quantity if position.short_position else position.quantity


def print_positions(positions: List[PositionDescriptor]):
    """Pretty-prints the held positions as an aligned table."""
    if not positions:
        print("No open positions.\n")
        return

    header = f"{'Symbol':<28}{'Quantity':>10}{'Trade Price':>14}"
    print(header)
    print("-" * len(header))
    for position in positions:
        print(
            f"{position.security_descriptor.symbol_full:<28}"
            f"{signed_quantity(position):>10}"
            f"{position.price:>14.2f}"
        )
    print(f"\n{len(positions)} position(s).\n")


def load_existing_rows(csv_path: str) -> List[dict]:
    """
    Reads an existing positions CSV into a list of row dicts (restricted to the known columns), preserving
    each row's recorded values. Returns an empty list if the file doesn't exist.
    """
    rows: List[dict] = []
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({col: (row.get(col) or "") for col in CSV_COLUMNS})
    return rows


def _max_position_num(rows: List[dict]) -> int:
    """Highest integer Position # among the given rows (0 if none parse)."""
    highest = 0
    for row in rows:
        try:
            highest = max(highest, int((row.get(COL_POSITION_NUM) or "").strip()))
        except ValueError:
            pass
    return highest


def compute_new_rows(positions: List[PositionDescriptor], existing_rows: List[dict]) -> List[dict]:
    """
    Returns one new row for each currently-held symbol that isn't already present in existing_rows. A held
    symbol that conflicts with an existing row is skipped entirely (nothing added). Existing rows are only
    read here, never modified.
    """
    known_symbols = {(row.get(COL_SYMBOL) or "").strip() for row in existing_rows}
    next_position_num = _max_position_num(existing_rows) + 1
    now_str = get_datetime_as_str(current_datetime())

    new_rows = []
    for position in positions:
        symbol = position.security_descriptor.symbol_full
        if symbol in known_symbols:
            # Symbol conflict: leave the existing row untouched and add nothing.
            continue
        known_symbols.add(symbol)  # guard against the same symbol appearing twice in the broker's list
        new_rows.append(
            {
                COL_POSITION_NUM: str(next_position_num),
                COL_DATE_IN: now_str,
                COL_POSITION_TYPE: "",
                COL_SYMBOL: symbol,
                COL_QUANTITY: signed_quantity(position),
                # Schwab reports a full-precision average cost; option prices are quoted in cents, so round to
                # 2 decimals to match current_positions.csv.
                COL_TRADE_PRICE: round(position.price, 2),
                COL_DATE_OUT: "",
                COL_QUANTITY_OUT: 0,
                COL_EXIT_PRICE: 0,
            }
        )
        next_position_num += 1

    return new_rows


def _ends_with_newline(csv_path: str) -> bool:
    """True if the file is empty or its last byte is a newline (so an append starts on a fresh line)."""
    with open(csv_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            return True
        f.seek(-1, os.SEEK_END)
        return f.read(1) in (b"\n", b"\r")


def write_csv(positions: List[PositionDescriptor], csv_path: str):
    """
    Appends any newly-held, non-conflicting symbols to csv_path in current_positions.csv format. Existing
    rows are never rewritten -- the file is only ever appended to (with a header written first if it's new or
    empty), so existing content stays byte-for-byte identical.
    """
    existing_rows = load_existing_rows(csv_path)
    new_rows = compute_new_rows(positions, existing_rows)

    has_content = os.path.exists(csv_path) and os.path.getsize(csv_path) > 0
    # Guard against a final line with no trailing newline, which would otherwise fuse with our first new row.
    if has_content and not _ends_with_newline(csv_path):
        with open(csv_path, "a", newline="") as f:
            f.write("\n")

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not has_content:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"{csv_path}: {len(existing_rows)} existing row(s) untouched, {len(new_rows)} new row(s) appended.\n")


async def main(csv_path: Optional[str]):
    driver: BaseDriver = SchwabDriver.create()
    if not driver.connect():
        print("Failed to connect to Schwab. Check credentials in .env and the token file.")
        return

    try:
        positions_info, error_str = await driver.get_positions()
        if error_str:
            print(f"Error getting positions: {error_str}")
            return

        positions = positions_info.get_positions()
        print_positions(positions)
        if csv_path:
            write_csv(positions, csv_path)
    finally:
        driver.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and print current Schwab positions.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=None,
        help="optional path to write positions as CSV (current_positions.csv format)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.csv_path))
