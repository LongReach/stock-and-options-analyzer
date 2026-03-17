import asyncio
import os
import sys
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

# module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'core'))
# sys.path.append(module_path)

from core.common import RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
Run like:
python -m scripts.options_driver_example
"""

CLIENT_ID = 15


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="options_driver_test.log", level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=CLIENT_ID)
    try:
        ib_driver.connect()
        contract_details_list, error_str = await ib_driver.get_contract_details("SPY")
        if error_str or len(contract_details_list) == 0:
            print(f"Error: {error_str}")
            return
        contract_details = contract_details_list[0]

        print(f"Got {contract_details.contract}")
        # contract_id = contract_details.contract.conId
        option_info, error_str = await ib_driver.get_options_chain_info(contract_details)
        print(f"Get options info from exchange {option_info.exchange}")
        exp_list = sorted(option_info.expirations)
        print(f"Expirations are {exp_list}")
        strike_list = sorted(option_info.strikes)
        print(f"Strikes are {strike_list}")

        await asyncio.sleep(1.0)

        contract_details_list, error_str = await ib_driver.get_contract_details(
            "SPY", is_option=True, is_call=True, strike=600.0, expiration="20250620"
        )
        if error_str or len(contract_details_list) == 0:
            print(f"Error: {error_str}")
            return
        contract_details = contract_details_list[0]
        option_info, error_str = await ib_driver.get_greeks(contract_details)
        print(f"Option info is: {option_info.to_dict()}")

        # Extra experimental
        print("\nExtra experimental part")
        contract_details_list, error_str = await ib_driver.get_contract_details(
            "SPY", is_option=True, is_call=True, expiration="20250620"
        )
        for contract_details in contract_details_list:
            print(f"Contract Details are {ib_driver.get_full_symbol_from_contract_details(contract_details)}")

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


asyncio.run(main())
