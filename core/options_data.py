from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from core.utils import BarSize, bar_size_to_str, str_to_bar_size
from core.common import OptionInfo

_logger = logging.getLogger(__name__)


class OptionDataException(Exception):
    pass


class OptionData:
    """
    Holds data for a set of options contracts pertaining to a particular underlying security (e.g. SPY).
    Price, delta, theta, etc. merely represent an instant in time -- actual values for option are
    subject to rapid change.

    Each entry winds up being a row in a pandas dataframe
    """

    column_names = [
        "date",
        "full_name",
        "right",
        "strike",
        "expiration",
        "price",
        "volume",
        "open_interest",
        "implied_volatility",
        "delta",
        "theta",
        "gamma",
        "vega",
    ]

    def __init__(self, symbol: str, timestamp: datetime):
        self._symbol = symbol
        self._timestamp = timestamp
        self._options_df: pd.DataFrame = pd.DataFrame(columns=OptionData.column_names)
        self._current_index: int = 0
        self._underlying_price: float = 0.0

    def add_data(self, option_info: OptionInfo):
        """Adds information about a single options contract"""
        info_dict = option_info.to_dict()
        self._underlying_price = option_info.underlying_price

        data_row = []
        for col_name in OptionData.column_names:
            if col_name == "date":
                data_row.append(self._timestamp)
            elif col_name == "right":
                data_row.append("C" if info_dict["is_call"] else "P")
            else:
                data_row.append(info_dict[col_name])
        self._options_df.loc[self._current_index] = data_row
        self._current_index += 1

    def get_dataframe(self) -> DataFrame:
        """Returns pandas dataframe"""
        return self._options_df

    def sort(self, column: str, ascending: bool = True):
        """Sorts the rows of options data"""
        self._options_df.sort_values(by=column, inplace=True, ascending=ascending)

    @property
    def underlying_price(self) -> float:
        """Returns price of underlying security"""
        return self._underlying_price
