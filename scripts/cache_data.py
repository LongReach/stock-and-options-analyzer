import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime
import argparse

from core.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager
from core.utils import str_to_bar_size

def print_df(df):
    if df is None:
        print("ERROR: no dataframe")
        return
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head())
    print("Tail:")
    print(df.tail())

async def main(symbol: str, bar_size_str: str, info_only: bool):
    logger = getLogger(__name__)
    basicConfig(filename='cache_data.log', level=INFO)
    stock_manager = StockDataManager()
    ib_driver = None
    if not info_only:
        ib_driver = IBDriver(sim_account=True, client_id=14)
        stock_manager.add_driver(ib_driver)
    stock_manager.set_log_to_stdout(True)
    bar_size = str_to_bar_size(bar_size_str)
    if info_only:
        print(f"Displaying data for {symbol}, {bar_size_str}\n======================================")
    else:
        print(f"Scraping data for {symbol}, {bar_size_str}\n======================================")

    df = None
    try:
        stock_manager.load_data(symbol, bar_size)
        if not info_only:
            success, error_str = await stock_manager.scrape_data_smart(symbol, bar_size, start_date="19700101")
            if not success:
                print(f"Error: {error_str}")
            stock_manager.save_data(symbol, bar_size)
        df = stock_manager.get_pandas_df(symbol, bar_size)
    except Exception as ex:
        print(f"Exception: {ex}")
    print_df(df)
    print()

    if ib_driver:
        ib_driver.disconnect()

parser = argparse.ArgumentParser(description='StockDataManager test')
parser.add_argument("--symbol", help="ticker symbol", required=True, type=str)
parser.add_argument("--barsize", help="bar size, e.g. 1m, 1h, 1d, etc.", required=False, default="1d", type=str)
parser.add_argument("--info-only", help="don't do any scraping, just show info", action="store_true")
args = parser.parse_args()

asyncio.run(main(args.symbol, args.barsize, args.info_only))