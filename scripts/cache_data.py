import asyncio
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
from core.stock_data import StockData
from core.utils import (
    str_to_bar_size,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
)

"""
Utility for collecting price (or other) data for a particular security, in bar form, then caching it to disk.
Building the cache from scratch can take a long time, on the order of half and hour, and often requires 
multiple runs of this program, due to data-throttling timeouts from Interactive Brokers. However, once the
cache exists, updating it is relatively quick.

Run like:
------------------------
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.cache_data --help

To build data cache for iv_finder tool
------------------------
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\iv_data.h5 --info-type iv

To see cache contents:
python -m scripts.cache_data --db .\data\iv_data.h5 --info-type iv --show
"""

CLIENT_ID = 13
DB_PATH = "data/market_data.h5"
NUM_ACCEPTABLE_BARS = 50
# TODO: this is in days, need values for other timeframes
ACCEPTABLE_RECENCY = 2
MAX_ERRORS_ALLOWED = 10


class ScrapeLevel(IntEnum):
    """Specifies level of scraping to do"""

    FULL = 0  # both old data and recent data
    RECENT = 1  # recent data only
    NONE = 2  # don't scrape any data


def print_df(df):
    if df is None:
        print("ERROR: no dataframe")
        return
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head())
    print("Tail:")
    print(df.tail())


async def do_cache(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size: BarSize,
    info_type: RequestedInfoType,
    scrape_level: ScrapeLevel,
) -> Tuple[Optional[pandas.DataFrame], Optional[str]]:
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type: type of chart data, e.g. trades or implied volatility
    :param scrape_level: full, recent, or none
    :return:
    """
    if scrape_level == ScrapeLevel.FULL:
        if info_type == RequestedInfoType.IMPLIED_VOLATILITY:
            start_date = get_datetime_as_str(current_datetime() - timedelta(days=365), date_only=True)
        else:
            start_date = "19700101"
    else:
        start_date = ""

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
    except Exception as ex:
        print(f"Exception: {ex}")
        print(traceback.format_exc())
        error_msg = f"Exception: {error_msg}"

    return df, error_msg


async def show_cache_contents(stock_manager: StockDataManager):
    """Prints information about data currently in cache"""
    stock_manager.set_log_to_stdout(False)
    ib_driver: Optional[IBDriver] = stock_manager.driver
    keys: List[str] = stock_manager.get_cached_keys()
    print("Cache contents\n======================================")
    for key in keys:
        symbol, bar_size, info_type = StockDataManager.get_key_elements(key)

        entry_type: str = "cache"
        metadata = stock_manager.get_metadata(symbol, bar_size, info_type)
        if metadata is not None:
            current_dt = current_datetime()
            num_bars, earliest_dt, latest_dt = metadata
            entry_type = "metadata"
        else:
            success = stock_manager.load_data(symbol=symbol, bar_size=bar_size, info_type=info_type)
            if not success:
                print(f"Could not find data for cache entry {key}")
                continue
            df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
            if df is not None and len(df) > 0:
                earliest_dt = df.iloc[0]["date"]
                latest_dt = df.iloc[-1]["date"]
                num_bars = len(df)
            else:
                print(f"Not enough data for cache entry {key}")
                continue
            # This will save the metadata, if it doesn't exist in DB
            stock_manager.save_data(symbol=symbol, bar_size=bar_size, info_type=info_type)

        earliest_date_str = get_datetime_as_str(earliest_dt)
        latest_date_str = get_datetime_as_str(latest_dt)
        out_line = f"Have {entry_type} entry for {key}, num bars: {num_bars}, earliest data: {earliest_date_str}, latest date: {latest_date_str}"
        if ib_driver and False:
            head_dt = await ib_driver.get_head_timestamp(symbol, info_type)
            if head_dt is not None and head_dt < earliest_dt:
                out_line += f", available data begins at: {get_datetime_as_str(head_dt)}"
        print(out_line)

        stock_manager.unload_data(symbol, bar_size, info_type)


async def cache_single_stock(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size_str: str,
    info_type_str: str,
    info_only: bool,
):
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :param info_only: if True, no scraping will be performed
    :return:
    """
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)

    if info_only:
        print(f"Displaying data for {symbol}, {bar_size_str}\n======================================")
    else:
        print(f"Scraping {info_type.value} data for {symbol}, {bar_size_str}\n======================================")

    df, error_str = await do_cache(
        stock_manager, symbol, bar_size, info_type, scrape_level=(ScrapeLevel.NONE if info_only else ScrapeLevel.FULL)
    )
    if df is not None:
        print_df(df)
    if error_str:
        print(f"Error caching single stock: {error_str}")
    print()


