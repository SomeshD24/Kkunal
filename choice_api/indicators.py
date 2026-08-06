"""
Technical Indicators Module for choice_api (Kkunal).

Provides vectorized, mathematical implementations of standard technical analysis indicators
using pandas and numpy. No external C-dependencies required.

Indicators:
    Trend:       SMA, EMA, WMA, DEMA, TEMA, MACD, ADX, Supertrend, Parabolic SAR, Ichimoku Cloud
    Momentum:    RSI, Stochastic Oscillator, CCI, Williams %R
    Volatility:  Bollinger Bands, ATR, Donchian Channel
    Volume:      VWAP, OBV
    Utilities:   Crossover, Crossunder, Heikin Ashi, Pivot Points
"""

from typing import Optional, Union, Dict, Any, List
import numpy as np
import pandas as pd


# ============================================================
# Validation Helpers
# ============================================================

def _validate_period(period: int, name: str = "period") -> None:
    """Validates that a period parameter is a positive integer."""
    if not isinstance(period, int) or period < 1:
        raise ValueError(f"{name} must be a positive integer, got {period}")


def _validate_df(df: pd.DataFrame) -> None:
    """Validates that the input is a non-empty DataFrame."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"Expected a pandas DataFrame, got {type(df).__name__}")


def _get_col(df: pd.DataFrame, col_name: str) -> pd.Series:
    """Helper to locate column in a case-insensitive manner."""
    if col_name in df.columns:
        return df[col_name]
    for c in df.columns:
        if str(c).lower() == col_name.lower():
            return df[c]
    raise KeyError(f"Column '{col_name}' not found in DataFrame. Available columns: {list(df.columns)}")


# ============================================================
# Signal Utilities
# ============================================================

def crossover(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """
    Detects where series_a crosses ABOVE series_b.

    Returns:
        Boolean Series — True at each bar where a crossover occurs.

    Example:
        >>> buy_signals = crossover(df['EMA_9'], df['EMA_21'])
    """
    a = series_a.values
    b = series_b.values if isinstance(series_b, pd.Series) else np.full(len(a), series_b)
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    cross = (a > b) & (prev_a <= prev_b)
    cross[0] = False  # First bar is undefined
    return pd.Series(cross, index=series_a.index, name="Crossover")


def crossunder(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """
    Detects where series_a crosses BELOW series_b.

    Returns:
        Boolean Series — True at each bar where a crossunder occurs.

    Example:
        >>> sell_signals = crossunder(df['EMA_9'], df['EMA_21'])
    """
    a = series_a.values
    b = series_b.values if isinstance(series_b, pd.Series) else np.full(len(a), series_b)
    prev_a = np.roll(a, 1)
    prev_b = np.roll(b, 1)
    cross = (a < b) & (prev_a >= prev_b)
    cross[0] = False
    return pd.Series(cross, index=series_a.index, name="Crossunder")


# ============================================================
# Moving Averages & Trend
# ============================================================

def sma(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Simple Moving Average (SMA).
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    return s.rolling(window=period, min_periods=1).mean().rename(f"SMA_{period}")


def ema(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Exponential Moving Average (EMA).
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    return s.ewm(span=period, adjust=False).mean().rename(f"EMA_{period}")


def dema(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Double Exponential Moving Average (DEMA).
    DEMA = 2 * EMA(period) - EMA(EMA(period))
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    ema1 = s.ewm(span=period, adjust=False).mean()
    ema2 = ema1.ewm(span=period, adjust=False).mean()
    return (2 * ema1 - ema2).rename(f"DEMA_{period}")


def tema(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Triple Exponential Moving Average (TEMA).
    TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    ema1 = s.ewm(span=period, adjust=False).mean()
    ema2 = ema1.ewm(span=period, adjust=False).mean()
    ema3 = ema2.ewm(span=period, adjust=False).mean()
    return (3 * ema1 - 3 * ema2 + ema3).rename(f"TEMA_{period}")


def wma(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Weighted Moving Average (WMA).
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    weights = np.arange(1, period + 1)

    def _calc_wma(window):
        return np.dot(window, weights) / weights.sum()

    res = s.rolling(window=period, min_periods=period).apply(_calc_wma, raw=True)
    return res.rename(f"WMA_{period}")


def macd(
    df: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    column: str = "Close"
) -> pd.DataFrame:
    """
    Moving Average Convergence Divergence (MACD).
    Returns DataFrame with columns: MACD, MACD_Signal, MACD_Hist
    """
    _validate_df(df)
    _validate_period(fast_period, "fast_period")
    _validate_period(slow_period, "slow_period")
    _validate_period(signal_period, "signal_period")
    s = _get_col(df, column)
    fast_ema = s.ewm(span=fast_period, adjust=False).mean()
    slow_ema = s.ewm(span=slow_period, adjust=False).mean()

    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "MACD": macd_line,
        "MACD_Signal": signal_line,
        "MACD_Hist": histogram
    }, index=df.index)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Average Directional Index (ADX).
    Returns DataFrame with columns: ADX, Plus_DI, Minus_DI
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    up_move = high.diff()
    down_move = -1 * low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    tr_smooth = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_dm_smooth = pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    minus_dm_smooth = pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    plus_di = 100 * (plus_dm_smooth / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm_smooth / tr_smooth.replace(0, np.nan))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_series = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    return pd.DataFrame({
        "ADX": adx_series,
        "Plus_DI": plus_di,
        "Minus_DI": minus_di
    }, index=df.index)


def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0
) -> pd.DataFrame:
    """
    Supertrend Indicator.
    Returns DataFrame with columns: Supertrend, Supertrend_Direction
        +1 = Bullish, -1 = Bearish, 0 = Warmup (insufficient data)
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    atr_val = atr(df, period=period)
    hl2 = (high + low) / 2.0

    basic_ub = hl2 + (multiplier * atr_val)
    basic_lb = hl2 - (multiplier * atr_val)

    n = len(df)
    final_ub = np.zeros(n)
    final_lb = np.zeros(n)
    st = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)

    close_vals = close.values
    b_ub_vals = basic_ub.values
    b_lb_vals = basic_lb.values

    # Find first valid index (where ATR is not NaN)
    first_valid = 0
    for i in range(n):
        if not np.isnan(b_ub_vals[i]):
            first_valid = i
            break

    # Initialize at first valid index
    if first_valid < n:
        final_ub[first_valid] = b_ub_vals[first_valid]
        final_lb[first_valid] = b_lb_vals[first_valid]
        direction[first_valid] = 1
        st[first_valid] = final_lb[first_valid]

    for i in range(first_valid + 1, n):
        if np.isnan(b_ub_vals[i]):
            continue

        # Upper band
        if b_ub_vals[i] < final_ub[i-1] or close_vals[i-1] > final_ub[i-1]:
            final_ub[i] = b_ub_vals[i]
        else:
            final_ub[i] = final_ub[i-1]

        # Lower band
        if b_lb_vals[i] > final_lb[i-1] or close_vals[i-1] < final_lb[i-1]:
            final_lb[i] = b_lb_vals[i]
        else:
            final_lb[i] = final_lb[i-1]

        # Trend selection
        if direction[i-1] == 1:
            if close_vals[i] < final_lb[i]:
                direction[i] = -1
                st[i] = final_ub[i]
            else:
                direction[i] = 1
                st[i] = final_lb[i]
        else:
            if close_vals[i] > final_ub[i]:
                direction[i] = 1
                st[i] = final_lb[i]
            else:
                direction[i] = -1
                st[i] = final_ub[i]

    # Mark warmup period as NaN
    for i in range(first_valid):
        st[i] = np.nan
        direction[i] = 0

    return pd.DataFrame({
        "Supertrend": st,
        "Supertrend_Direction": direction
    }, index=df.index)


