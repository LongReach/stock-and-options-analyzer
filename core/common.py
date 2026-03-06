from typing import Dict, List, Tuple, Optional, Set, Any, Self
from datetime import datetime
from enum import Enum, auto
from threading import Lock
from logging import getLogger
from ibapi.common import BarData

"""
Classes in this file represent data returned by or give to IBDriver. However, they are generic enough
that they might be repurposed for communications with some other broker.
"""

LOCAL_TIMEZONE = "America/New_York"
MARKETS_TIMEZONE = "America/New_York"


class CoreException(Exception):
    """Base class for custom exceptions in this module."""

    pass


class BarSize(Enum):
    """Corresponds to width of a candle on a stock chart"""

    ONE_MINUTE = auto()
    TWO_MINUTES = auto()
    FIVE_MINUTES = auto()
    FIFTEEN_MINUTES = auto()
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


class OrderAction(Enum):
    BUY = auto()
    SELL = auto()


class OrderType(Enum):
    """The standard types of orders offered by most brokerages"""

    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()


class OrderStatus(Enum):
    """
    For tracking the state of an order sent to IB. Not all orders are filled right away --
    e.g. a limit sell order will only trigger when a security hits a certain price.
    """

    NONE = auto()
    SUBMITTED = auto()
    CANCELLED = auto()
    FILLED = auto()


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

    def is_option(self):
        """Returns True if this is a descriptor for an option contract"""
        return self.right is not None

    def is_call(self):
        """Returns True if for a call contract"""
        return self.right == "C"

    def to_string(self) -> str:
        """Returns string representation, e.g. 'SPY-C-20250627-600.0'"""
        if self.is_opt:
            return f"{self.ticker}-{self.right}-{self.expiration}-{self.strike:.2f}"
        else:
            return f"{self.ticker}"

    @classmethod
    def create(
        cls,
        ticker: str,
        right: Optional[str] = None,
        expiration: Optional[str] = None,
        strike: Optional[float] = None,
    ):
        """
        Create SecurityDescriptor object.

        :param ticker: ticker for stock/ETF or underlying, e.g. 'SPY'
        :param right: 'C' if call, 'P' if put, or None if not for option contract
        :param expiration: exp. date of option, or None
        :param strike: strike price of option, or None
        :return: new descriptor object
        """
        descriptor = SecurityDescriptor(ticker)
        if right is not None:
            descriptor.right = right
        if expiration is not None:
            descriptor.expiration = expiration
        if strike is not None:
            descriptor.strike = strike
        descriptor.symbol_full = descriptor.to_string()
        return descriptor


class HistoricalData:
    """
    Holds multiple bars of historical data returned by IBDriver.

    The Lock is important because data might be continuously streaming back from IB, containing
    updates to the bar in progress.
    """

    next_id = 1

    def __init__(self):
        self.bar_data: List[BarData] = []
        self.timestamps: List[datetime] = []
        self.lock = Lock()

        self._id = HistoricalData.next_id
        HistoricalData.next_id += 1

        self._logger = getLogger(__file__)

    def get_id(self) -> int:
        return self._id

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

        with self.lock:
            # Go backwards through the list, insert received bar after first encountered existing bar
            # that it's newer than.
            insert_idx = 0
            for idx in range(len(self.bar_data) - 1, -1, -1):
                compare_bar = self.bar_data[idx]
                compare_dt = self.timestamps[idx]
                if compare_dt == bar_dt:
                    # Simply replace data
                    _replace_bar_data(compare_bar, bar)
                    return
                if compare_dt < bar_dt:
                    # Want to insert AFTER this index
                    insert_idx = idx + 1
                    break

            self.bar_data.insert(insert_idx, bar)
            self.timestamps.insert(insert_idx, bar_dt)

    def is_empty(self):
        """Returns True if no data present"""
        with self.lock:
            return len(self.bar_data) == 0

    def get_zipped_lists(self) -> List[Tuple[Dict, datetime]]:
        """Returns list of (bar data dict, timestamp for bar)"""
        with self.lock:
            bar_data_dicts = self.get_bar_data_as_dicts()
            return list(zip(bar_data_dicts, self.timestamps))

    def get_bar_data_as_dicts(self):
        """Gets bar data as list of Dicts"""
        with self.lock:
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

    def get_current_bar(self) -> Optional[Tuple[Dict, datetime]]:
        with self.lock:
            if len(self.bar_data) == 0:
                return None

            bar = self.bar_data[-1]
            dt = self.timestamps[-1]
            ret_bar = {
                "date": bar.date,
                "open": bar.open,
                "close": bar.close,
                "low": bar.low,
                "high": bar.high,
                "volume": float(bar.volume),
            }
            return ret_bar, dt


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
        # E.g. SPY-C-20250627-600.0
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
        """
        Sets open interest.
        :param amount: amount to use
        :param for_call: True if amount is meant for a call option (amount will be ignored if wrong option type)
        """
        if self.is_call == for_call:
            self.open_interest = amount
            self._interest_defined = True

    def set_volume(self, amount: int, for_call: bool = False):
        """
        Sets volume
        :param amount: amount to use
        :param for_call: True if amount is meant for a call option (amount will be ignored if wrong option type)
        """
        if self.is_call == for_call:
            self.volume = amount
            self._volume_defined = True

    def set_live(self, live: bool = True):
        """Sets whether the info in this object will reflect live market data"""
        self._live = live

    def set_greeks_defined(self):
        """IBDriver calls this func. when information for Greeks has been set."""
        self._greeks_defined = True

    def is_defined(self) -> bool:
        """Returns True when this object has been filled out with all desired info."""
        # TODO: why does volume often not get defined in live mode? Why does refer happen when not live?
        # print(f"is_defined(), greeks_defined={self._greeks_defined}, interest_defined={self._interest_defined}, volume_defined={self._volume_defined}, live={self._live}")
        return self._greeks_defined and (
            self._interest_defined and self._live or not self._live
        )

    def to_dict(self) -> Dict[str, Any]:
        """Returns data as a dict"""
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

    def get_debug_info(self) -> str:
        return f"Greeks defined: {self._greeks_defined}, interest defined: {self._interest_defined}, volume defined: {self._volume_defined}, is live: {self._live}"

    @staticmethod
    def make_empty_option_info(full_name: str):
        # E.g. SPY-C-20250627-600.0
        option_info = OptionInfo()
        parts = full_name.split("-")
        option_info.full_name = full_name
        option_info.is_call = parts[1] == "C"
        option_info.expiration = parts[2]
        option_info.strike = float(parts[3])
        return option_info


