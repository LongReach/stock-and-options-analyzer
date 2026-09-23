import asyncio
import textwrap
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from logging import basicConfig, INFO, getLogger
from typing import Tuple, Dict, Optional, List
from enum import IntEnum

import pandas
from datetime import timedelta
import traceback

from core.common import RequestedInfoType, ScrapeLevel
from core.base_driver import BaseDriver
from core.earnings_manager import EarningsManager
from core.ib.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager
from core.stock_data import StockDataException
from core.utils import get_datetime, get_datetime_as_str, current_datetime, calculate_expected_move, bar_size_to_time
from core.option_data_manager import OptionDataManager

r"""
Utility for finding stocks with high or low IV rank.

Setup and usage:
------------------------
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.iv_finder --help    # full instruction manual with examples
"""

CLIENT_ID = 20
DB_PATH = "data/iv_data.h5"
NUM_ACCEPTABLE_BARS = 200
ACCEPTABLE_RECENCY = 2


def calculate_iv_rank(df: pandas.DataFrame) -> Tuple[float, float]:
    """Given a dataframe holding IV data, return IV rank and latest IV"""
    lowest_iv = 1000000.0
    highest_iv = 0.0
    total_bars = len(df)
    start_bar = total_bars - 200
    start_bar = 0 if start_bar < 0 else start_bar
    for idx in range(start_bar, total_bars):
        iv = df.iloc[idx]["close"]
        lowest_iv = min(iv, lowest_iv)
        highest_iv = max(iv, highest_iv)
    latest_iv = df.iloc[-1]["close"]
    rank = (latest_iv - lowest_iv) / (highest_iv - lowest_iv)
    rank *= 100.0
    return rank, latest_iv


async def find_stocks(
    stock_manager: StockDataManager,
    earnings_manager: EarningsManager,
    iv_rank: float,
    above: bool,
    earnings_window: int,
    earnings_after: bool,
    no_scrape: bool,
):
    """
    Find high or low IV stocks and print a list sorted by IV rank.

    :param stock_manager: StockDataManager instance, must be connected to broker
    :param earnings_manager: EarningsManager instance
    :param iv_rank: desired IV rank to be above or below
    :param above: if True, want stocks with IV rank above iv_rank. Otherwise, the opposite.
    :param earnings_window: in days from now
    :param earnings_after: if True, earning should fall AFTER earnings window. If False, BEFORE.
    :param no_scrape: if True, used cached data only
    """
    stock_manager.set_log_to_stdout(False)

    found_map: Dict[str, Tuple[float, float]] = {}

    keys = stock_manager.get_cached_keys()
    for key in keys:
        symbol, bar_size, info_type = stock_manager.get_key_elements(key)
        if bar_size != BarSize.ONE_DAY or info_type != RequestedInfoType.IMPLIED_VOLATILITY:
            continue

        print(f"Examining {key}...")
        rank, latest_iv, error_msg = await stock_manager.get_iv_rank(symbol, cache_only=no_scrape, acceptable_recency=2)
        if error_msg is not None:
            print(f"Error finding IV rank with {key}, is: {error_msg}")
            continue

        days_to_earnings = earnings_manager.get_next_date(symbol, in_days=True)

        # Filter out any that fail to meet criteria about earnings and IV rank
        # If not earnings date can be found (e.g. ETFs don't have earnings), don't consider that criteria

        if above and rank < iv_rank:
            continue
        if not above and rank > iv_rank:
            continue
        if days_to_earnings is not None:
            if earnings_after and days_to_earnings < earnings_window:
                continue
            if not earnings_after and days_to_earnings > earnings_window:
                continue

        found_map[key] = (rank, latest_iv)

    # Sort by IV rank
    found_map = dict(sorted(found_map.items(), key=lambda item: item[1][0], reverse=above))

    stock_list: List[str] = []

    print(f"\nStocks with IV rank {'above' if above else 'below'} {iv_rank}:")
    print("----------------------------------------")
    for key, info in found_map.items():
        symbol, bar_size, info_type = stock_manager.get_key_elements(key)
        stock_list.append(symbol)
        rank, iv = info
        days_to_earnings = earnings_manager.get_next_date(symbol, in_days=True)
        earnings_str = ""
        if days_to_earnings is not None:
            earnings_str = f", earnings are in {days_to_earnings} days"
        print(f"{symbol}: rank is {rank:.2f}, IV is {iv:.2f}{earnings_str}")

    print(f"\nSymbol list is: {",".join(stock_list)}")


