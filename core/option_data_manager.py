import asyncio
import math
from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from pandas.core.resample import maybe_warn_args_and_kwargs

from core.common import HistoricalData
from core.utils import (
    BarSize,
    bar_size_to_str,
    str_to_bar_size,
    bar_size_to_time,
    get_datetime,
    get_datetime_as_str,
)
from core.options_data import OptionData, OptionDataException
from core.ib_driver import IBDriver

_logger = logging.getLogger(__name__)


class OptionDataManager:

    def __init__(self):
        pass

    def add_driver(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        if not ib_driver.is_connected():
            self._ib_driver.connect()

    async def get_expirations(
        self, ticker: str, min_days_away: int, max_days_away: int
    ):
        contract_details, error_str = await self._ib_driver.get_contract_details_single(
            ticker
        )
        options_chain_info, error_str = await self._ib_driver.get_options_chain_info(
            contract_details
        )
        if error_str:
            raise OptionDataException(error_str)

        dt_now = datetime.now()
        out_expirations = []
        seconds_per_day = 60 * 60 * 24
        sorted_expirations = sorted(options_chain_info.expirations)
        for exp in sorted_expirations:
            exp_dt = get_datetime(exp)
            time_delta = exp_dt - dt_now
            days_away = int(time_delta.total_seconds() / float(seconds_per_day))
            if min_days_away <= days_away <= max_days_away:
                out_expirations.append(exp)
        return out_expirations

    async def get_option_chain(
        self,
        ticker: str,
        expiration: str,
        right: str,
        min_delta: float = 0.08,
        max_delta: float = 0.7,
    ) -> OptionData:
        contract_details_list, error_str = await self._ib_driver.get_contract_details(
            ticker, is_option=True, is_call=(right == "C"), expiration=expiration
        )
        if error_str:
            raise OptionDataException(error_str)

        option_data = OptionData(ticker, datetime.now())
        if len(contract_details_list) == 0:
            return option_data

        # Get the underlying price
        option_info, error_str = await self._ib_driver.get_greeks(
            contract_details_list[0]
        )
        if not option_info:
            raise OptionDataException(
                f"Couldn't get underlying price, error is {error_str}"
            )
        underlying_price = option_info.underlying_price
        print(f"**** Underlying price is {underlying_price}")

        # Create a list in which contract details with strike price closest to underlying are at the top of the list
        sortable_cd_list = [
            (cd, math.fabs(cd.contract.strike - underlying_price))
            for cd in contract_details_list
        ]
        sortable_cd_list.sort(key=lambda x: x[1])

        error_count = 0
        max_allowed_errors = 5
        for tup in sortable_cd_list:
            contract_details = tup[0]
            print(
                f"**** Fetching Greeks for {self._ib_driver.get_full_symbol_from_contract_details(contract_details)}"
            )
            option_info, error_str = await self._ib_driver.get_greeks(contract_details)
            if error_str:
                print(
                    f"**** Error: {error_str}, debug info {option_info.get_debug_info()}"
                )
                if error_count > max_allowed_errors:
                    break
                error_count += 1
            else:
                # Can we break out of loop due to reaching sufficiently high or low delta?
                if right == "C":
                    if (
                        contract_details.contract.strike > underlying_price
                        and option_info.delta < min_delta
                    ):
                        print(f"**** no 1 {option_info.delta}")
                        break
                    if (
                        contract_details.contract.strike <= underlying_price
                        and option_info.delta > max_delta
                    ):
                        print("**** no 2")
                        break
                else:
                    if (
                        contract_details.contract.strike < underlying_price
                        and option_info.delta < min_delta
                    ):
                        print("**** no 3")
                        break
                    if (
                        contract_details.contract.strike >= underlying_price
                        and option_info.delta > max_delta
                    ):
                        print("**** no 4")
                        break

                option_data.add_data(option_info)

        if error_count > max_allowed_errors:
            raise OptionDataException(
                f"Too many errors getting option chain for {ticker}"
            )

        return option_data
