from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from core.utils import BarSize, bar_size_to_str, str_to_bar_size

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

    def load(self, filename: Optional[str] = None) -> bool:
        """
        Loads data from disk.
        :param filename: if not given, one will be chosen from symbol and bar size
        :return: True if data was loaded from disk
        """
        if filename:
            try:
                self._symbol, self._bar_size = self._infer_symbol_and_bar_size_from_file_name(filename)
            except:
                pass
        else:
            filename = self._get_file_name()

        path = f"./data/{filename}"
        try:
            self._price_and_vol_df = read_pickle(path)
        except:
            _logger.warning(f"Couldn't load file {filename}")
            return False
        return True

    def save(self, filename: Optional[str] = None):
        """
        Saves data to disk.
        :param filename: if not given, one will be chosen from symbol and bar size
        """
        filename = self._get_file_name() if filename is None else filename
        path = f"./data/{filename}"
        self._price_and_vol_df.to_pickle(path)

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def bar_size(self) -> BarSize:
        return self._bar_size

    def _get_readable_date(self, dt: datetime):
        """Converts a datetime into a human-readable string"""
        if self._bar_size in [BarSize.ONE_DAY, BarSize.ONE_WEEK]:
            return f"{dt.month:02}/{dt.day:02}/{dt.year:04}"
        else:
            return f"{dt.month:02}/{dt.day:02} {dt.hour:02}:{dt.minute:02}"

    def _get_file_name(self) -> str:
        return f"{self._symbol}-{bar_size_to_str(self._bar_size)}.zip"

    def _infer_symbol_and_bar_size_from_file_name(self, filename: str) -> Tuple[str, BarSize]:
        parts = filename.split(".")
        sym_and_bar_size = parts[0].split("-")
        symbol_str = sym_and_bar_size[0]
        bar_size = str_to_bar_size(sym_and_bar_size[1])
        return symbol_str, bar_size