def parabolic_sar(
    df: pd.DataFrame,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.20
) -> pd.DataFrame:
    """
    Parabolic SAR (Stop and Reverse).
    Returns DataFrame with columns: PSAR, PSAR_Trend (+1 = Bullish, -1 = Bearish)
    """
    _validate_df(df)
    high = _get_col(df, "High").values
    low = _get_col(df, "Low").values
    close = _get_col(df, "Close").values
    n = len(df)

    psar = np.zeros(n)
    trend = np.ones(n, dtype=int)
    af = np.zeros(n)
    ep = np.zeros(n)

    # Initialize
    psar[0] = low[0]
    af[0] = af_start
    ep[0] = high[0]
    trend[0] = 1

    for i in range(1, n):
        prev_psar = psar[i-1]
        prev_af = af[i-1]
        prev_ep = ep[i-1]
        prev_trend = trend[i-1]

        if prev_trend == 1:  # Bullish
            psar[i] = prev_psar + prev_af * (prev_ep - prev_psar)
            # SAR cannot be above the prior two lows
            psar[i] = min(psar[i], low[i-1])
            if i >= 2:
                psar[i] = min(psar[i], low[i-2])

            if low[i] < psar[i]:  # Reversal to bearish
                trend[i] = -1
                psar[i] = prev_ep
                ep[i] = low[i]
                af[i] = af_start
            else:
                trend[i] = 1
                if high[i] > prev_ep:
                    ep[i] = high[i]
                    af[i] = min(prev_af + af_step, af_max)
                else:
                    ep[i] = prev_ep
                    af[i] = prev_af
        else:  # Bearish
            psar[i] = prev_psar + prev_af * (prev_ep - prev_psar)
            # SAR cannot be below the prior two highs
            psar[i] = max(psar[i], high[i-1])
            if i >= 2:
                psar[i] = max(psar[i], high[i-2])

            if high[i] > psar[i]:  # Reversal to bullish
                trend[i] = 1
                psar[i] = prev_ep
                ep[i] = high[i]
                af[i] = af_start
            else:
                trend[i] = -1
                if low[i] < prev_ep:
                    ep[i] = low[i]
                    af[i] = min(prev_af + af_step, af_max)
                else:
                    ep[i] = prev_ep
                    af[i] = prev_af

    return pd.DataFrame({
        "PSAR": psar,
        "PSAR_Trend": trend
    }, index=df.index)


