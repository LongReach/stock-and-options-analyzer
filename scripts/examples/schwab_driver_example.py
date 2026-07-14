import asyncio

from core.base_driver import BaseDriver
from core.common import HistoricalData
from core.schwab.schwab_driver import SchwabDriver
from core.utils import BarSize, get_datetime_as_str

"""
Exercises the Schwab driver (Phase Two, Version One):
  1. Daily OHLC bars for SPY
  2. Recent one-minute bars for SPY
  3. get_most_recent_data() for AAPL
  4. A live one-minute candle stream for SPY (CHART_EQUITY)

Credentials come from .env (see schwab_driver.py). Streaming only produces new candles while the market is
open; run during regular trading hours to see live updates.
"""

STREAM_SECONDS = 60
POLL_SECONDS = 3


def print_historical_data(bars: HistoricalData):
    for bar_dict, dt in bars.get_zipped_lists():
        print(
            f"  {get_datetime_as_str(dt):<28}"
            f"O={bar_dict['open']:>10.2f}  H={bar_dict['high']:>10.2f}  "
            f"L={bar_dict['low']:>10.2f}  C={bar_dict['close']:>10.2f}  V={bar_dict['volume']:>12,.0f}"
        )
    print()


async def demo_daily_bars(driver: BaseDriver):
    print("Daily bars for SPY (last 10)\n----------------------------")
    bars, error_str = await driver.get_historical_data("SPY", num_bars=10, bar_size=BarSize.ONE_DAY)
    if error_str:
        print(f"  Error: {error_str}\n")
        return
    print_historical_data(bars)


async def demo_minute_bars(driver: BaseDriver):
    print("One-minute bars for SPY (last 10)\n---------------------------------")
    bars, error_str = await driver.get_historical_data("SPY", num_bars=10, bar_size=BarSize.ONE_MINUTE)
    if error_str:
        print(f"  Error: {error_str}\n")
        return
    print_historical_data(bars)


async def demo_most_recent(driver: BaseDriver):
    print("Most recent daily bar for AAPL\n------------------------------")
    recent, error_str = await driver.get_most_recent_data("AAPL", bar_size=BarSize.ONE_DAY)
    if error_str:
        print(f"  Error: {error_str}\n")
        return
    if recent is None:
        print("  No data returned.\n")
        return
    bar_dict, dt = recent
    print(f"  {get_datetime_as_str(dt)}  close={bar_dict['close']:.2f}  volume={bar_dict['volume']:,.0f}\n")


async def demo_streaming(driver: BaseDriver):
    print(f"Streaming one-minute candles for SPY (~{STREAM_SECONDS}s)\n---------------------------------------------")
    bars, error_str = await driver.get_historical_data("SPY", num_bars=3, bar_size=BarSize.ONE_MINUTE, live_data=True)
    if error_str:
        print(f"  Error starting stream: {error_str}\n")
        return

    print("  Seeded with recent bars:")
    print_historical_data(bars)

    print("  Watching for live updates (new/updated minute candles will appear below)...")
    seen_timestamps = {dt for _, dt in bars.get_zipped_lists()}
    elapsed = 0
    while elapsed < STREAM_SECONDS:
        await asyncio.sleep(POLL_SECONDS)
        elapsed += POLL_SECONDS
        for bar_dict, dt in bars.get_zipped_lists():
            if dt not in seen_timestamps:
                seen_timestamps.add(dt)
                print(
                    f"  [live] {get_datetime_as_str(dt):<28}"
                    f"O={bar_dict['open']:.2f}  H={bar_dict['high']:.2f}  "
                    f"L={bar_dict['low']:.2f}  C={bar_dict['close']:.2f}  V={bar_dict['volume']:,.0f}"
                )

    await driver.cancel_historical_data(bars)
    print("  Stream cancelled.\n")


async def main():
    driver: BaseDriver = SchwabDriver.create()
    if not driver.connect():
        print("Failed to connect to Schwab. Check credentials in .env and the token file.")
        return

    try:
        await demo_daily_bars(driver)
        await demo_minute_bars(driver)
        await demo_most_recent(driver)
        await demo_streaming(driver)
    except Exception as ex:
        print(f"Exception: {ex}")
    finally:
        driver.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