async def cache_multiple_stocks(
    stock_manager: StockDataManager,
    file_path: str,
    bar_size_str: str,
    info_type_str: str,
):
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param file_path: path of file containing list of ticker symbols
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :return:
    """
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)

    symbols_with_error: Dict[Tuple[str, RequestedInfoType], str] = {}

    try:
        with open(file_path, "r") as file:
            error_count: int = 0
            for line in file:
                symbol = line.strip()

                metadata = stock_manager.get_metadata(symbol, bar_size, info_type)
                if metadata is not None:
                    current_dt = current_datetime()
                    num_bars, earliest_dt, latest_dt = metadata
                    if num_bars > NUM_ACCEPTABLE_BARS and (current_dt - latest_dt).days <= ACCEPTABLE_RECENCY:
                        # No need to cache. Data is fresh enough
                        print(f"Data scrape unnecessary for {symbol}. Have {num_bars} of data ending on {latest_dt}")
                        continue

                print(f"Scraping and caching data for {symbol}...")
                df, error_message = await do_cache(
                    stock_manager=stock_manager,
                    symbol=symbol,
                    bar_size=bar_size,
                    info_type=info_type,
                    scrape_level=ScrapeLevel.FULL,
                )
                if error_message is not None:
                    print(f"Error for symbol {symbol}: {error_message}")
                    symbols_with_error[(symbol, info_type)] = error_message
                    error_count += 1
                    if error_count > MAX_ERRORS_ALLOWED:
                        print("Too many errors, stopping program.")
                        break
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


async def remove_single_stock(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size_str: str,
    info_type_str: str,
):
    """
    Removes a single stock from cache.

    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    """
    print(f"Removing stock from cache: symbol is {symbol}, bar size is {bar_size_str}, info type is {info_type_str}")
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)
    stock_manager.clear_data(symbol, bar_size, info_type, remove_from_cache=True)


async def main(parser: argparse.ArgumentParser):
    """Top-level function, unpacks arguments and calls functions that do the work"""
    args = parser.parse_args()

    logger = getLogger(__name__)
    basicConfig(filename="cache_data.log", level=INFO)
    stock_manager = StockDataManager()
    ib_driver = None
    if not args.info_only:
        ib_driver = IBDriver(sim_account=True, client_id=CLIENT_ID)
        success = stock_manager.add_driver(ib_driver)
        if not success:
            print("Error connecting to broker data")
            return
    stock_manager.set_log_to_stdout(True)
    stock_manager.set_db_path(args.db)

    try:
        if args.show:
            await show_cache_contents(stock_manager)
        elif args.symbol:
            if args.remove:
                await remove_single_stock(stock_manager, args.symbol, args.barsize, args.info_type)
            else:
                await cache_single_stock(
                    stock_manager,
                    args.symbol,
                    args.barsize,
                    args.info_type,
                    args.info_only,
                )
        elif args.file:
            await cache_multiple_stocks(stock_manager, args.file, args.barsize, args.info_type)
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())

    if ib_driver:
        ib_driver.disconnect()


parser = argparse.ArgumentParser(description="Tool for caching market data on disk")
parser.add_argument("--symbol", help="ticker symbol", required=False, default=None, type=str)
parser.add_argument("--file", help="file containing list of ticker symbols", required=False, default=None, type=str)
parser.add_argument("--db", help="path to database file", required=False, default=DB_PATH, type=str)
parser.add_argument(
    "--barsize",
    help="bar size, e.g. 1m, 1h, 1d, etc.",
    required=False,
    default="1d",
    type=str,
)
parser.add_argument(
    "--info-type",
    help="type of info, e.g. tr, iv, hv, al",
    required=False,
    default="tr",
    type=str,
)
parser.add_argument("--info-only", help="don't do any scraping, just show info", action="store_true")
parser.add_argument("--show", help="show what data is in cache", action="store_true")
parser.add_argument("--remove", help="remove symbol from cache", action="store_true")

asyncio.run(main(parser))
