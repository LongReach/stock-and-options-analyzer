import asyncio
import math
from argparse import ArgumentParser
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict, Optional
from enum import IntEnum

import pandas
from ibapi.common import BarData
from datetime import datetime, timedelta
import argparse
import traceback

from core.common import RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager
from core.stock_data import StockData, StockDataException
from core.utils import (
    str_to_bar_size,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
    calculate_expected_move,
    get_best_strike,
)
from core.options_data import OptionData
from core.option_data_manager import OptionDataManager

"""
Utility for finding stocks with high or low IV rank. It's strongly recommended, before using, to build a data
cache with the cache_data tool. 

Run like:
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.iv_finder --help
"""

CLIENT_ID = 20
DB_PATH = "data/iv_data.h5"
NUM_ACCEPTABLE_BARS = 50
ACCEPTABLE_RECENCY = 2


class ScrapeLevel(IntEnum):
    """Specifies level of scraping to do"""

    FULL = 0  # both old data and recent data
    RECENT = 1  # recent data only
    NONE = 2  # don't scrape any data


async def do_cache(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size: BarSize,
    info_type: RequestedInfoType,
    scrape_level: ScrapeLevel = ScrapeLevel.FULL,
) -> Tuple[Optional[pandas.DataFrame], Optional[str]]:
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type: type of chart data, e.g. trades or implied volatility
    :param scrape_level: indicates how much data should be scraped. It can be helpful to do no scraping when
        the broker is being unresponsive.
    :return:
    """
    if scrape_level in [ScrapeLevel.NONE, ScrapeLevel.RECENT]:
        start_date = ""
    else:
        start_date = get_datetime_as_str(current_datetime() - timedelta(days=365), date_only=True)

    df = None
    error_msg: Optional[str] = None
    try:
        # We'll be using cached data
        stock_manager.load_data(symbol, bar_size, info_type)

        if scrape_level != ScrapeLevel.NONE:
            success, error_str = await stock_manager.scrape_data_smart(
                symbol, bar_size, info_type, start_date=start_date, update_recent=True
            )
            if not success:
                error_msg = error_str
            stock_manager.save_data(symbol, bar_size, info_type)
        df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
        stock_manager.unload_data(symbol, bar_size, info_type)
    except KeyboardInterrupt:
        raise
    except StockDataException as ex:
        return None, f"StockDataException {ex}"
    except Exception as ex:
        print(f"Exception: {ex}")
        print(traceback.format_exc())
        error_msg = f"Exception: {error_msg}"

    return df, error_msg


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


async def find_stocks(stock_manager: StockDataManager, iv_rank: float, above: bool, no_scrape: bool):
    """
    Find high or low IV stocks and print a list sorted by IV rank.

    :param stock_manager: StockDataManager instance, must be connected to broker
    :param iv_rank: desired IV rank to be above or below
    :param above: if True, want stocks with IV rank above iv_rank. Otherwise, the opposite.
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

        if above and rank >= iv_rank:
            found_map[key] = (rank, latest_iv)
        if not above and rank <= iv_rank:
            found_map[key] = (rank, latest_iv)

    # Sort by IV rank
    found_map = dict(sorted(found_map.items(), key=lambda item: item[1][0], reverse=above))

    print(f"Stocks with IV rank {'above' if above else 'below'} {iv_rank}:")
    print("----------------------------------------")
    for key, info in found_map.items():
        symbol, bar_size, info_type = stock_manager.get_key_elements(key)
        rank, iv = info
        print(f"{symbol}: rank is {rank:.2f}, IV is {iv:.2f}")


async def get_single_stock_data(
    stock_manager: StockDataManager,
    options_manager: OptionDataManager,
    symbol: str,
    dte: float,
    date: Optional[str],
    no_scrape: bool,
    delta: Optional[float],
    strike: Optional[float],
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


async def find_move(stock_manager: StockDataManager, options_manager: OptionDataManager, symbol: str, dte: int):
    """
    Finds and prints expected move of stock, using both IV and ATM straddle methods.

    :param stock_manager: StockManager instance. Connection to brokerage must be established.
    :param options_manager: OptionsDataManager instance. Same about connection.
    :param symbol: ticker symbol, e.g. SPY
    :param dte: days to expiration
    """
    stock_manager.set_log_to_stdout(False)

    rank, latest_iv, error_msg = await stock_manager.get_iv_rank(symbol, cache_only=False, acceptable_recency=2)
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
    expirations = await options_manager.get_expirations(symbol, int(dte / 2), dte * 2)
    print(f"Nearby expirations are: {expirations}")
    if len(expirations) == 0:
        print(f"Could not find options contracts with appropriate expirations for {symbol}")
        return

    best_expiration_dt, best_dte, error_msg = await options_manager.get_best_expiration(symbol, dte)
    if error_msg:
        print(f"Error finding best expiration: {error_msg}")
        return

    best_expiration_date_str = get_datetime_as_str(best_expiration_dt)
    expected_move = calculate_expected_move(price, latest_iv, best_dte)
    print(
        f"Expected move for {best_dte} DTE option expiring on {best_expiration_date_str} (~{dte} days) is {expected_move:.2f}, between {price - expected_move:.2f} and {price + expected_move:.2f}"
    )

    atm_straddle_move, best_strike, error_msg = await options_manager.get_atm_straddle_move(
        symbol, best_expiration_date_str
    )
    if error_msg:
        print(f"Error getting ATM straddle: {error_msg}")
        return
    print(f"ATM straddle move, one standard deviation: {atm_straddle_move}, using strikes at {best_strike}")
    print(f"ATM straddle move, two standard deviations: {atm_straddle_move * 2.0}")


async def main(parser: ArgumentParser):
    """Top-level function, unpacks arguments and calls functions that do the work"""
    args = parser.parse_args()

    logger = getLogger(__name__)
    basicConfig(filename="iv_finder.log", level=INFO)
    stock_manager = StockDataManager()
    stock_manager.set_db_path(DB_PATH)
    ib_driver = IBDriver(sim_account=True, client_id=CLIENT_ID)
    success = stock_manager.add_driver(ib_driver)
    if not success:
        print("Error connecting to broker data")
        return
    stock_manager.set_log_to_stdout(True)

    options_manager = OptionDataManager()
    options_manager.add_driver(ib_driver)

    try:
        if args.above >= 0:
            await find_stocks(stock_manager, args.above, True, no_scrape=args.info_only)
        elif args.below >= 0:
            await find_stocks(stock_manager, args.below, False, no_scrape=args.info_only)
        elif args.move:
            if args.symbol is None:
                print("No symbol given.")
            else:
                dte = args.dte
                if args.date is not None:
                    date_dt = get_datetime(args.date)
                    dte = (date_dt - current_datetime()).days
                await find_move(stock_manager, options_manager, args.symbol, dte)
        elif args.symbol is not None:
            await get_single_stock_data(
                stock_manager,
                options_manager,
                args.symbol,
                args.dte,
                args.date,
                no_scrape=args.info_only,
                delta=args.delta,
                strike=args.strike,
            )
        else:
            print("No --above or --below argument given")
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())

    if ib_driver:
        ib_driver.disconnect()


parser = ArgumentParser(description="Tool for finding high or low IV stocks")
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
parser.add_argument("--info-only", help="don't do any scraping, just show info", action="store_true")
parser.add_argument("--move", help="show expected move (symbol and dte/date must be given)", action="store_true")


asyncio.run(main(parser))
