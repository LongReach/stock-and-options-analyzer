from typing import Dict, List, Tuple, Optional
from datetime import datetime
from enum import Enum, auto


class CoreException(Exception):
    """Base class for custom exceptions in this module."""

    pass


class BarSize(Enum):
    """Corresponds to width of a candle on a stock chart"""

    ONE_MINUTE = auto()
    FIVE_MINUTES = auto()
    ONE_HOUR = auto()
    FOUR_HOURS = auto()
    ONE_DAY = auto()
    ONE_WEEK = auto()


class RequestedInfoType(Enum):
    """Specifies what kind of historical data to get"""

    TRADES = "TRADES"
    IMPLIED_VOLATILITY = "OPTION_IMPLIED_VOLATILITY"
    HISTORICAL_VOLATILITY = "HISTORICAL_VOLATILITY"
    ADJUSTED_LAST = "ADJUSTED_LAST"


class SecurityDescriptor:
    """
    Describes a security, either a stock or an option. E.g.:
    "SPY"
    "SPY-C-20250627-600.0"
    """

    def __init__(self, symbol_full: str):
        self.symbol_full: str = symbol_full
        parts = symbol_full.split("-")
        self.is_opt: bool = False
        self.right: Optional[str] = None
        self.expiration: Optional[str] = None
        self.strike: Optional[float] = None
        if len(parts) >= 1:
            self.ticker = parts[0]
        if len(parts) > 1:
            self.is_opt = True
            self.right = parts[1]
            self.expiration = parts[2]
            self.strike = float(parts[3])

    def is_call(self):
        return self.right == "C"


class HistoricalData:
    """Holds historical data returned by IBDriver"""

    def __init__(self, bars: List[Dict], datetimes: List[datetime]):
        self.bar_data_list = bars
        self.datetime_list = datetimes

    def is_empty(self):
        return len(self.bar_data_list) == 0

    def get_zipped_lists(self) -> List[Tuple[Dict, datetime]]:
        return list(zip(self.bar_data_list, self.datetime_list))
