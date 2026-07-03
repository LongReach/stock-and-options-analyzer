from typing import Optional
from datetime import date
import logging
import pandas as pd
import yfinance as yf

_logger = logging.getLogger(__name__)


def get_upcoming_earnings_date(ticker: str) -> Optional[date]:
    """Returns the next upcoming earnings date for the ticker, or None if unavailable."""
    try:
        t = yf.Ticker(ticker)
        calendar = t.get_calendar()
        dates = calendar.get("Earnings Date", [])
        return dates[0] if dates else None
    except Exception as e:
        _logger.warning(f"Could not fetch upcoming earnings date for {ticker}: {e}")
        return None


def get_past_earnings_dates(ticker: str, limit: int = 8) -> pd.DataFrame:
    """Returns past earnings dates with EPS estimate, reported EPS, and surprise %.

    Returns a DataFrame indexed by Earnings Date with columns:
        EPS Estimate, Reported EPS, Surprise(%)
    Rows with no reported EPS (i.e. future/estimated dates) are excluded.
    """
    try:
        t = yf.Ticker(ticker)
        df = t.get_earnings_dates(limit=limit)
        if df is None or df.empty:
            return pd.DataFrame()
        return df[df["Reported EPS"].notna()]
    except Exception as e:
        _logger.warning(f"Could not fetch past earnings dates for {ticker}: {e}")
        return pd.DataFrame()
