import asyncio
from datetime import datetime, timezone

async def wait_for_condition(condition, timeout: float, check_interval: float=0.1):
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
        await asyncio.sleep(check_interval)
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
        dt = datetime(year, month, day, hour, minute, second)
    except:
        raise TypeError(f"General failure to convert IB date {ib_date}")
    return dt

def get_datetime_as_str(dt: datetime) -> str:
    """
    Given a datetimte, return it as an IB-style datetime string, e.g. "20250523 09:30:00 US/Eastern"
    """
    return f"{dt.year:04}{dt.month:02}{dt.day:02} {dt.hour:02}:{dt.minute:02}:{dt.second:02} US/Eastern"