class OrderInfo:
    """Info about a particular order in IB"""

    def __init__(self):
        self.security_descriptor: Optional[SecurityDescriptor] = None
        # Will reference an OrderInfo that represents a parent order. For example, this order will have a parent if it's
        # a half-out order attached to a stop order that causes a trade to be entered.
        self.parent_order: Optional[Self] = None
        self.order_type: OrderType = OrderType.MARKET
        self.order_status: OrderStatus = OrderStatus.NONE
        self.shares_filled: int = 0
        self.shares_remaining: int = 0
        # TODO: need better name
        self.avg_fill_price: Optional[float] = None

    def get_info_str(self) -> str:
        """Returns string representation"""
        parent_order_str = ""
        if self.parent_order:
            parent_order_str = f"parent_order={OrderType(self.parent_order.order_type).name}/{OrderStatus(self.parent_order.order_status).name}, "
        return f"Order info: symbol={self.security_descriptor.to_string()}, {parent_order_str}order_type={OrderType(self.order_type).name}, order_status={OrderStatus(self.order_status).name}, shares_filled={self.shares_filled}, shares_remaining={self.shares_remaining}, price={self.avg_fill_price}"


class PositionDescriptor:
    """Info about a particular position held in the account."""

    def __init__(self, descriptor: SecurityDescriptor):
        self.security_descriptor: SecurityDescriptor = descriptor
        self.quantity: int = 0
        self.price: float = 0.0
        self.short_position = False

    def to_string(self):
        """Returns string representation"""
        return f"Position for {self.security_descriptor.to_string()}, quantity={self.quantity}, price={self.price}, short={self.short_position}"


class PositionsInfo:
    """Info about all positions currently held in the account"""

    def __init__(self):
        self.position_map: Dict[str, PositionDescriptor] = {}

    def set_position(
        self,
        descriptor: SecurityDescriptor,
        quantity: int,
        price: float,
        short_position: bool,
    ):
        """
        Sets or updates a position with data received from broker

        :param descriptor: describes security, whether stock/ETF or options
        :param quantity: number of shares/contracts
        :param price: purchase or sale price
        :param short_position: True if short position
        """
        position_descriptor = self.position_map.get(descriptor.symbol_full)
        if position_descriptor is None:
            position_descriptor = PositionDescriptor(descriptor)
            self.position_map[descriptor.symbol_full] = position_descriptor
        position_descriptor.quantity = quantity
        position_descriptor.price = price
        position_descriptor.short_position = short_position

    def get_position(
        self, security_descriptor: SecurityDescriptor
    ) -> Optional[PositionDescriptor]:
        """Returns a PositionDescriptor, or None"""
        return self.position_map.get(security_descriptor.symbol_full)

    def get_positions(self) -> List[PositionDescriptor]:
        """Return all position descriptors"""
        return [desc for symbol, desc in self.position_map.items()]
