from ibapi.contract import Contract, ContractDetails
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString
from typing import Optional, Dict, List, Tuple, Union, Set
from enum import Enum, auto
from datetime import datetime, timedelta

from core.utils import wait_for_condition, get_datetime, get_datetime_as_str, BarSize

class IBDriverException(Exception):
    pass


class DataRequest:
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

    def __init__(self, ticker: str):
        super().__init__()
        self.ticker: str = ticker
        # Bar data, oldest to newest
        self.bar_data: List[BarData] = []
        # One timestamp for each entry in bar_data
        self.timestamps: List[datetime] = []
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

        def _replace_bar_data(existing: BarData, new: BarData):
            existing.low = new.low
            existing.high = new.high
            existing.open = new.open
            existing.volume = new.volume

        # Go backwards through the list, insert received bar after first encountered existing bar
        # that it's newer than.
        insert_idx = 0
        for idx in range(len(self.bar_data) - 1, -1, -1):
            compare_bar = self.bar_data[idx]
            compare_dt = self.timestamps[idx]
            if compare_dt < bar_dt:
                # Want to insert AFTER this index
                insert_idx = idx + 1
                break
            if compare_dt == bar_dt:
                # Simply replace data
                _replace_bar_data(compare_bar, bar_data)
                return

        self.bar_data.insert(insert_idx, bar_data)
        self.timestamps.insert(insert_idx, bar_dt)

    def get_bar_data_as_dicts(self):
        ret_bars = [{"date": bar.date, "open": bar.open, "close": bar.close, "low": bar.low, "high": bar.high,
                     "volume": float(bar.volume)} for bar in self.bar_data]
        return ret_bars


class ContractDetailsRequest(DataRequest):

    def __init__(self):
        super().__init__()
        self.details_list: List[ContractDetails] = []

class OptionChainRequest(DataRequest):

    def __init__(self, ticker: str):
        super().__init__()
        self.ticker = ticker
        self.expirations: Set = set()
        self.strikes: Set = set()


