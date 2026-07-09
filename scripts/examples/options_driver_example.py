import asyncio
from logging import basicConfig, INFO, getLogger

# module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'core'))
# sys.path.append(module_path)

from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver

"""
Demonstrates how to obtain options info from IBDriver.

Run like:
python -m scripts.options_driver_example
"""

CLIENT_ID = 15

TICKER = "SPY"
STRIKE = 770.0
EXPIRATION = "20260618"


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="options_driver_test.log", level=INFO)
    data_driver: BaseDriver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
    try:
        data_driver.connect()

        # contract_id = contract_details.contract.conId
        option_chain_info, error_str = await data_driver.get_options_chain_info(TICKER)
        print(f"Get options info for {TICKER} from exchange {option_chain_info.exchange}")
        exp_list = sorted(option_chain_info.expirations)
        print(f"Expirations are {exp_list}")
        strike_list = sorted(option_chain_info.strikes)
        print(f"Strikes are {strike_list}")

        await asyncio.sleep(1.0)

        option_info, error_str = await data_driver.get_option_info_single(
            TICKER, is_call=True, strike=STRIKE, expiration=EXPIRATION
        )
        if error_str:
            print(f"Error: {error_str}")
            data_driver.disconnect()
            return
        option_info, error_str = await data_driver.get_greeks(option_info)
        if error_str:
            print(f"Error getting the Greeks: {error_str}")
            data_driver.disconnect()
            return
        print(f"Option info is: {option_info.to_dict()}")

        # Extra experimental
        print(f"\nGetting option info for expiration date {EXPIRATION}")
        options_info_list, error_str = await data_driver.get_option_info(TICKER, is_call=True, expiration=EXPIRATION)
        if error_str:
            print(f"Error: {error_str}")
            data_driver.disconnect()
            return
        for option_info in options_info_list:
            print(f"Option info is {option_info.full_name}")

    except Exception as ex:
        print(f"Exception: {ex}")

    data_driver.disconnect()


asyncio.run(main())
