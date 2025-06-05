from typing import Dict, List, Tuple, Optional, Set, Any
from datetime import datetime
from enum import Enum, auto
from ibapi.common import BarData

LOCAL_TIMEZONE = "America/New_York"
MARKETS_TIMEZONE = "America/New_York"


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

    def __init__(self):
        self.bar_data: List[BarData] = []
        self.timestamps: List[datetime] = []

    def add_data(self, bar: BarData, bar_dt: datetime):
        """
        Adds a new bar of data to that received so far. We don't necessarily expect bars to arrive
        in sequential order, so we must take timestamps into account to keep them in order. Also,
        a bar's data might replace an existing bar, i.e. if the bar is actively trading right now
        and we're receiving updates on it.

        :param bar: --
        :param bar_dt: datetime for bar
        """

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
                _replace_bar_data(compare_bar, bar)
                return

        self.bar_data.insert(insert_idx, bar)
        self.timestamps.insert(insert_idx, bar_dt)

    def is_empty(self):
        """Returns True if no data present"""
        return len(self.bar_data) == 0

    def get_zipped_lists(self) -> List[Tuple[Dict, datetime]]:
        """Returns list of (bar data dict, timestamp for bar)"""
        bar_data_dicts = self.get_bar_data_as_dicts()
        return list(zip(bar_data_dicts, self.timestamps))

    def get_bar_data_as_dicts(self):
        """Gets bar data as list of Dicts"""
        ret_bars = [
            {
                "date": bar.date,
                "open": bar.open,
                "close": bar.close,
                "low": bar.low,
                "high": bar.high,
                "volume": float(bar.volume),
            }
            for bar in self.bar_data
        ]
        return ret_bars

class OptionChainInfo:
    """Receives very basic information about an option chain."""

    def __init__(self):
        self.exchange: str = ""
        self.underlying: str = ""
        self.multiplier: int = 100
        self.expirations: Set[str] = set()
        self.strikes: Set[float] = set()


class OptionInfo:
    """Info about a particular option contract."""

    def __init__(self):
        self.full_name: str = ""
        self.is_call: bool = True
        self.strike: float = 0.0
        self.expiration: str = ""
        self.price: float = 0.0
        self.underlying_price: float = 0.0
        self.delta: float = 0.0
        self.theta: float = 0.0
        self.gamma: float = 0.0
        self.vega: float = 0.0
        self.open_interest: int = 0
        self.volume: int = 0
        self.implied_volatility: float = 0.0

        self._live: bool = False
        self._greeks_defined: bool = False
        self._interest_defined: bool = False
        self._volume_defined: bool = False

    def set_open_interest(self, amount: int, for_call: bool = False):
        if self.is_call == for_call:
            self.open_interest = amount
            self._interest_defined = True

    def set_volume(self, amount: int, for_call: bool = False):
        if self.is_call == for_call:
            self.volume = amount
            self._volume_defined = True

    def set_live(self, live: bool = True):
        self._live = live

    def set_greeks_defined(self):
        self._greeks_defined = True

    def is_defined(self) -> bool:
        return self._greeks_defined and self._interest_defined and (self._volume_defined or not self._live)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "full_name": self.full_name,
            "is_call": self.is_call,
            "strike": self.strike,
            "expiration": self.expiration,
            "price": self.price,
            "underlying_price": self.underlying_price,
            "delta": self.delta,
            "theta": self.theta,
            "gamma": self.gamma,
            "vega": self.vega,
            "open_interest": self.open_interest,
            "volume": self.volume,
            "implied_volatility": self.implied_volatility,
        }