import asyncio
from contextlib import asynccontextmanager
from typing import Union, Optional, List
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from enum import Enum, auto
import traceback
import math
import pandas_market_calendars as mcal

from core.common import BarSize, CoreException, LOCAL_TIMEZONE, MARKETS_TIMEZONE


def bar_size_to_str(bar_size: BarSize) -> str:
    """Convert BarSize to a string description"""
    conversion_map = {
        BarSize.ONE_MINUTE: "1m",
        BarSize.FIVE_MINUTES: "5m",
        BarSize.ONE_HOUR: "1h",
        BarSize.FOUR_HOURS: "4h",
        BarSize.ONE_DAY: "1d",
        BarSize.ONE_WEEK: "1w",
    }
    try:
        return conversion_map[bar_size]
    except:
        raise CoreException(f"Couldn't convert {bar_size.name} to string")


def str_to_bar_size(bar_size_str: str) -> BarSize:
    """Given a string description, e.g. '1d', return a BarSize"""
    conversion_map = {
        "1m": BarSize.ONE_MINUTE,
        "5m": BarSize.FIVE_MINUTES,
        "1h": BarSize.ONE_HOUR,
        "4h": BarSize.FOUR_HOURS,
        "1d": BarSize.ONE_DAY,
        "1w": BarSize.ONE_WEEK,
    }
    try:
        return conversion_map[bar_size_str]
    except:
        raise CoreException(f"Couldn't convert {bar_size_str} to BarSize")


def bar_size_to_time(bar_size: BarSize) -> timedelta:
    """Given a BarSize, return a timedelta object"""
    conversion_map = {
        BarSize.ONE_MINUTE: timedelta(minutes=1),
        BarSize.FIVE_MINUTES: timedelta(minutes=5),
        BarSize.ONE_HOUR: timedelta(hours=1),
        BarSize.FOUR_HOURS: timedelta(hours=4),
        BarSize.ONE_DAY: timedelta(days=1),
        BarSize.ONE_WEEK: timedelta(weeks=1),
    }
    try:
        return conversion_map[bar_size]
    except:
        raise CoreException(f"Couldn't convert {bar_size.name} to timedelta")


async def wait_for_condition(condition, timeout: float, check_interval: float = 0.1):
    """
    Waits for a condition to be true with a timeout.

    :param condition: a function that returns a boolean value
    :param timeout: the maximum time to wait in seconds
    :param check_interval: how often to check the condition in seconds. Defaults to 0.1.
    :return: True if condition was met, False if timeout
    """
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        if condition():
            return True
        try:
            await asyncio.sleep(check_interval)
        except asyncio.CancelledError as ex:
            # Happens if user control-Cs out of program
            raise ex from None
    return False


def get_datetime(ib_date: str) -> datetime:
    """
    Given an IB-style datetime string, e.g. "20250523 09:30:00 US/Eastern", convert it to a datetime
    """
    try:
        ib_parts = ib_date.split(" ")
        year = int(ib_parts[0][0:4])
        month = int(ib_parts[0][4:6])
        day = int(ib_parts[0][6:8])
    except:
        raise TypeError(f"Couldn't convert date part of IB date {ib_date}")

    hour = 9
    minute = 30
    second = 0
    if len(ib_parts) > 1:
        try:
            time_parts = ib_parts[1].split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2])
        except:
            raise TypeError(f"Couldn't convert time part of IB date {ib_date}")

    if year < 1000 or year > 3000:
        raise TypeError(f"Bad year value of {year} in IB date {ib_date}")
    if month < 1 or month > 12:
        raise TypeError(f"Bad month value of {month} in IB date {ib_date}")
    if day < 1 or day > 31:
        raise TypeError(f"Bad day value of {day} in IB date {ib_date}")
    if hour > 24:
        raise TypeError(f"Bad hour value of {hour} in IB date {ib_date}")
    if minute > 60:
        raise TypeError(f"Bad minute value of {minute} in IB date {ib_date}")
    if second > 60:
        raise TypeError(f"Bad second value of {second} in IB date {ib_date}")

    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(MARKETS_TIMEZONE))
    except:
        raise TypeError(f"General failure to convert IB date {ib_date}")
    return dt


