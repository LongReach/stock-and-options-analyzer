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
from core.cache import TTLCache
from core.utils import (
    BarSize,
    bar_size_to_str,
    str_to_bar_size,
    bar_size_to_time,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
    get_full_symbol_name,
)
from core.options_data import OptionData, OptionDataException
from core.ib_driver import IBDriver

_logger = logging.getLogger(__name__)


class OptionDataManager:
    """
    Wraps IBDriver, providing functions that simplify collecting options data or placing options orders.
    """

    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self._ib_driver: Optional[IBDriver] = None
        self._opt_info_cache: TTLCache[OptionInfo] = TTLCache(maxsize=100, ttl=120.0)

    def add_driver(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        if not ib_driver.is_connected():
            self._ib_driver.connect()

    async def get_expirations(self, ticker: str, min_days_away: int, max_days_away: int) -> List[str]:
        """
        Gets a set of available expiration dates
        :param ticker: ticker for underlying, e.g. AAPL
        :param min_days_away: minimum number of days until expiration (relative to now)
        :param max_days_away: maximum number of days until expiration
        :return: list of IB-style datetimes
        """
        self._logger.info(
            f"Getting expirations for {ticker}, min_days_away={min_days_away}, max_days_away={max_days_away}"
        )
        # This is the CD for the stock, not any option
        options_chain_info, error_str = await self._ib_driver.get_options_chain_info(ticker, primary_exchange=None)
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

    async def get_strikes(
        self,
        ticker: str,
        expiration: str,
        right: str,
        num_below: Optional[int] = None,
        num_above: Optional[int] = None,
        min_strike: Optional[float] = None,
        max_strike: Optional[float] = None,
    ) -> Tuple[List[float], int]:
        """
        Gets all strikes for a particular stock at a particular expiration date.
        :param ticker: underlying stock/ETF
        :param expiration: IB-style date
        :param right: "C" or "P"
        :param num_below: number of strikes below at-the-money price, or None if all strikes
        :param num_above: number of strikes above at-the-money price, or None if all strikes
        :param min_strike: if given, lowest allowed strike price to return
        :param max_strike: if given, highest allowed strike price to return
        :return: (list of strikes, index of which strike is closest to ATM)
        """
        info_str = f"Getting strikes for {ticker}, expiration={expiration}, right={right}"
        if num_below:
            info_str += f", num_below={num_below}"
        if num_above:
            info_str += f", num_above={num_above}"
        self._logger.info(info_str)

        option_info_list, error_str = await self._ib_driver.get_option_info(
            ticker,
            is_call=(right == "C"),
            expiration=expiration,
            primary_exchange=None,
        )
        if error_str:
            raise OptionDataException(error_str)

        strikes = [opt_info.strike for opt_info in option_info_list]
        strikes.sort()

        underlying_price = await self._get_underlying_price(ticker)

        # Will reference closest to being at the money
        closest_strike_idx: int = 0
        best_dist = 1000000
        for idx, strike in enumerate(strikes):
            if math.fabs(strike - underlying_price) < best_dist:
                best_dist = math.fabs(strike - underlying_price)
                closest_strike_idx = idx

        # Constrain results to how many strikes to go below/above
        if num_below:
            lowest_idx = closest_strike_idx - num_below
            if lowest_idx < 0:
                lowest_idx = 0
        else:
            lowest_idx = 0
        if num_above:
            highest_idx = closest_strike_idx + num_above
            if highest_idx > len(strikes):
                highest_idx = len(strikes)
        else:
            highest_idx = len(strikes)
        strikes = strikes[lowest_idx:highest_idx]
        closest_strike_idx = closest_strike_idx - lowest_idx

        # Now, we take into account lowest and highest desired strike price
        lowest_idx = 0
        if min_strike:
            for idx, strike in enumerate(strikes):
                if strike >= min_strike:
                    lowest_idx = idx
                    break

        highest_idx = len(strikes)
        if max_strike:
            # Note the different logic of this loop
            for idx in range(len(strikes) - 1, -1, -1):
                strike = strikes[idx]
                if strike > max_strike:
                    highest_idx = idx

        return strikes[lowest_idx:highest_idx], closest_strike_idx - lowest_idx

    async def get_option_chain(
        self,
        ticker: str,
        expiration: str,
        right: str,
        strike: Optional[Union[float, List[float]]] = None,
        min_delta: float = 0.08,
        max_delta: float = 0.7,
    ) -> OptionData:
        """
        Returns a whole options chain, within an OptionData object.
        :param ticker: symbol of underlying
        :param expiration: expiration date, IB style
        :param right: "C" for call, "P" for put
        :param strike: strike price (for a single option contract), list of strike prices, or None
        :param min_delta: don't want data for options contracts with delta below this value
        :param max_delta: don't want data for options contracts with delta above this value
        :return: OptionData, holding all retrieved data
        :raise OptionDataException: if some problem encountered
        """
        self._logger.info(
            f"Getting option chain for {ticker}, expiration={expiration}, right={right}, min_delta={min_delta}, max_delta={max_delta}"
        )

        strike_list = [None]
        if strike:
            if isinstance(strike, list):
                strike_list = strike
            elif isinstance(strike, float):
                strike_list = [strike]
            else:
                raise OptionDataException(f"Wrong data for strike data, is type {type(strike)}")

        option_info_list: List[OptionInfo] = []

        for single_strike in strike_list:
            temp_list, error_str = await self._ib_driver.get_option_info(
                ticker,
                is_call=(right == "C"),
                expiration=expiration,
                strike=single_strike,
            )
            if error_str:
                raise OptionDataException(error_str)
            option_info_list.extend(temp_list)

        option_data = OptionData(ticker, current_datetime())
        if len(option_info_list) == 0:
            return option_data

        underlying_price = await self._get_underlying_price(ticker)

        # Create a list in which OptionInfo objects with strike price closest to underlying are at the top of the list
        sortable_oi_list = [(oi, math.fabs(oi.strike - underlying_price)) for oi in option_info_list]
        sortable_oi_list.sort(key=lambda x: x[1])

        new_list = [item[0] for item in sortable_oi_list]
        await self._batch_collect_options_data(
            new_list,
            option_data,
            underlying_price,
            right=right,
            max_delta=max_delta,
            min_delta=min_delta,
        )

        option_data.sort("strike")
        return option_data

    async def get_option_info(
        self,
        ticker: str,
        expiration: str,
        right: str,
        strike: float,
    ) -> Optional[OptionInfo]:
        """
        Get OptionInfo for a particular option contract, including current trade price and the Greeks. Will pull data
        from cache if present, unless cache data isn't fresh enough.

        :param ticker: ticker of underlying
        :param expiration: expiration date, IB style
        :param right: "C" for call, "P" for put
        :param strike: strike price (for a single option contract)
        :return: OptionInfo or None
        """

        # Check the cache first
        full_symbol = get_full_symbol_name(
            ticker, is_option=True, is_call=(right == "C"), expiration=expiration, strike=strike
        )
        cached_oi = self._opt_info_cache.get(full_symbol)
        if cached_oi is not None:
            return cached_oi

        option_info, error_msg = await self._ib_driver.get_option_info_single(
            ticker=ticker, expiration=expiration, strike=strike, is_call=(right == "C")
        )
        if error_msg is not None:
            raise OptionDataException(f"Could not get option info: {error_msg}")

        option_info, error_msg = await self._ib_driver.get_greeks(option_info)
        if error_msg is not None:
            raise OptionDataException(f"Could not get Greeks: {error_msg}")

        if option_info is not None:
            self._opt_info_cache.put(full_symbol, option_info)

        return option_info

    async def _get_underlying_price(self, ticker: str):
        """Get the latest trading price (within a minute) for ticker"""
        ret_tup, error_str = await self._ib_driver.get_most_recent_data(ticker, BarSize.ONE_MINUTE)
        if not ret_tup or error_str:
            raise OptionDataException(f"Couldn't get underlying price, error is: {error_str}")
        underlying_price = ret_tup[0]["close"]
        if underlying_price <= 0.0:
            raise OptionDataException("Couldn't get underlying price")
        return underlying_price

    async def _batch_collect_options_data(
        self,
        option_info_list: List[OptionInfo],
        option_data: OptionData,
        underlying_price: float,
        right: str = "C",
        max_delta: float = 0.8,
        min_delta: float = 0.07,
    ):
        """
        Grabs data for a bunch of different options contracts in parallel, rather than one-at-a-time (which is
        very slow). Effectively, multiple requests go out to IB at the same time. asyncio.Tasks are used to wait
        for results.

        :param option_info_list: one OptionInfo for each option
        :param option_data: this will receive the results
        :param underlying_price: price of underlying security
        :param right: "C" for call, "P" for put
        :param min_delta: don't want data for options contracts with delta below this value
        :param max_delta: don't want data for options contracts with delta above this value
        """

        # This limits the number of requests active with IB at once
        MAX_TO_RETRIEVE_AT_ONCE = 20
        MAX_ERRORS = 5

        # Keep tracks of which entries in contract_details_list we've made tasks for
        current_idx: int = 0
        # A queue for Greeks-retrieval tasks that are still running
        task_queue: List[asyncio.Task] = []
        ignore_strikes_below: float = 0
        ignore_strikes_above: float = 1000000000.0
        error_count = 0

        # Note: deltas are negative for puts
        delta_multiplier = 1.0 if right == "C" else -1.0

        # The idea: once we get info about an options contract, including its delta, and we find out that
        # the delta is higher or lower than desired, then we don't need to fetch data for contracts above/below
        # that option's strike.

        def _set_ignorable_strikes(_option_info: OptionInfo):
            """Helper function to set values of ignore_strikes_below, ignore_strikes_above"""
            nonlocal ignore_strikes_above, ignore_strikes_below
            _strike = _option_info.strike
            _delta = _option_info.delta * delta_multiplier
            if right == "C":
                if _strike > underlying_price and _delta < min_delta:
                    ignore_strikes_above = _strike
                if _strike <= underlying_price and _delta > max_delta:
                    ignore_strikes_below = _strike
            else:
                if _strike < underlying_price and _delta < min_delta:
                    ignore_strikes_below = _strike
                if _strike >= underlying_price and _delta > max_delta:
                    ignore_strikes_above = _strike

        def _test_for_ignorable(_option_info: OptionInfo):
            """Returns True if we don't need info for a particular options contract due to strike being too high or low"""
            return not (ignore_strikes_below <= _option_info.strike <= ignore_strikes_above)

        # Loop until we've processed all OptionInfos and task queue is empty
        while current_idx < len(option_info_list) or len(task_queue) > 0:

            # Create new tasks as needed, keeping the queue of active tasks as full as possible
            while len(task_queue) < MAX_TO_RETRIEVE_AT_ONCE and current_idx < len(option_info_list):
                option_info = option_info_list[current_idx]
                if not _test_for_ignorable(option_info):
                    task_name = option_info.full_name
                    self._logger.debug(f"Creating retrieval task for {task_name}")
                    task = asyncio.create_task(self._ib_driver.get_greeks(option_info), name=task_name)
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
                                # Debugging
                                # print(f"option {option_info.strike}/{option_info.delta}")
                                if ignore_strikes_below <= option_info.strike <= ignore_strikes_above:
                                    if min_delta <= option_info.delta * delta_multiplier <= max_delta:
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
