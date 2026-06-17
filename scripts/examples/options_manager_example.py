import asyncio
from logging import basicConfig, INFO, getLogger

# module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'core'))
# sys.path.append(module_path)

from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver
from core.option_data_manager import OptionDataManager

"""
Run like:
python -m scripts.options_manager_example
"""

CLIENT_ID = 16
TICKER = "AAPL"
MIN_DAYS_AWAY = 5
MAX_DAYS_AWAY = 70


def print_df(df):
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head(8))
    print("Tail:")
    print(df.tail(8))


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="options_manager_test.log", level=INFO)
    data_driver: BaseDriver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
    try:
        data_driver.connect()
        await asyncio.sleep(1.0)
        options_manager = OptionDataManager()
        options_manager.add_driver(data_driver)

        print(f"Getting expirations for {TICKER}...")
        expirations = await options_manager.get_expirations(TICKER, MIN_DAYS_AWAY, MAX_DAYS_AWAY)
        print(f"Expirations for {TICKER} between {MIN_DAYS_AWAY} and {MAX_DAYS_AWAY} days away are {expirations}")

        print(f"Getting strikes for {TICKER}, {expirations[-1]}...")
        strikes, idx = await options_manager.get_strikes(
            ticker=TICKER,
            expiration=expirations[-1],
            right="C",
            num_above=8,
            num_below=8,
        )
        print(f"Strikes are: {strikes}, idx is {idx}")
        print(f"Closest to ATM strike is {strikes[idx]}")

        print(
            f"\nGetting option chain for {TICKER} at {expirations[-1]}, with strikes from {strikes[0]} to {strikes[-1]}"
        )
        option_data = await options_manager.get_option_chain(
            TICKER, expiration=expirations[-1], right="C", strike=strikes
        )
        # option_data.sort("delta", ascending=True)
        df = option_data.get_dataframe(drop_columns=["date", "full_name"])
        print_df(df)

        # Now get a single option info object
        option_info = await options_manager.get_option_info(TICKER, expirations[-1], "P", strikes[idx])
        print(f"\nOption info is: {option_info.to_dict()}")

        # Get it again, but it should be cached this time
        option_info = await options_manager.get_option_info(TICKER, expirations[-1], "P", strikes[idx])
        print(f"\nCached option info is: {option_info.to_dict()}")

    except Exception as ex:
        print(f"Exception: {ex}")

    data_driver.disconnect()


asyncio.run(main())
