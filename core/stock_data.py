from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from core.utils import BarSize

_logger = logging.getLogger(__name__)

class StockData:
    """
    A wrapper for a pandas Dataframe, which holds bars of price data.

    Columns: date (datetime), open, close, low, high, volume
    Indexed by: human-readable date-time
    """

    def __init__(self, symbol: str, bar_size: BarSize):
        self._symbol = symbol
        self._bar_size = bar_size
        self._price_and_vol_df: pd.DataFrame = pd.DataFrame(columns=["date", "open", "close", "low", "high", "volume"])

    def add_data(self, bar: Dict[str, Any], date: datetime):
        """
        Adds a bar of data
        :param bar: dict of open, close, low, high, volume data
        :param date: datetime at which bar begins
        :return:
        """
        date_str = self._get_readable_date(date)
        df = self._price_and_vol_df
        df.loc[date_str] = [date, float(bar["open"]), float(bar["close"]), float(bar["low"]), float(bar["high"]), float(bar["volume"])]

    def get_data_frame(self):
        """Returns pandas Dataframe"""
        return self._price_and_vol_df

    def _get_readable_date(self, dt: datetime):
        """Converts a datetime into a human-readable string"""
        if self._bar_size in [BarSize.ONE_DAY, BarSize.ONE_WEEK]:
            return f"{dt.month:02}/{dt.day:02}/{dt.year:04}"
        else:
            return f"{dt.month:02}/{dt.day:02} {dt.hour:02}:{dt.minute:02}"
