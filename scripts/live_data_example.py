import asyncio
from logging import basicConfig, INFO, getLogger
import time
from time import sleep
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

from core.common import HistoricalData, RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
An example of how to get recent data bars (price, volatility) from the market, as well as how to get
live, constantly streaming data.
"""

TICKER = "AAPL"
# If True, communicate with TWS app instead of Gateway
USE_GATEWAY = False
CLIENT_ID = 14


def print_historical_data(bars: HistoricalData):
    for bar in bars.bar_data:
        print(f"{bar}")
    print()


async def print_streaming_data(price_data: HistoricalData, stop_event: asyncio.Event):
    while not stop_event.is_set():
        await asyncio.sleep(2.0)
        bar, dt = price_data.get_current_bar()
        print(f"Bar for {dt} is {bar}")

    print("Loop stopped.")


async def wait_for_keypress(stop_event: asyncio.Event):
    # Run blocking input() in a separate thread
    await asyncio.to_thread(input, "Press ENTER to stop...\n")
    stop_event.set()


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="live_data_test.log", level=INFO)
    ib_driver = IBDriver(
        sim_account=True, client_id=CLIENT_ID, gateway_connection=USE_GATEWAY
    )
    try:
        ib_driver.connect()

        price_data_five, error_str = await ib_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.FIVE_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
        )
        print(f"Five minute bars for {TICKER} (trades) are\n------------------------")
        print_historical_data(price_data_five)

        iv_data, error_str = await ib_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.ONE_DAY,
            request_info_type=RequestedInfoType.IMPLIED_VOLATILITY,
        )
        print(
            f"One day bars for {TICKER} (implied volatility) are\n------------------------"
        )
        print_historical_data(iv_data)

        hv_data, error_str = await ib_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.ONE_DAY,
            request_info_type=RequestedInfoType.HISTORICAL_VOLATILITY,
        )
        print(
            f"One day bars for {TICKER} (historical volatility) are\n------------------------"
        )
        print_historical_data(hv_data)

        price_data_two, error_str = await ib_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=True,
            bar_size=BarSize.TWO_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
            regular_trading_hours_only=False,
        )

    except Exception as ex:
        print(f"Exception: {ex}")

    print("Now printing live data for two minute bars, stand by... (ctrl-c to end)")
    stop_event = asyncio.Event()
    task1 = asyncio.create_task(print_streaming_data(price_data_two, stop_event))
    task2 = asyncio.create_task(wait_for_keypress(stop_event))

    await asyncio.gather(task1, task2)

    ib_driver.disconnect()


asyncio.run(main())