def ichimoku(
    df: pd.DataFrame,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26
) -> pd.DataFrame:
    """
    Ichimoku Cloud (Ichimoku Kinko Hyo).
    Returns DataFrame with columns:
        Tenkan_Sen, Kijun_Sen, Senkou_Span_A, Senkou_Span_B, Chikou_Span
    """
    _validate_df(df)
    _validate_period(tenkan_period, "tenkan_period")
    _validate_period(kijun_period, "kijun_period")
    _validate_period(senkou_b_period, "senkou_b_period")
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    # Tenkan-sen (Conversion Line)
    tenkan = (high.rolling(window=tenkan_period, min_periods=1).max()
              + low.rolling(window=tenkan_period, min_periods=1).min()) / 2.0

    # Kijun-sen (Base Line)
    kijun = (high.rolling(window=kijun_period, min_periods=1).max()
             + low.rolling(window=kijun_period, min_periods=1).min()) / 2.0

    # Senkou Span A (Leading Span A) — displaced forward
    senkou_a = ((tenkan + kijun) / 2.0).shift(displacement)

    # Senkou Span B (Leading Span B) — displaced forward
    senkou_b = ((high.rolling(window=senkou_b_period, min_periods=1).max()
                 + low.rolling(window=senkou_b_period, min_periods=1).min()) / 2.0).shift(displacement)

    # Chikou Span (Lagging Span) — displaced backward
    chikou = close.shift(-displacement)

    return pd.DataFrame({
        "Tenkan_Sen": tenkan,
        "Kijun_Sen": kijun,
        "Senkou_Span_A": senkou_a,
        "Senkou_Span_B": senkou_b,
        "Chikou_Span": chikou
    }, index=df.index)


# ============================================================
# Oscillators & Momentum
# ============================================================

