import asyncio
from dataclasses import dataclass, field
from typing import Optional

from core.common import HistoricalData


class SchwabDriverException(Exception):
    """Raised when SchwabDriver can't fulfill a request."""


@dataclass
class LiveStream:
    """
    Tracks an in-progress live (streaming) data subscription.

    The caller of get_historical_data(live_data=True) hangs onto the HistoricalData object; the
    background task keeps updating it with new one-minute candles until the stream is cancelled.
    """

    symbol: str
    historical_data: HistoricalData
    task: Optional[asyncio.Task] = field(default=None)
