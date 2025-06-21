from enum import IntEnum
from typing import Union


class TradeColumn(IntEnum):
    """Fields in the "spreadsheet", one for each individual options trade from open to close"""

    POSITION_NUMBER = 0  # ties this trade to a specific position
    DATE_OPENED = 1  # date at which trade opened
    RIGHT = 2  # "P" or "C"
    EXPIRATION = 3  # when option expires
    STRIKE = 4  # strike price
    NUM_CONTRACTS = 5
    OPENING_PRICE = 6  # price of contract
    DATE_CLOSED = 7  # date at which trade closed, or empty
    LAST_PRICE = 8  # price when trade closed, or last checkec price
    IV = 9
    DELTA = 10
    THETA = 11
    GAMMA = 12
    VEGA = 13


class PositionColumn(IntEnum):
    """Fields in the "spreadsheet", one for each individual position from open to close"""

    POSITION_NUMBER = 0  # ties this position to a set of trades
    STRATEGY = 1  # IC, CS, DS, etc.
    TICKER = 2
    DATE_OPENED = 3
    DATE_CLOSED = 4


def column_enum_to_str(enum: Union[TradeColumn, PositionColumn]):
    """Given a field like POSITION_NUMBER, output like 'position number'"""
    return enum.name.lower().replace("_", " ")