def rsi(df: pd.DataFrame, period: int = 14, column: str = "Close") -> pd.Series:
    """
    Relative Strength Index (RSI) using Wilder's Exponential Smoothing.
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    delta = s.diff()

    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)

    # Wilder's smoothing (alpha = 1 / period)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_series = rsi_series.fillna(100)  # Handles zero loss edge case
    rsi_series.iloc[:period-1] = np.nan
    return rsi_series.rename(f"RSI_{period}")


def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
    smooth_k: int = 3
) -> pd.DataFrame:
    """
    Stochastic Oscillator (%K, %D).
    Returns DataFrame with columns: Stoch_K, Stoch_D
    """
    _validate_df(df)
    _validate_period(k_period, "k_period")
    _validate_period(d_period, "d_period")
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    lowest_low = low.rolling(window=k_period, min_periods=1).min()
    highest_high = high.rolling(window=k_period, min_periods=1).max()

    denom = (highest_high - lowest_low).replace(0, np.nan)
    raw_k = 100 * (close - lowest_low) / denom

    stoch_k = raw_k.rolling(window=smooth_k, min_periods=1).mean()
    stoch_d = stoch_k.rolling(window=d_period, min_periods=1).mean()

    return pd.DataFrame({
        "Stoch_K": stoch_k,
        "Stoch_D": stoch_d
    }, index=df.index)


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Commodity Channel Index (CCI).
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(window=period, min_periods=1).mean()

    def _mean_dev(window):
        return np.abs(window - window.mean()).mean()

    mad = tp.rolling(window=period, min_periods=period).apply(_mean_dev, raw=True)
    cci_series = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
    return cci_series.rename(f"CCI_{period}")


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Williams %R.
    Oscillates between -100 and 0 (-100 = oversold, 0 = overbought).
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    highest_high = high.rolling(window=period, min_periods=1).max()
    lowest_low = low.rolling(window=period, min_periods=1).min()

    denom = (highest_high - lowest_low).replace(0, np.nan)
    wr = -100 * (highest_high - close) / denom
    return wr.rename(f"Williams_R_{period}")


# ============================================================
# Volatility
# ============================================================

def bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
    column: str = "Close"
) -> pd.DataFrame:
    """
    Bollinger Bands.
    Returns DataFrame with columns: BB_Upper, BB_Middle, BB_Lower, BB_Bandwidth, BB_PercentB
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    middle = s.rolling(window=period, min_periods=1).mean()
    std = s.rolling(window=period, min_periods=1).std().fillna(0)

    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    bandwidth = (upper - lower) / middle.replace(0, np.nan)
    band_range = (upper - lower).replace(0, np.nan)
    percent_b = (s - lower) / band_range

    return pd.DataFrame({
        "BB_Upper": upper,
        "BB_Middle": middle,
        "BB_Lower": lower,
        "BB_Bandwidth": bandwidth,
        "BB_PercentB": percent_b
    }, index=df.index)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (ATR) using Wilder's Smoothing.
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return atr_series.rename(f"ATR_{period}")


