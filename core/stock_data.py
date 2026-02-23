from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from core.utils import BarSize, bar_size_to_str, str_to_bar_size, non_naive_datetime
from core.common import RequestedInfoType

_logger = logging.getLogger(__name__)


class StockDataException(Exception):
    pass


class StockData:
    """
    A wrapper for a pandas Dataframe, which holds bars of price data.

    Columns: date (datetime), open, close, low, high, volume
    Indexed by: human-readable date-time
    """

    def __init__(
            self,
            symbol: str,
            bar_size: BarSize,
            info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ):
        self._symbol = symbol
        self._bar_size = bar_size
        self._info_type = info_type
        self.clear()

    def add_data(self, bar: Dict[str, Any], date: datetime):
        """
        Adds a bar of data to StockData object. Once added, this object can be saved to disk.

        :param bar: dict of open, close, low, high, volume data
        :param date: datetime at which bar begins
        """
        # Make sure the date is in the right timezone
        date = non_naive_datetime(date)
        date_str = self._get_readable_date(date)
        df = self._price_and_vol_df
        df.loc[date_str] = [
            date,
            float(bar["open"]),
            float(bar["close"]),
            float(bar["low"]),
            float(bar["high"]),
            float(bar["volume"]),
        ]

    def finalize_data(self):
        """Call when all data has been added. Puts data into proper order."""
        self._price_and_vol_df.sort_values(by="date", inplace=True)

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
                self._symbol, self._bar_size, self._info_type = (
                    self._infer_characteristics_from_file_name(filename)
                )
            except:
                _logger.warning(
                    f"Couldn't infer symbol and bar size from filename {filename}"
                )
                pass
        else:
            filename = self._get_file_name()

        path = f"./data/{filename}"
        try:
            _logger.info(f"Attempting to load pickle {path}")
            self._price_and_vol_df = read_pickle(path)
        except:
            _logger.warning(f"Couldn't load file {filename}")
            return False

        # Go through date, make sure timezone is right for date
        for idx in range(len(self._price_and_vol_df)):
            # TODO: 0 is index of "date" column, make a constant for it
            self._price_and_vol_df.iloc[idx, 0] = non_naive_datetime(
                self._price_and_vol_df.iloc[idx]["date"]
            )

        return True

    def save(self, filename: Optional[str] = None) -> bool:
        """
        Saves data to disk.
        :param filename: if not given, one will be chosen from symbol and bar size
        """
        filename = self._get_file_name() if filename is None else filename
        path = f"./data/{filename}"
        try:
            _logger.info(f"Attempting to save pickle {path}")
            self._price_and_vol_df.to_pickle(path)
        except:
            _logger.warning(f"Couldn't save file {filename}")
            return False
        return True

    def clear(self):
        """Make new, empty dataframe"""
        self._price_and_vol_df: pd.DataFrame = pd.DataFrame(
            columns=["date", "open", "close", "low", "high", "volume"]
        )

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def bar_size(self) -> BarSize:
        return self._bar_size

    @property
    def info_type(self) -> RequestedInfoType:
        return self._info_type

    @staticmethod
    def get_info_type_str(info_type: RequestedInfoType) -> str:
        """Convert a RequestedInfoType to a simple string"""
        _map: Dict[RequestedInfoType, str] = {
            RequestedInfoType.TRADES: "tr",
            RequestedInfoType.IMPLIED_VOLATILITY: "iv",
            RequestedInfoType.HISTORICAL_VOLATILITY: "hv",
            RequestedInfoType.ADJUSTED_LAST: "al",
        }
        result = _map.get(info_type)
        if not result:
            raise StockDataException(f"Couldn't convert {info_type.name} to str")
        return result

    @staticmethod
    def get_info_type(info_type_str: str) -> RequestedInfoType:
        """Convert a simple string to a RequestedInfoType"""
        _map: Dict[str, RequestedInfoType] = {
            "tr": RequestedInfoType.TRADES,
            "iv": RequestedInfoType.IMPLIED_VOLATILITY,
            "hv": RequestedInfoType.HISTORICAL_VOLATILITY,
            "al": RequestedInfoType.ADJUSTED_LAST,
        }
        result = _map.get(info_type_str)
        if not result:
            raise StockDataException(
                f"Couldn't convert string {info_type_str} to RequestedInfoType"
            )
        return result

    def _get_readable_date(self, dt: datetime):
        """Converts a datetime into a human-readable string"""
        if self._bar_size in [BarSize.ONE_DAY, BarSize.ONE_WEEK]:
            return f"{dt.month:02}/{dt.day:02}/{dt.year:04}"
        else:
            return f"{dt.month:02}/{dt.day:02} {dt.hour:02}:{dt.minute:02}"

    def _get_file_name(self) -> str:
        """Assigns a filename based on symbol, bar size, and info type, returns in a string"""
        return f"{self._symbol}-{bar_size_to_str(self._bar_size)}-{StockData.get_info_type_str(self._info_type)}.zip"

    def _infer_characteristics_from_file_name(
            self, filename: str
    ) -> Tuple[str, BarSize, RequestedInfoType]:
        """Attempts to infer symbol, bar size, and info type from a filename"""
        try:
            # Get part of filename before extension
            parts = filename.split(".")
            characteristics_str = parts[0].split("-")
            symbol_str = characteristics_str[0]
            bar_size = str_to_bar_size(characteristics_str[1])
            info_type = StockData.get_info_type(characteristics_str[2])
            return symbol_str, bar_size, info_type
        except:
            raise StockDataException(
                f"Couldn't infer symbol/bar size/info y from {filename}"
            )
