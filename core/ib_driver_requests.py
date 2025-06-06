from ibapi.contract import Contract, ContractDetails
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString
from typing import Optional, Dict, List, Tuple, Union, Set
from enum import Enum, auto
from datetime import datetime, timedelta

from core.common import SecurityDescriptor, HistoricalData, OptionChainInfo, OptionInfo
from core.utils import wait_for_condition, get_datetime, get_datetime_as_str, BarSize


class IBDriverException(Exception):
    pass


class DataRequest:
    """
    Base class for all "request" classes below. Implements common functionality.
    """

    def __init__(self):
        # Error triggered by request, if any
        self.last_error_code: int = -1
        self.last_error_string: str = ""
        # False while data fetch still in progress
        self.data_fetch_complete: bool = True

    def has_error(self):
        """Returns True if request triggered an error"""
        return self.last_error_code != -1


class BarDataRequest(DataRequest):
    """For tracking an in-progress bar data request and capturing data returned so far"""

    def __init__(self, ticker_desc: SecurityDescriptor):
        super().__init__()
        self.ticker_desc: SecurityDescriptor = ticker_desc
        self.historical_data = HistoricalData()
        # Discard any bar data older than this, if set
        self.earliest_permitted_dt: Optional[datetime] = None

    def add_or_update_bar(self, bar_data: BarData, allow_update: bool = False):
        """
        Adds a new bar of data to that received so far. We don't necessarily expect bars to arrive
        in sequential order, so we must take timestamps into account to keep them in order. Also,
        a bar's data might replace an existing bar, i.e. if the bar is actively trading right now
        and we're receiving updates on it.

        :param bar_data: --
        :param allow_update: TBD
        """
        bar_dt = get_datetime(bar_data.date)
        self.historical_data.add_data(bar_data, bar_dt)


class ContractDetailsRequest(DataRequest):
    """For tracking a contract details request and capturing results returned so far."""

    # TODO: refactor this thing for the returning of multiple contract details

    def __init__(self):
        super().__init__()
        self._exchange_map: Dict[str, List[ContractDetails]] = {}

    def add_contract_details(self, details: ContractDetails):
        exchange = details.contract.exchange
        details_list = self._exchange_map.get(exchange)
        if not details_list:
            details_list = self._exchange_map[exchange] = []
        details_list.append(details)

    def get_best_list(self) -> List[ContractDetails]:
        if len(self._exchange_map) == 0:
            return []
        if self._exchange_map.get("SMART"):
            return self._exchange_map.get("SMART")
        # Just pick some list
        arbitrary_item = next(iter(self._exchange_map.items()))
        return arbitrary_item[1]


class OptionChainInfoRequest(DataRequest):
    """For tracking an options chain info request and capturing results returned so far."""

    def __init__(self, ticker: str):
        super().__init__()
        self.ticker = ticker
        self.option_chain_info_list: List[OptionChainInfo] = []
        self._exchange_map: Dict[str, OptionChainInfo] = {}

    def add_option_chain_info(self, info: OptionChainInfo):
        """There will be one of these for each exchange"""
        self.option_chain_info_list.append(info)
        self._exchange_map[info.exchange] = info

    def get_best_option_chain_info(self) -> Optional[OptionChainInfo]:
        """
        Return OptionChainInfo or None. Prefer the one from SMART exchange, if any has
        arrived for that exchange.
        """
        if len(self.option_chain_info_list) == 0:
            return None
        if self._exchange_map.get("SMART"):
            return self._exchange_map.get("SMART")
        return self.option_chain_info_list[-1]


class OptionRequest(DataRequest):
    """For tracking an options info request and capturing results returned so far."""

    def __init__(self):
        super().__init__()
        self.option_info = OptionInfo()
