import asyncio
import math
from asyncio import CancelledError
from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from ibapi.contract import ContractDetails
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from pandas.core.resample import maybe_warn_args_and_kwargs

from core.common import HistoricalData, OptionInfo
from core.utils import (
    BarSize,
    bar_size_to_str,
    str_to_bar_size,
    bar_size_to_time,
    get_datetime,
    get_datetime_as_str,
    current_datetime
)
from core.options_data import OptionData, OptionDataException
from core.ib_driver import IBDriver

_logger = logging.getLogger(__name__)


class OptionDataManager:

    def __init__(self):
        self._logger = logging.getLogger(__name__)

    def add_driver(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        if not ib_driver.is_connected():
            self._ib_driver.connect()

    async def get_expirations(
        self, ticker: str, min_days_away: int, max_days_away: int
    ) -> List[str]:
        """
        Gets a set of available expiration dates
        :param ticker: ticker for underlying, e.g. AAPL
        :param min_days_away: minimum number of days until expiration (relative to now)
        :param max_days_away: maximum number of days until expiration
        :return: list of IB-style datetimes
        """
        self._logger.info(f"Getting expirations for {ticker}, min_days_away={min_days_away}, max_days_away={max_days_away}")
        contract_details, error_str = await self._ib_driver.get_contract_details_single(
            ticker
        )
        options_chain_info, error_str = await self._ib_driver.get_options_chain_info(
            contract_details
        )
        if error_str:
            raise OptionDataException(error_str)

        dt_now = current_datetime()
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
        """
        Returns a whole options chain, within an OptionData object.
        :param ticker: symbol of underlying
        :param expiration: expiration date, IB style
        :param right: "C" for call, "P" for put
        :param min_delta: don't want data for options contracts with delta below this value
        :param max_delta: don't want data for options contracts with delta above this value
        :return: OptionData, holding all retrieved data
        :raise OptionDataException: if some problem encountered
        """
        self._logger.info(f"Getting option chain for {ticker}, expiration={expiration}, right={right}, min_delta={min_delta}, max_delta={max_delta}")
        contract_details_list, error_str = await self._ib_driver.get_contract_details(
            ticker, is_option=True, is_call=(right == "C"), expiration=expiration
        )
        if error_str:
            raise OptionDataException(error_str)

        option_data = OptionData(ticker, current_datetime())
        if len(contract_details_list) == 0:
            return option_data

        # Get the underlying price
        ret_tup, error_str = await self._ib_driver.get_most_recent_data(ticker, BarSize.ONE_MINUTE)
        if not ret_tup or error_str:
            raise OptionDataException(
                f"Couldn't get underlying price, error is {error_str}"
            )
        underlying_price = ret_tup[0]["close"]
        if underlying_price <= 0.0:
            raise OptionDataException("Couldn't get underlying price")

        # Create a list in which contract details with strike price closest to underlying are at the top of the list
        sortable_cd_list = [
            (cd, math.fabs(cd.contract.strike - underlying_price))
            for cd in contract_details_list
        ]
        sortable_cd_list.sort(key=lambda x: x[1])

        new_list = [item[0] for item in sortable_cd_list]
        await self._batch_collect_options_data(new_list, option_data, underlying_price, right=right, max_delta=max_delta, min_delta=min_delta)

        option_data.sort("strike")
        return option_data

    async def _batch_collect_options_data(self, contract_details_list: List[ContractDetails], option_data: OptionData, underlying_price: float, right: str = "C", max_delta: float = 0.8, min_delta: float = 0.07):
        """
        Grabs data for a bunch of different options contracts in parallel, rather than one-at-a-time (which is
        very slow). Effectively, multiple requests go out to IB at the same time. asyncio.Tasks are used to wait
        for results.

        :param contract_details_list: one ContractDetails for each option
        :param option_data: this will receive the results
        :param underlying_price: price of underlying security
        :param right: "C" for call, "P" for put
        :param min_delta: don't want data for options contracts with delta below this value
        :param max_delta: don't want data for options contracts with delta above this value
        """

        # This limits the number of requests active with IB at once
        MAX_TO_RETRIEVE_AT_ONCE = 10
        MAX_ERRORS = 5

        # Keep tracks of which entries in contract_details_list we've made tasks for
        current_idx: int = 0
        # A queue for Greeks-retrieval tasks that are still running
        task_queue: List[asyncio.Task] = []
        ignore_strikes_below: float = 0
        ignore_strikes_above: float = 1000000000.0
        error_count = 0

        def _set_ignorable_strikes(_option_info: OptionInfo):
            """Helper function to set values of ignore_strikes_below, ignore_strikes_above"""
            nonlocal ignore_strikes_above, ignore_strikes_below
            _strike = _option_info.strike
            if right == "C":
                if _strike > underlying_price and _option_info.delta < min_delta:
                    ignore_strikes_above = _strike
                if _strike <= underlying_price and _option_info.delta > max_delta:
                    ignore_strikes_below = _strike
            else:
                if _strike < underlying_price and _option_info.delta < min_delta:
                    ignore_strikes_below = _strike
                if _strike >= underlying_price and _option_info.delta > max_delta:
                    ignore_strikes_above = _strike

        def _test_for_ignorable(_contract_details: ContractDetails):
            """Returns True if we don't need info for a particular options contract due to strike being too high or low"""
            return not (ignore_strikes_below <= _contract_details.contract.strike <= ignore_strikes_above)

        while current_idx < len(contract_details_list) or len(task_queue) > 0:

            # Create new tasks as needed, keeping the queue of active tasks as full as possible
            while len(task_queue) < MAX_TO_RETRIEVE_AT_ONCE and current_idx < len(contract_details_list):
                contract_details = contract_details_list[current_idx]
                if not _test_for_ignorable(contract_details):
                    task_name = self._ib_driver.get_full_symbol_from_contract_details(contract_details)
                    self._logger.debug(f"Creating retrieval task for {task_name}")
                    task = asyncio.create_task(self._ib_driver.get_greeks(contract_details), name=task_name)
                    task_queue.append(task)
                current_idx += 1

            # Keep looping until some task completes, then grab the results
            while len(task_queue) > 0:
                remove_idx = -1
                for idx, task in enumerate(task_queue):
                    if task.done():
                        error_found = False
                        # Task has been completed, cancelled, or hit an exception
                        try:
                            option_info, error_str = task.result()
                        except CancelledError:
                            pass
                        except Exception as e:
                            self._logger.error(f"Exception while retrieving Greeks for {task.get_name()}: {e}")
                            error_found = True
                        else:
                            if error_str:
                                self._logger.error(f"Error while retrieving Greeks for {task.get_name()}: {error_str}")
                                error_found = True
                            else:
                                self._logger.debug(f"Task done for {option_info.full_name}")
                                _set_ignorable_strikes(option_info)
                                if ignore_strikes_below <= option_info.strike <= ignore_strikes_above:
                                    option_data.add_data(option_info)

                        if error_found:
                            if error_count >= MAX_ERRORS:
                                raise OptionDataException("Too many errors fetching option chain")
                            error_count += 1
                            # Put an empty OptionInfo object into option_data
                            empty_option_info = OptionInfo.make_empty_option_info(task.get_name())
                            if ignore_strikes_below <= empty_option_info.strike <= ignore_strikes_above:
                                option_data.add_data(empty_option_info)

                        # Time to remove this task
                        remove_idx = idx
                        break

                if remove_idx >= 0:
                    task_queue.pop(remove_idx)
                    # Break out of this loop so new tasks (or a task) can be added
                    break

                await asyncio.sleep(0.1)