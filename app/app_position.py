from typing import Dict, List, Tuple, Optional, Set, Any, Self
from datetime import datetime, date
from enum import Enum, auto, IntEnum
from threading import Lock
from logging import getLogger
from zoneinfo import ZoneInfo

from core.common import SecurityDescriptor, OrderInfo
from core.base_driver import BaseDriver


class DumbException(Exception):
    """Base class for custom exceptions in this module."""

    pass


class DumbSize(Enum):
    """Corresponds to width of a candle on a stock chart"""

    ONE_MINUTE = auto()
    TWO_MINUTES = auto()
    FIVE_MINUTES = auto()
    FIFTEEN_MINUTES = auto()
    ONE_HOUR = auto()
    FOUR_HOURS = auto()
    ONE_DAY = auto()
    ONE_WEEK = auto()
    ONE_MONTH = auto()


class AppPosition:

    driver: Optional[BaseDriver] = None

    def __init__(self, symbol: Optional[str] = None, symbols: Optional[List[str]] = None):
        self.security_descriptor: Optional[SecurityDescriptor] = None

        self.desired_quantity: int = 0
        self.held_quantity: int = 0
        self.out_quantity: int = 0
        self.average_entry_price: float = 0.0
        self.average_exit_price: float = 0.0

        self.long_stop_order: Optional[OrderInfo] = None
        self.long_limit_order: Optional[OrderInfo] = None
        self.short_stop_order: Optional[OrderInfo] = None
        self.short_limit_order: Optional[OrderInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.security_descriptor.to_string(),
            "desired_quantity": self.desired_quantity,
            "held_quantity": self.held_quantity,
            "out_quantity": self.out_quantity,
            "average_entry_price": self.average_entry_price,
            "average_exit_price": self.average_exit_price,
        }