def donchian_channel(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Donchian Channel.
    Returns DataFrame with columns: Donchian_Upper, Donchian_Middle, Donchian_Lower
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")

    upper = high.rolling(window=period, min_periods=1).max()
    lower = low.rolling(window=period, min_periods=1).min()
    middle = (upper + lower) / 2.0

    return pd.DataFrame({
        "Donchian_Upper": upper,
        "Donchian_Middle": middle,
        "Donchian_Lower": lower
    }, index=df.index)


# ============================================================
# Volume-Based
# ============================================================

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume Weighted Average Price (VWAP).
    If a 'Time' column is present and is datetime, groups by Date; otherwise calculates cumulative VWAP.
    """
    _validate_df(df)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")
    volume = _get_col(df, "Volume")

    tp = (high + low + close) / 3.0
    vp = tp * volume

    if "Time" in df.columns and pd.api.types.is_datetime64_any_dtype(df["Time"]):
        dates = df["Time"].dt.date
        cum_vp = vp.groupby(dates).cumsum()
        cum_vol = volume.groupby(dates).cumsum()
    else:
        cum_vp = vp.cumsum()
        cum_vol = volume.cumsum()

    vwap_series = cum_vp / cum_vol.replace(0, np.nan)
    return vwap_series.rename("VWAP")


def obv(df: pd.DataFrame) -> pd.Series:
    """
    On-Balance Volume (OBV).
    """
    _validate_df(df)
    close = _get_col(df, "Close")
    volume = _get_col(df, "Volume")

    diff = close.diff()
    direction = np.where(diff > 0, 1, np.where(diff < 0, -1, 0))

    obv_series = (direction * volume).cumsum()
    return pd.Series(obv_series, index=df.index, name="OBV")


# ============================================================
# Candle Transformations & Levels
# ============================================================

def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Heikin Ashi candle transformation.
    Returns DataFrame with columns: HA_Open, HA_High, HA_Low, HA_Close
    """
    _validate_df(df)
    opn = _get_col(df, "Open").values.copy()
    high = _get_col(df, "High").values.copy()
    low = _get_col(df, "Low").values.copy()
    close = _get_col(df, "Close").values.copy()

    ha_close = (opn + high + low + close) / 4.0
    ha_open = np.zeros(len(df))
    ha_open[0] = (opn[0] + close[0]) / 2.0

    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0

    ha_high = np.maximum(high, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(low, np.minimum(ha_open, ha_close))

    return pd.DataFrame({
        "HA_Open": ha_open,
        "HA_High": ha_high,
        "HA_Low": ha_low,
        "HA_Close": ha_close
    }, index=df.index)


def pivot_points(
    df: pd.DataFrame,
    method: str = "standard"
) -> pd.DataFrame:
    """
    Pivot Points calculator.

    Args:
        df: DataFrame with High, Low, Close columns.
        method: 'standard', 'fibonacci', or 'camarilla'.

    Returns:
        DataFrame with Pivot, R1-R3, S1-S3 columns.
    """
    _validate_df(df)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    pivot = (high + low + close) / 3.0

    if method == "standard":
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)
        r3 = high + 2 * (pivot - low)
        s3 = low - 2 * (high - pivot)
    elif method == "fibonacci":
        diff = high - low
        r1 = pivot + 0.382 * diff
        r2 = pivot + 0.618 * diff
        r3 = pivot + 1.000 * diff
        s1 = pivot - 0.382 * diff
        s2 = pivot - 0.618 * diff
        s3 = pivot - 1.000 * diff
    elif method == "camarilla":
        diff = high - low
        r1 = close + diff * 1.1 / 12
        r2 = close + diff * 1.1 / 6
        r3 = close + diff * 1.1 / 4
        s1 = close - diff * 1.1 / 12
        s2 = close - diff * 1.1 / 6
        s3 = close - diff * 1.1 / 4
    else:
        raise ValueError(f"Unknown pivot method '{method}'. Use 'standard', 'fibonacci', or 'camarilla'.")

    return pd.DataFrame({
        "Pivot": pivot,
        "R1": r1, "R2": r2, "R3": r3,
        "S1": s1, "S2": s2, "S3": s3
    }, index=df.index)


# ============================================================
# IndicatorsAPI Class
# ============================================================

class IndicatorsAPI:
    """
    Helper API attached to ChoiceClient for applying technical indicators directly to DataFrames.
    """
    def __init__(self, client=None):
        self.client = client

    def add_sma(self, df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"SMA_{period}"] = sma(df, period=period, column=column)
        return df

    def add_ema(self, df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"EMA_{period}"] = ema(df, period=period, column=column)
        return df

    def add_dema(self, df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"DEMA_{period}"] = dema(df, period=period, column=column)
        return df

    def add_tema(self, df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"TEMA_{period}"] = tema(df, period=period, column=column)
        return df

    def add_wma(self, df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"WMA_{period}"] = wma(df, period=period, column=column)
        return df

    def add_rsi(self, df: pd.DataFrame, period: int = 14, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"RSI_{period}"] = rsi(df, period=period, column=column)
        return df

    def add_macd(
        self,
        df: pd.DataFrame,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        column: str = "Close"
    ) -> pd.DataFrame:
        df = df.copy()
        macd_df = macd(df, fast_period=fast_period, slow_period=slow_period, signal_period=signal_period, column=column)
        for col in macd_df.columns:
            df[col] = macd_df[col]
        return df

    def add_bollinger_bands(
        self,
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0,
        column: str = "Close"
    ) -> pd.DataFrame:
        df = df.copy()
        bb_df = bollinger_bands(df, period=period, std_dev=std_dev, column=column)
        for col in bb_df.columns:
            df[col] = bb_df[col]
        return df

    def add_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        df[f"ATR_{period}"] = atr(df, period=period)
        return df

    def add_supertrend(self, df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
        df = df.copy()
        st_df = supertrend(df, period=period, multiplier=multiplier)
        for col in st_df.columns:
            df[col] = st_df[col]
        return df

    def add_adx(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        adx_df = adx(df, period=period)
        for col in adx_df.columns:
            df[col] = adx_df[col]
        return df

    def add_stochastic(self, df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth_k: int = 3) -> pd.DataFrame:
        df = df.copy()
        stoch_df = stochastic(df, k_period=k_period, d_period=d_period, smooth_k=smooth_k)
        for col in stoch_df.columns:
            df[col] = stoch_df[col]
        return df

    def add_cci(self, df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        df = df.copy()
        df[f"CCI_{period}"] = cci(df, period=period)
        return df

    def add_williams_r(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        df[f"Williams_R_{period}"] = williams_r(df, period=period)
        return df

    def add_vwap(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["VWAP"] = vwap(df)
        return df

    def add_obv(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["OBV"] = obv(df)
        return df

    def add_parabolic_sar(self, df: pd.DataFrame, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.20) -> pd.DataFrame:
        df = df.copy()
        psar_df = parabolic_sar(df, af_start=af_start, af_step=af_step, af_max=af_max)
        for col in psar_df.columns:
            df[col] = psar_df[col]
        return df

    def add_ichimoku(self, df: pd.DataFrame, tenkan_period: int = 9, kijun_period: int = 26, senkou_b_period: int = 52, displacement: int = 26) -> pd.DataFrame:
        df = df.copy()
        ichi_df = ichimoku(df, tenkan_period=tenkan_period, kijun_period=kijun_period, senkou_b_period=senkou_b_period, displacement=displacement)
        for col in ichi_df.columns:
            df[col] = ichi_df[col]
        return df

    def add_donchian_channel(self, df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        df = df.copy()
        dc_df = donchian_channel(df, period=period)
        for col in dc_df.columns:
            df[col] = dc_df[col]
        return df

    def add_heikin_ashi(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        ha_df = heikin_ashi(df)
        for col in ha_df.columns:
            df[col] = ha_df[col]
        return df

    def add_pivot_points(self, df: pd.DataFrame, method: str = "standard") -> pd.DataFrame:
        df = df.copy()
        pp_df = pivot_points(df, method=method)
        for col in pp_df.columns:
            df[col] = pp_df[col]
        return df

    def add_all(
        self,
        df: pd.DataFrame,
        sma_period: int = 20,
        ema_period: int = 20,
        rsi_period: int = 14,
        atr_period: int = 14,
        supertrend_period: int = 10,
        supertrend_multiplier: float = 3.0,
        bb_period: int = 20,
        bb_std_dev: float = 2.0
    ) -> pd.DataFrame:
        """
        Adds all standard technical indicators to the DataFrame at once.
        All periods are customizable via keyword arguments.
        """
        df = self.add_sma(df, period=sma_period)
        df = self.add_ema(df, period=ema_period)
        df = self.add_rsi(df, period=rsi_period)
        df = self.add_macd(df)
        df = self.add_bollinger_bands(df, period=bb_period, std_dev=bb_std_dev)
        df = self.add_atr(df, period=atr_period)
        df = self.add_supertrend(df, period=supertrend_period, multiplier=supertrend_multiplier)
        df = self.add_vwap(df)
        df = self.add_obv(df)
        return df
