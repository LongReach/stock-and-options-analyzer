import asyncio
from logging import basicConfig, INFO, getLogger
import time
from time import sleep
from typing import List, Tuple, Dict, Optional
from ibapi.common import BarData
from datetime import datetime

from core.common import (
    HistoricalData,
    RequestedInfoType,
    OrderType,
    OrderAction,
    OrderInfo,
    OrderStatus,
    PositionsInfo,
)
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
An example of how to place an order with IB. This script should be used while the TWS desktop application
is running, in paper-trading mode. It will place an order with an attached stop.

It also collects and prints information about the orders and the position from IB.
"""

CLIENT_ID = 18
TICKER = "SPY"
ACTION = OrderAction.SELL
ORDER_TYPE = OrderType.STOP


async def make_orders(
    ib_driver: IBDriver, price_data: HistoricalData
) -> Optional[Tuple[OrderInfo, OrderInfo]]:
    bar_highs = [bar.high for bar in price_data.bar_data]
    highest_recent_price = max(bar_highs)
    bar_lows = [bar.low for bar in price_data.bar_data]
    lowest_recent_price = min(bar_lows)

    if ACTION == OrderAction.BUY:
        entry_price = highest_recent_price + 5.0
        stop_out_price = entry_price - (highest_recent_price - lowest_recent_price)
    else:
        entry_price = lowest_recent_price - 5.0
        stop_out_price = entry_price + (highest_recent_price - lowest_recent_price)

    order_info, error_str = await ib_driver.place_order(
        symbol_full=TICKER,
        action=ACTION,
        quantity=5,
        price=entry_price,
        order_type=ORDER_TYPE,
        transmit=False,
    )
    if error_str is not None:
        print(f"Error placing order: {error_str}")
        return None

    print("Main order placed!")
    print(f"Order info is: {order_info.get_info_str()}")

    stop_action = OrderAction.SELL if ACTION == OrderAction.BUY else OrderAction.BUY
    stop_order_info, error_str = await ib_driver.place_order(
        symbol_full=TICKER,
        action=stop_action,
        quantity=5,
        price=stop_out_price,
        order_type=OrderType.STOP,
        parent_order=order_info,
        transmit=True,
    )
    if error_str is not None:
        print(f"Error placing stop order: {error_str}")
        return None

    return order_info, stop_order_info


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

        result = await make_orders(ib_driver, price_data)

        async def _get_positions():
            position_info, _error_str = await ib_driver.get_positions()
            if _error_str:
                print(f"Positions gotten, error is {_error_str}")
            return position_info

        positions_task: Optional[asyncio.Task] = None

        if result:
            print("Orders placed successfully.")
            order_info, stop_order_info = result
            while order_info.order_status not in [
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
            ] or stop_order_info.order_status not in [
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
            ]:
                print(f"Main order: {order_info.get_info_str()}")
                print(f"Stop order: {stop_order_info.get_info_str()}")
                if positions_task is None:
                    positions_task = asyncio.create_task(_get_positions())
                if positions_task.done():
                    info: PositionsInfo = positions_task.result()
                    descs = info.get_positions()
                    for desc in descs:
                        print(f"Position: {desc.to_string()}")
                    positions_task = None
                await asyncio.sleep(2.0)

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


asyncio.run(main())
