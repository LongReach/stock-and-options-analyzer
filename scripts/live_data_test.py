import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

from core.common import HistoricalData, RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str


def print_historical_data(bars: HistoricalData):
    for bar in bars.bar_data_list:
        print(f"{bar}")
    print()

async def main():
    logger = getLogger(__name__)
    basicConfig(filename='live_data_test.log', level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=45)
    try:
        ib_driver.connect()

        results, error_str = await ib_driver.get_historical_data("SPY", num_bars=10, live_data=False, bar_size=BarSize.FIVE_MINUTES, request_info_type=RequestedInfoType.TRADES)
        print("Five minute bars for SPY (trades) are\n------------------------")
        print_historical_data(results)

        results, error_str = await ib_driver.get_historical_data("SPY", num_bars=50, live_data=False, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.IMPLIED_VOLATILITY)
        print("One day bars for SPY (implied volatility) are\n------------------------")
        print_historical_data(results)

        results, error_str = await ib_driver.get_historical_data("SPY", num_bars=50, live_data=False, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.HISTORICAL_VOLATILITY)
        print("One day bars for SPY (historical volatility) are\n------------------------")
        print_historical_data(results)

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()

asyncio.run(main())