async def get_single_stock_data(
    stock_manager: StockDataManager,
    options_manager: OptionDataManager,
    symbol: str,
    dte: float,
    date: Optional[str],
    no_scrape: bool,
    delta: Optional[float],
    strike: Optional[float],
    move: bool = False,
):
    """
    Print data of potential options trades for a particular security.

    :param stock_manager: StockManager instance. Connection to brokerage must be established.
    :param options_manager: OptionsDataManager instance. Same about connection.
    :param symbol: ticker symbol, e.g. SPY
    :param dte: days to expiration
    :param date: specific expiration date. If given, supplants dte.
    :param no_scrape: if given, no data will be pulled from online, only cache.
    :param delta: if given, desired delta. Otherwise, get wide chain of options.
    :param strike: if given, desired strike. If not given, strikes will be chosen by delta.
    :param move: if True, print the expected move (IV and ATM straddle methods) instead of option chains.
    :return:
    """
    stock_manager.set_log_to_stdout(False)

    if date:
        date_dt = get_datetime(date)
        dte = (date_dt - current_datetime()).days

    min_delta = 0.05
    max_delta = 0.5
    if delta is not None:
        min_delta = delta - 0.05
        max_delta = delta + 0.05

    rank, latest_iv, error_msg = await stock_manager.get_iv_rank(symbol, cache_only=no_scrape, acceptable_recency=2)
    if error_msg is not None:
        print(f"Error finding IV rank for {symbol}, is: {error_msg}")
        return

    price, error_msg = await stock_manager.get_most_recent_price(symbol)
    if error_msg is not None:
        print(f"Error getting underlying price for symbol {symbol}")
        return

    print(f"\nInfo for {symbol}:\n----------------------------------------------")
    print(f"Current price: {price}")
    print(f"IV rank is {rank}, IV is {latest_iv}")
    expirations = await options_manager.get_expirations(symbol, 0, 70)
    print(f"Nearby expirations are: {expirations}")
    if len(expirations) == 0:
        print(f"Could not find options contracts with appropriate expirations for {symbol}")
        return

    best_expiration_dt, best_dte, error_msg = await options_manager.get_best_expiration(symbol, dte)
    if error_msg:
        print(f"Error getting best expiration: {error_msg}")
        return

    best_expiration_date_str = get_datetime_as_str(best_expiration_dt)
    expected_move = calculate_expected_move(price, latest_iv, best_dte)
    print(
        f"Expected move for {best_dte} DTE option expiring on {best_expiration_date_str} (~{dte} days) is {expected_move:.2f}, between {price - expected_move:.2f} and {price + expected_move:.2f}"
    )

    if move:
        print("Please wait for ATM straddle move...")
        atm_straddle_move, best_strike, error_msg = await options_manager.get_atm_straddle_move(
            symbol, best_expiration_date_str
        )
        if error_msg:
            print(f"Error getting ATM straddle: {error_msg}")
            return
        # get_atm_straddle_move returns the raw straddle (~0.8 SD); scale by 1.25 for a one-SD estimate.
        one_sd_move = atm_straddle_move * 1.25
        print(f"ATM straddle move, one standard deviation: {one_sd_move}, using strikes at {best_strike}")
        print(f"ATM straddle move, two standard deviations: {one_sd_move * 2.0}")

    # Get both put and call chains, then print them
    sides = [("P", "put"), ("C", "call")]
    for side in sides:
        right, contract_type = side
        if strike is None:
            if right == "P":
                min_strike = price - expected_move * 2.0
                max_strike = price
            else:
                min_strike = price
                max_strike = price + expected_move * 2.0
            the_strikes, idx = await options_manager.get_strikes(
                symbol, best_expiration_date_str, right, min_strike=min_strike, max_strike=max_strike
            )
        else:
            the_strikes = [strike]
        print(f"The {contract_type} strikes are:", the_strikes)
        print(f"Please wait for {contract_type} option chain...")
        option_data = await options_manager.get_option_chain(
            symbol, best_expiration_date_str, right, strike=the_strikes, min_delta=min_delta, max_delta=max_delta
        )
        df = option_data.get_dataframe(drop_columns=["date", "expiration"])
        print(f"The {contract_type} chain is:\n{df}")


