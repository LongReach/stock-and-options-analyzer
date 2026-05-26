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
Utility for collecting price (or other) data for a particular security, in bar form, then caching it to disk.

Run like:
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.cache_data --help
"""

CLIENT_ID = 13


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
    info_only: bool,
    update: bool,
    fresh: bool,
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
    start_date = "19700101"

    df = None
    error_msg: Optional[str] = None
    try:
        if fresh:
            # We are scraping all the data from brokerage side, replacing any cached data
            stock_manager.clear_data(symbol, bar_size, info_type)
        else:
            # We'll be using cached data
            stock_manager.load_data(symbol, bar_size, info_type)

        if not info_only:
            success, error_str = await stock_manager.scrape_data_smart(
                symbol, bar_size, info_type, start_date=start_date, update_recent=update
            )
            if not success:
                error_msg = error_str
            stock_manager.save_data(symbol, bar_size, info_type)
        df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
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
        success = stock_manager.load_data(symbol=symbol, bar_size=bar_size, info_type=info_type)
        if not success:
            print(f"Could not find data for cache entry {key}")
            continue
        df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
        if df is not None and len(df) > 0:
            earliest_dt = df.iloc[0]["date"]
            earliest_date_str = get_datetime_as_str(earliest_dt)
            latest_dt = df.iloc[-1]["date"]
            latest_date_str = get_datetime_as_str(latest_dt)
            out_line = f"Have cache entry for {key}, earliest data: {earliest_date_str}, latest date: {latest_date_str}"
            if ib_driver:
                head_dt = await ib_driver.get_head_timestamp(symbol, info_type)
                if head_dt is not None and head_dt < earliest_dt:
                    out_line += f", available data begins at: {get_datetime_as_str(head_dt)}"
            print(out_line)
        else:
            print(f"Could not find data for {key}")

async def cache_single_stock(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size_str: str,
    info_type_str: str,
    info_only: bool,
    update: bool,
    fresh: bool,
):
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :param info_only: if True, no scraping will be performed
    :param update: if True, update cache with any new stock movements that might have occurred since last update
    :param fresh: if True, clear all cached data and scrape it fresh again
    :return:
    """
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)

    if info_only:
        print(f"Displaying data for {symbol}, {bar_size_str}\n======================================")
    else:
        action_str = "Updating" if update else "Scraping"
        print(
            f"{action_str} {info_type.value} data for {symbol}, {bar_size_str}\n======================================"
        )

    df, error_str = await do_cache(stock_manager, symbol, bar_size, info_type, info_only, update, fresh)
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
        with open(file_path, 'r') as file:
            for line in file:
                symbol = line.strip()
                info_types_to_cache: List[RequestedInfoType] = [info_type]
                if info_type == RequestedInfoType.TRADES:
                    # Might as well cache this, too
                    info_types_to_cache.append(RequestedInfoType.IMPLIED_VOLATILITY)

                print(f"Scraping and caching data for {symbol}...")
                for info_type_to_cache in info_types_to_cache:
                    df, error_message = await do_cache(stock_manager=stock_manager, symbol=symbol, bar_size=bar_size, info_type=info_type_to_cache, info_only=False, update=True, fresh=False)
                    if error_message is not None:
                        symbols_with_error[(symbol, info_type_to_cache)] = error_message
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

    if args.show:
        await show_cache_contents(stock_manager)
    elif args.symbol:
        await cache_single_stock(
            stock_manager,
            args.symbol,
            args.barsize,
            args.info_type,
            args.info_only,
            args.update,
            args.fresh,
        )
    elif args.file:
        await cache_multiple_stocks(stock_manager, args.file, args.barsize, args.info_type)

    if ib_driver:
        ib_driver.disconnect()


parser = argparse.ArgumentParser(description="Tool for caching market data on disk")
parser.add_argument("--symbol", help="ticker symbol", required=False, default=None, type=str)
parser.add_argument("--file", help="file containing list of ticker symbols", required=False, default=None, type=str)
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
parser.add_argument("--update", help="add more recent data to file", action="store_true")
parser.add_argument("--fresh", help="re-scrape all data", action="store_true")
parser.add_argument("--show", help="show what data is in cache", action="store_true")

asyncio.run(
    main(parser)
)
