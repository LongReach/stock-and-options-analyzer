import asyncio
from argparse import ArgumentParser
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict, Optional

import pandas
from ibapi.common import BarData
from datetime import datetime, timedelta
import argparse
import traceback

from core.common import RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager
from core.stock_data import StockData
from core.utils import (
    str_to_bar_size,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
)

"""
Utility for finding stocks with high or low IV rank.

Run like:
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.iv_finder --help
"""

CLIENT_ID = 20
DB_PATH = "data/iv_data.h5"


async def do_cache(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size: BarSize,
    info_type: RequestedInfoType,
) -> Tuple[Optional[pandas.DataFrame], Optional[str]]:
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type: type of chart data, e.g. trades or implied volatility
    :param info_only: if True, no scraping will be performed
    :param update: if True, update cache with any new stock movements that might have occurred since last update
    :param fresh: if True, clear all cached data and scrape it fresh again
    :return:
    """
    start_date = get_datetime_as_str(current_datetime() - timedelta(days=365))

    df = None
    error_msg: Optional[str] = None
    try:
        # We'll be using cached data
        stock_manager.load_data(symbol, bar_size, info_type)

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
    except Exception as ex:
        print(f"Exception: {ex}")
        print(traceback.format_exc())
        error_msg = f"Exception: {error_msg}"

    return df, error_msg


async def cache_multiple_stocks(
    stock_manager: StockDataManager,
    file_path: str,
):
    """
    Scrapes and caches IV data for multiple stocks or ETFs
    :param stock_manager:  --
    :param file_path: path of file containing list of ticker symbols
    :return:
    """
    bar_size = BarSize.ONE_DAY
    info_type = RequestedInfoType.IMPLIED_VOLATILITY

    symbols_with_error: Dict[Tuple[str, RequestedInfoType], str] = {}

    try:
        with open(file_path, "r") as file:
            for line in file:
                symbol = line.strip()

                print(f"Scraping and caching data for {symbol}...")
                df, error_message = await do_cache(
                    stock_manager=stock_manager,
                    symbol=symbol,
                    bar_size=bar_size,
                    info_type=info_type,
                )
                if error_message is not None:
                    symbols_with_error[(symbol, info_type)] = error_message
                print(f"Done with {symbol}")

    except FileNotFoundError:
        print(f"Could not find file {file_path}")

    if len(symbols_with_error) > 0:
        print("Caching had errors with:")
        for key, error_msg in symbols_with_error.items():
            symbol, info_type = key
            info_type_str = StockData.get_info_type_str(info_type)
            print(f"{symbol}, {info_type_str}: {error_msg}")

    print()


async def find_stocks(stock_manager: StockDataManager, iv_rank: float, above: bool):
    stock_manager.set_log_to_stdout(False)

    found_map: Dict[str, Tuple[float, float]] = {}

    keys = stock_manager.get_cached_keys()
    for key in keys:
        symbol, bar_size, info_type = stock_manager.get_key_elements(key)
        if bar_size != BarSize.ONE_DAY or info_type != RequestedInfoType.IMPLIED_VOLATILITY:
            continue

        print(f"Examining {key}...")
        df, error_msg = await do_cache(stock_manager, symbol, bar_size, info_type)
        if df is None or error_msg is not None:
            print(f"Error finding IV rank with {key}, is: {error_msg}")
            continue
        if len(df) < 10:
            print(f"Not enough data found for {key}")
            continue

        lowest_iv = 1000000.0
        highest_iv = 0.0
        for idx in range(len(df)):
            iv = df.iloc[idx]["close"]
            lowest_iv = min(iv, lowest_iv)
            highest_iv = max(iv, highest_iv)
        latest_iv = df.iloc[-1]["close"]
        rank = (latest_iv - lowest_iv) / (highest_iv - lowest_iv)
        rank *= 100.0
        if above and rank >= iv_rank:
            found_map[key] = (rank, latest_iv)
        if not above and rank <= iv_rank:
            found_map[key] = (rank, latest_iv)

    print(f"Stocks with IV rank {'above' if above else 'below'} {iv_rank}:")
    print("----------------------------------------")
    for key, info in found_map.items():
        symbol, bar_size, info_type = stock_manager.get_key_elements(key)
        rank, iv = info
        print(f"{symbol}: rank is {rank}, IV is {iv}")


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

    try:
        if args.file is not None:
            await cache_multiple_stocks(stock_manager, args.file)

        if args.above >= 0:
            await find_stocks(stock_manager, args.above, True)
        elif args.below >= 0:
            await find_stocks(stock_manager, args.below, False)
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
parser.add_argument("--file", help="file containing list of ticker symbols", required=False, default=None, type=str)
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

asyncio.run(main(parser))
