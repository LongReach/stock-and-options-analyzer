import asyncio
from logging import basicConfig, INFO, getLogger
import time
from time import sleep
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

from core.common import HistoricalData, RequestedInfoType, OrderType, OrderAction
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
An example of how to place an order with IB. This script should be used while the TWS desktop application
is running, in paper-trading mode. Depending on settings, it will either:

* place a stop order for 5 shares of SPY, with the stop (for buying) five dollars above SPY's recent high trading price.
* place a limit order for 5 shares of SPY, five dollars below SPY's recent low trading price
* place a stop order for shorting, five dollars below SPY's recent low trading price
* Place a stop limit order for shorting below current price, with an attached stop
"""

CLIENT_ID = 18
TICKER = "SPY"

modes = ["long", "short", "buy limit", "stop limit"]
MODE = 3


async def long_order(ib_driver: IBDriver, price_data: HistoricalData):
    bar_highs = [bar.high for bar in price_data.bar_data]
    highest_recent_price = max(bar_highs)

    stop_order_price = highest_recent_price + 5.0
    order_info, error_str = await ib_driver.place_order(
        symbol_full=TICKER,
        action=OrderAction.BUY,
        quantity=5,
        price=stop_order_price,
        order_type=OrderType.STOP,
    )
    if error_str is not None:
        print(f"Error placing order: {error_str}")
        return

    print("Long order placed!")
    print(f"Order info is: {order_info.get_info_str()}")


async def short_order(ib_driver: IBDriver, price_data: HistoricalData):
    bar_lows = [bar.low for bar in price_data.bar_data]
    lowest_recent_price = max(bar_lows)

    stop_order_price = lowest_recent_price - 5.0
    order_info, error_str = await ib_driver.place_order(
        symbol_full=TICKER,
        action=OrderAction.SELL,
        quantity=5,
        price=stop_order_price,
        order_type=OrderType.STOP,
    )
    if error_str is not None:
        print(f"Error placing order: {error_str}")
        return

    print("Short order placed!")
    print(f"Order info is: {order_info.get_info_str()}")


async def limit_order(ib_driver: IBDriver, price_data: HistoricalData):
    bar_lows = [bar.low for bar in price_data.bar_data]
    lowest_recent_price = max(bar_lows)

    limit_order_price = lowest_recent_price - 5.0
    order_info, error_str = await ib_driver.place_order(
        symbol_full=TICKER,
        action=OrderAction.BUY,
        quantity=5,
        price=limit_order_price,
        order_type=OrderType.LIMIT,
    )
    if error_str is not None:
        print(f"Error placing order: {error_str}")
        return

    print("Limit order placed!")
    print(f"Order info is: {order_info.get_info_str()}")


async def stop_limit_order(ib_driver: IBDriver, price_data: HistoricalData):
    bar_lows = [bar.low for bar in price_data.bar_data]
    lowest_recent_price = max(bar_lows)

    limit_order_price = lowest_recent_price - 5.0
    order_info, error_str = await ib_driver.place_order(
        symbol_full=TICKER,
        action=OrderAction.SELL,
        quantity=5,
        price=limit_order_price,
        order_type=OrderType.STOP_LIMIT,
    )
    if error_str is not None:
        print(f"Error placing order: {error_str}")
        return

    # Now, the attached stop order
    order_info_2, error_str_2 = await ib_driver.place_order(
        symbol_full=TICKER,
        action=OrderAction.BUY,
        quantity=5,
        price=limit_order_price + 5,
        order_type=OrderType.STOP,
        parent_order=order_info,
    )
    if error_str_2 is not None:
        print(f"Error placing order: {error_str_2}")
        return

    print("Stop limit order placed!")
    print(f"Main order info is: {order_info.get_info_str()}")
    print(f"Stop order info is: {order_info_2.get_info_str()}")


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="place_order_example.log", level=INFO)
    ib_driver = IBDriver(
        sim_account=True, client_id=CLIENT_ID, gateway_connection=False
    )
    try:
        ib_driver.connect()

        # Get some recent bars of trading data, then work out the highest price recently achieved
        price_data, error_str = await ib_driver.get_historical_data(
            TICKER,
            num_bars=4,
            live_data=False,
            bar_size=BarSize.FIVE_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
        )
        if error_str is not None:
            print(f"Failed to get price data for {TICKER}. Error is: {error_str}")
            return

        mode_string = modes[MODE]
        if mode_string == "long":
            await long_order(ib_driver, price_data)
        elif mode_string == "short":
            await short_order(ib_driver, price_data)
        elif mode_string == "buy limit":
            await limit_order(ib_driver, price_data)
        elif mode_string == "stop limit":
            await stop_limit_order(ib_driver, price_data)

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


asyncio.run(main())