async def main(parser: ArgumentParser):
    """Top-level function, unpacks arguments and calls functions that do the work"""
    args = parser.parse_args()

    logger = getLogger(__name__)
    basicConfig(filename="iv_finder.log", level=INFO)
    stock_manager = StockDataManager()
    stock_manager.set_db_path(DB_PATH)
    data_driver: BaseDriver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
    if not args.cache_only:
        success = stock_manager.add_driver(data_driver)
        if not success:
            print("Error connecting to broker data")
            return
    stock_manager.set_log_to_stdout(True)

    options_manager = OptionDataManager()
    options_manager.add_driver(data_driver)

    earnings_manager = EarningsManager()
    earnings_manager.set_tickers_from_file("data/optionable.txt")

    try:
        if args.above >= 0 or args.below >= 0 or args.earnings_before >= 0 or args.earnings_after >= 0:
            iv_rank_should_be_above = True
            iv_rank = 0.0
            if args.above >= 0:
                iv_rank = args.above
                iv_rank_should_be_above = True
            elif args.below >= 0:
                iv_rank = args.below
                iv_rank_should_be_above = False

            earnings_window = 0
            after = True
            if args.earnings_before >= 0:
                earnings_window = args.earnings_before
                after = False
            elif args.earnings_after >= 0:
                earnings_window = args.earnings_after
                after = True

            await find_stocks(
                stock_manager,
                earnings_manager,
                iv_rank,
                above=iv_rank_should_be_above,
                earnings_window=earnings_window,
                earnings_after=after,
                no_scrape=args.cache_only,
            )
        elif args.symbol is not None:
            await get_single_stock_data(
                stock_manager,
                options_manager,
                args.symbol,
                args.dte,
                args.date,
                no_scrape=args.cache_only,
                delta=args.delta,
                strike=args.strike,
                move=args.move,
            )
        elif args.move:
            print("No symbol given.")
        else:
            print("No --above or --below argument given")
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())

    if data_driver:
        data_driver.disconnect()


parser = ArgumentParser(
    prog="python -m scripts.iv_finder",
    formatter_class=RawDescriptionHelpFormatter,
    description=textwrap.dedent("""\
        Find stocks with high or low IV rank, inspect a single symbol's option chain,
        or estimate a stock's expected move.

        It is strongly recommended, before using, to build a data cache with the
        cache_data tool (see the first example below). Requires IB Gateway or TWS
        running locally (defaults to the paper/sim account).
        """),
    epilog=textwrap.dedent("""\
        Examples:
          # First, build the IV data cache this tool reads from
          python -m scripts.cache_data --file .\\data\\optionable.txt --db .\\data\\iv_data.h5 --info-type iv --limited

          # List stocks with IV rank above 50%
          python -m scripts.iv_finder --above 50

          # List stocks with IV rank below 25% whose earnings are more than 30 days out
          python -m scripts.iv_finder --below 25 --earnings-after 30

          # Filter by earnings alone (ignore IV): stocks with earnings within 7 days
          python -m scripts.iv_finder --earnings-before 7

          # Inspect a single symbol's put/call chains near 45 DTE
          python -m scripts.iv_finder --symbol SPY --dte 45

          # Inspect a specific expiration and delta
          python -m scripts.iv_finder --symbol SPY --date 20250627 --delta 0.30

          # Show the expected move for a symbol
          python -m scripts.iv_finder --symbol SPY --dte 45 --move

        Notes:
          * --above / --below take an IV rank in percent (0-100); give one, not both.
          * --earnings-after / --earnings-before filter by days until earnings, and
            may be used on their own (without --above/--below) to filter by earnings only.
          * --date (YYYYMMDD) overrides --dte when both are given.
          * --cache-only uses cached data only and performs no scraping.
        """),
)
parser.add_argument(
    "--above",
    help="IV rank to be above, in percent",
    required=False,
    default=-1.0,
    type=float,
)
parser.add_argument(
    "--below",
    help="IV rank to be below, in percent",
    required=False,
    default=-1.0,
    type=float,
)
parser.add_argument(
    "--earnings-after",
    help="Only stocks with earnings further away than X days in the future will be shown",
    required=False,
    default=-1,
    type=int,
)
parser.add_argument(
    "--earnings-before",
    help="Only stocks with earnings nearer than X days in the future will be shown",
    required=False,
    default=-1,
    type=int,
)
parser.add_argument(
    "--symbol",
    help="Collect information for specified symbol",
    required=False,
    default=None,
    type=str,
)
parser.add_argument(
    "--dte",
    help="Options expiration to target",
    required=False,
    default=45,
    type=float,
)
parser.add_argument(
    "--date",
    help="Specific expiration date, in YYYYMMDD format",
    required=False,
    default=None,
    type=str,
)
parser.add_argument(
    "--delta",
    help="Desired delta",
    required=False,
    default=None,
    type=float,
)
parser.add_argument(
    "--strike",
    help="Strike of interest",
    required=False,
    default=None,
    type=float,
)
parser.add_argument("--cache-only", help="don't do any scraping, just show info", action="store_true")
parser.add_argument("--move", help="show expected move (symbol and dte/date must be given)", action="store_true")


asyncio.run(main(parser))
