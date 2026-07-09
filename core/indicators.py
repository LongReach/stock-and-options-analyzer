from typing import List, Optional
import pandas as pd
from enum import Enum, auto
from typing import Tuple


class IndicatorException(Exception):
    """For exceptions in this module"""

    pass


class MA_TYPE(Enum):
    """Corresponds to width of a candle on a stock chart"""

    SMA = auto()  # simple moving average
    EMA = auto()  # exponential moving average


def sma_indicator(df: pd.DataFrame, column: str = "close", period: int = 8) -> pd.DataFrame:
    """
    Given stock price data, as pandas dataframe, return simple moving average of last p entries.

    :param df: pandas dataframe of stock price bars, with columns being "open", "close", "low", and "high"
    :param column: which column of df to take simple moving average of
    :param period: period of moving average, the value of p
    :return: pandas dataframe containing simple moving average. Single column is "value".
    :raises IndicatorException: if not enough data in df to fit value of period
    """
    if len(df) < period:
        raise IndicatorException(f"Not enough data: need {period} rows, got {len(df)}")
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")

    values = df[column].rolling(window=period).mean()
    return pd.DataFrame({"value": values})


def ema_indicator(df: pd.DataFrame, column: str = "close", period: int = 8) -> pd.DataFrame:
    """
    Given stock price data, as pandas dataframe, return exponential moving average of last p entries.

    :param df: pandas dataframe of stock price bars, with columns being "open", "close", "low", and "high"
    :param column: which column of df to take exponential moving average of
    :param period: period of moving average, the value of p
    :return: pandas dataframe containing exponential moving average. Single column is "value".
    :raises IndicatorException: if not enough data in df to fit value of period
    """
    if len(df) < period:
        raise IndicatorException(f"Not enough data: need {period} rows, got {len(df)}")
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")

    values = df[column].ewm(span=period, adjust=False).mean()
    return pd.DataFrame({"value": values})


def stochastic_indicator(
    df: pd.DataFrame,
    k_length: int = 14,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
) -> pd.DataFrame:
    """
    Computes the stochastic oscillator.

    Raw %K measures where the close sits within the high-low range over the last k_length bars.
    Smoothed %K is an SMA of raw %K over k_smoothing bars.
    %D is an SMA of smoothed %K over d_smoothing bars.

    :param df: pandas dataframe of stock price bars with "high", "low", and "close" columns
    :param k_length: lookback period for highest high / lowest low
    :param k_smoothing: SMA period applied to raw %K to produce smoothed %K
    :param d_smoothing: SMA period applied to smoothed %K to produce %D
    :return: pandas dataframe with columns "k" (smoothed %K) and "d" (%D)
    :raises IndicatorException: if not enough data or required columns are missing
    """
    required = k_length + k_smoothing + d_smoothing - 2
    if len(df) < required:
        raise IndicatorException(f"Not enough data: need {required} rows, got {len(df)}")
    for col in ("high", "low", "close"):
        if col not in df.columns:
            raise IndicatorException(f"Column '{col}' not found in dataframe")

    lowest_low = df["low"].rolling(window=k_length).min()
    highest_high = df["high"].rolling(window=k_length).max()
    raw_k = 100.0 * (df["close"] - lowest_low) / (highest_high - lowest_low)

    smoothed_k = raw_k.rolling(window=k_smoothing).mean()
    d = smoothed_k.rolling(window=d_smoothing).mean()

    return pd.DataFrame({"k": smoothed_k, "d": d})


