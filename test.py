import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple
from ibapi.common import BarData
from datetime import datetime

from core.ib_driver import IBDriver, BarSize


def print_historical_data(bars: List[Tuple[BarData, datetime]]):
    for bar in bars:
        bar_dict = {"date": bar[0].date, "open": bar[0].open, "close": bar[0].close, "low": bar[0].low,
                    "high": bar[0].high, "volume": float(bar[0].volume)}
        print(f"{bar_dict}")
    print()

async def main():
    logger = getLogger(__name__)
    basicConfig(filename='test.log', level=INFO)
    logger.info("Logging should work")
    ib_driver = IBDriver(sim_account=True, client_id=12)
    try:
        ib_driver.connect()

        results = await ib_driver.get_historical_data("SPY", 10)
        print("Daily bars for SPY are\n------------------------")
        print_historical_data(results)
        results = await ib_driver.get_historical_data("AAPL", 32, bar_size=BarSize.ONE_HOUR)
        print("Hourly bars for AAPL are\n------------------------")
        print_historical_data(results)
        results = await ib_driver.get_historical_data("DIA", 32, bar_size=BarSize.FOUR_HOURS)
        print("Four-hour bars for DIA are\n------------------------")
        print_historical_data(results)
        results = await ib_driver.get_historical_data("GLD", 4, bar_size=BarSize.ONE_DAY, end_date="20250422 16:00:00 US/Eastern")
        print("Daily bars for GLD are\n------------------------")
        print_historical_data(results)
    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()

asyncio.run(main())