def get_datetime_as_str(dt: Union[datetime, str], date_only: bool = False) -> str:
    """
    Given a datetimte, return it as an IB-style datetime string, e.g. "20250523 09:30:00 US/Eastern"
    """
    if isinstance(dt, str):
        dt = get_datetime(dt)
    if date_only:
        return f"{dt.year:04}{dt.month:02}{dt.day:02}"
    else:
        return f"{dt.year:04}{dt.month:02}{dt.day:02} {dt.hour:02}:{dt.minute:02}:{dt.second:02} US/Eastern"


_nyse = mcal.get_calendar("NYSE")


def is_trading_hours() -> bool:
    """Returns True if it's currently within NYSE trading hours"""
    current_dt = datetime.now(ZoneInfo(MARKETS_TIMEZONE))
    today_str = current_dt.strftime("%Y-%m-%d")
    schedule = _nyse.schedule(start_date=today_str, end_date=today_str)
    if schedule.empty:
        return False
    market_open = schedule.iloc[0]["market_open"].to_pydatetime()
    market_close = schedule.iloc[0]["market_close"].to_pydatetime()
    return market_open <= current_dt <= market_close


def current_datetime():
    """Returns current datetime, but in Eastern standard time"""
    return datetime.now(ZoneInfo(MARKETS_TIMEZONE))


def non_naive_datetime(dt: datetime) -> datetime:
    """Given a naive datetime (no timezone), set it to market time"""
    return dt.replace(tzinfo=ZoneInfo(MARKETS_TIMEZONE))


@asynccontextmanager
async def lock_with_timeout(lock: asyncio.Lock, timeout: float):
    """
    Helper function for waiting for an asyncio.Lock, but with a timeout.

    Use like:

    async with lock_with_timeout(lock, 5) as acquired:
        if not acquired:
            return
        # do something

    :param lock: --
    :param timeout: timeout in seconds
    """
    acquired = False
    try:
        await asyncio.wait_for(lock.acquire(), timeout)
        acquired = True
    except asyncio.TimeoutError:
        pass

    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def get_exception_traceback(ex: Exception):
    tb_obj = ex.__traceback__
    # Format the traceback object into a list of strings and join them
    tb_str = "".join(traceback.format_tb(tb_obj))
    return tb_str


def get_full_symbol_name(
    ticker: str, is_option: bool, is_call: bool = True, expiration: Optional[str] = None, strike: Optional[float] = None
):
    """Return a full IB-style symbol name, e.g. "SPY" or "SPY-C-20250627-600.0" (if option)"""
    if not is_option:
        return ticker
    if expiration is None and strike is None:
        return f"{ticker}-{'C' if is_call else 'P'}"
    if strike is None:
        return f"{ticker}-{'C' if is_call else 'P'}-{expiration}"

    return f"{ticker}-{'C' if is_call else 'P'}-{expiration}-{strike}"


def calculate_expected_move(price: float, iv: float, dte: int, standard_devs: int = 1):
    """
    Calculates expected move for a stock/ETF. This is a standard formula, kept here for easy finding.

    :param price: current price
    :param iv: current implied volatility
    :param dte: days to expiration
    :param standard_devs: standard deviations
    :return: expected move
    """
    return price * iv * math.sqrt(float(dte) / 365.0) * float(standard_devs)


def get_best_strike(strike_list: List[float], desired_strike: float):
    """
    Given a list of available strikes, return the one closest to desired strike price.
    :param strike_list: list of valid strikes
    :param desired_strike: the desired strike, often some price of the stock
    :return: strike that's best fit
    """
    best_dist = math.fabs(strike_list[0] - desired_strike)
    best_idx = 0
    for idx, strike in enumerate(strike_list):
        dist = math.fabs(strike - desired_strike)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return strike_list[best_idx]