def macd_indicator(
    df: pd.DataFrame,
    column: str = "close",
    fast_length: int = 12,
    slow_length: int = 26,
    signal_length: int = 9,
    oscillator_ma_type: MA_TYPE = MA_TYPE.EMA,
    signal_ma_type: MA_TYPE = MA_TYPE.EMA,
) -> pd.DataFrame:
    """
    Computes the MACD (Moving Average Convergence Divergence) indicator.

    MACD line    = fast MA(source) - slow MA(source)
    Signal line  = MA of MACD line over signal_length
    Histogram    = MACD line - Signal line

    :param df: pandas dataframe of stock price bars
    :param column: source column to compute MAs on
    :param fast_length: period of the fast moving average
    :param slow_length: period of the slow moving average (must be > fast_length)
    :param signal_length: smoothing period for the signal line
    :param oscillator_ma_type: MA type (SMA or EMA) used for the fast and slow MAs
    :param signal_ma_type: MA type (SMA or EMA) used for the signal line
    :return: pandas dataframe with columns "macd", "signal", and "histogram"
    :raises IndicatorException: if fast_length >= slow_length, column is missing, or not enough data
    """
    if fast_length >= slow_length:
        raise IndicatorException(f"fast_length ({fast_length}) must be less than slow_length ({slow_length})")
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")
    if len(df) < slow_length:
        raise IndicatorException(f"Not enough data: need {slow_length} rows, got {len(df)}")

    fast_ma = _apply_ma(df[column], fast_length, oscillator_ma_type)
    slow_ma = _apply_ma(df[column], slow_length, oscillator_ma_type)
    macd_line = fast_ma - slow_ma
    signal_line = _apply_ma(macd_line, signal_length, signal_ma_type)
    histogram = macd_line - signal_line

    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def rsi_indicator(
    df: pd.DataFrame,
    column: str = "close",
    rsi_length: int = 14,
    ma_type: MA_TYPE = MA_TYPE.EMA,
    ma_length: int = 14,
) -> pd.DataFrame:
    """
    Computes RSI (Relative Strength Index) and a smoothed signal line.

    RSI is calculated using Wilder's smoothing (RMA) on average gains and losses over rsi_length bars.
    The signal line is an MA of the RSI values, using ma_type and ma_length.

    :param df: pandas dataframe of stock price bars
    :param column: source column
    :param rsi_length: lookback period for computing RSI
    :param ma_type: MA type (SMA or EMA) used to smooth RSI into the signal line
    :param ma_length: smoothing period for the signal line
    :return: pandas dataframe with columns "rsi" and "signal"
    :raises IndicatorException: if column is missing or not enough data
    """
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")
    if len(df) < rsi_length:
        raise IndicatorException(f"Not enough data: need {rsi_length} rows, got {len(df)}")

    delta = df[column].diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    # Wilder's smoothing: EMA with alpha = 1 / rsi_length
    avg_gain = gains.ewm(alpha=1.0 / rsi_length, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1.0 / rsi_length, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi_values = 100.0 - (100.0 / (1.0 + rs))

    signal_values = _apply_ma(rsi_values, ma_length, ma_type)

    return pd.DataFrame({"rsi": rsi_values, "signal": signal_values})


def crossover(df: pd.DataFrame, column: str = "close", value: float = 0, direction: float = 1.0) -> bool:
    """
    Returns True if a crossover occurs within given data series. A crossover happens when data goes from below a
    certain value to above it, or vice versa.

    :param df: pandas dataframe
    :param column: column of interest within that dataframe
    :param value: the value that must be crossed over
    :param direction: the direction in which the crossover must occur. Provide a value of zero or above if crossover
        must be from below to above, or a negative value if crossover must be from above to below.
    :return: True if a crossover matching the given parameters occurs at some point within the specified
        dataframe column.
    """
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")
    if len(df) < 2:
        return False

    series = df[column]
    prev = series.iloc[-2]
    curr = series.iloc[-1]

    if direction >= 0:
        return prev < value <= curr
    else:
        return prev > value >= curr


def min(df: pd.DataFrame, column: str = "close") -> Tuple[float, int]:
    """
    Finds minimum value within particular dataframe column

    :param df: pandas dataframe
    :param column: column of interest within that dataframe
    :return: (minimum value, index at which it occurs)
    """
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")
    if len(df) == 0:
        raise IndicatorException("Dataframe is empty")

    idx = df[column].idxmin()
    return df[column][idx], idx


def max(df: pd.DataFrame, column: str = "close") -> Tuple[float, int]:
    """
    Finds maximum value within particular dataframe column

    :param df: pandas dataframe
    :param column: column of interest within that dataframe
    :return: (maximum value, index at which it occurs)
    """
    if column not in df.columns:
        raise IndicatorException(f"Column '{column}' not found in dataframe")
    if len(df) == 0:
        raise IndicatorException("Dataframe is empty")

    idx = df[column].idxmax()
    return df[column][idx], idx


def _apply_ma(series: pd.Series, period: int, ma_type: MA_TYPE) -> pd.Series:
    """Helper: apply SMA or EMA to a series."""
    if ma_type == MA_TYPE.SMA:
        return series.rolling(window=period).mean()
    else:
        return series.ewm(span=period, adjust=False).mean()
