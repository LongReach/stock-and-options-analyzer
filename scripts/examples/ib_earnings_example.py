import asyncio
import argparse
from logging import basicConfig, INFO, getLogger

from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver

CLIENT_ID = 18


async def main(ticker: str):
    logger = getLogger(__name__)
    basicConfig(filename="ib_earnings_test.log", level=INFO)
    data_driver: BaseDriver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
    try:
        data_driver.connect()

        ticker = ticker.upper()
        earnings_info, error_str = await data_driver.get_earnings_dates(ticker)

        if error_str:
            print(f"Error: {error_str}")

        if earnings_info.upcoming:
            print(f"Upcoming earnings dates for {ticker}:")
            for d in earnings_info.upcoming:
                print(f"  {d}")
        else:
            print(f"No upcoming earnings dates found for {ticker}.")

        print()

        if earnings_info.past:
            print(f"Past earnings dates for {ticker}:")
            for d in earnings_info.past:
                print(f"  {d}")
        else:
            print(f"No past earnings dates found for {ticker}.")

    except Exception as ex:
        print(f"Exception: {ex}")

    data_driver.disconnect()


parser = argparse.ArgumentParser(description="Earnings dates via Interactive Brokers")
parser.add_argument("ticker", help="Stock ticker symbol, e.g. AAPL")
args = parser.parse_args()

asyncio.run(main(args.ticker))
