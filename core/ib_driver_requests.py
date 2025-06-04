from ibapi.contract import Contract, ContractDetails
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString
from typing import Optional, Dict, List, Tuple, Union, Set
from enum import Enum, auto
from datetime import datetime, timedelta

from core.common import SecurityDescriptor, HistoricalData
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

    def __init__(self):
        super().__init__()
        self.details_list: List[ContractDetails] = []


class OptionChainInfoRequest(DataRequest):
    """For tracking an options chain info request and capturing results returned so far."""

    def __init__(self, ticker: str):
        super().__init__()
        self.ticker = ticker
        self.expirations: Set = set()
        self.strikes: Set = set()
