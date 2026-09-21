"""
Technical Indicators Module for choice_api (Kkunal).

Provides vectorized, mathematical implementations of standard technical analysis indicators
using pandas and numpy. No external C-dependencies required.

Indicators:
    Trend:       SMA, EMA, WMA, DEMA, TEMA, MACD, ADX, Supertrend, Parabolic SAR, Ichimoku Cloud
    Momentum:    RSI, Stochastic Oscillator, CCI, Williams %R, MFI, ROC
    Volatility:  Bollinger Bands, ATR, Donchian Channel, Keltner Channel
    Volume:      VWAP, OBV
    Levels:      Pivot Points, Central Pivot Range (CPR)
    Utilities:   Crossover, Crossunder, Heikin Ashi

Conventions (chosen so values match TA-Lib and TradingView bar for bar):
  * A windowed indicator returns NaN until its lookback window is full, so a
    value is only ever produced from a complete window. Drop those leading rows
    (e.g. `df.dropna()`) before feeding results into a strategy.
  * EMA-based indicators (EMA, DEMA, TEMA, MACD, Keltner) and Wilder-smoothed
    ones (RSI, ATR, ADX, Supertrend) seed the recursion with the simple average
    of the first window rather than with the first value.
  * Input must be in chronological order, oldest first.
"""

import inspect
from typing import Optional, Dict
import numpy as np
import pandas as pd


# ============================================================
# Validation Helpers
# ============================================================

def _validate_period(period: int, name: str = "period") -> None:
    """Validates that a period parameter is a positive integer."""
    if isinstance(period, bool) or not isinstance(period, (int, np.integer)) or period < 1:
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
# Smoothing Helpers
# ============================================================

def _seeded_ewm(series: pd.Series, period: int, alpha: float) -> pd.Series:
    """
    Recursive (exponential) smoothing seeded with the simple average of the first
    `period` observations - the convention TA-Lib and TradingView use.

    Seeding with the first value instead, as a bare `ewm(adjust=False)` does,
    converges to the same numbers eventually but is badly off early on: RSI_14
    differs by a median of 4 points at bar 30 and is still 1 point out at bar 50.
    """
    values = series.to_numpy(dtype=float, copy=True)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < period:
        return pd.Series(np.nan, index=series.index, dtype=float)
    seed_pos = valid[period - 1]
    seed = values[valid[:period]].mean()
    values[:seed_pos] = np.nan
    values[seed_pos] = seed
    return pd.Series(values, index=series.index).ewm(alpha=alpha, adjust=False).mean()


def _ema_series(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, SMA-seeded. First value at index period-1."""
    return _seeded_ewm(series, period, alpha=2.0 / (period + 1))


def _rma_series(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (TradingView's ta.rma), SMA-seeded."""
    return _seeded_ewm(series, period, alpha=1.0 / period)


def _time_series(df: pd.DataFrame) -> Optional[pd.Series]:
    """The bar timestamps: a datetime 'Time' column (any case), else a DatetimeIndex, else None."""
    for c in df.columns:
        if str(c).lower() == "time" and pd.api.types.is_datetime64_any_dtype(df[c]):
            return df[c]
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, index=df.index)
    return None


def _previous_period_hlc(df: pd.DataFrame, anchor: str):
    """
    High/Low/Close of the PREVIOUS period for every bar, which is what floor-trader
    levels are built from.

    On intraday data the period is the trading day: every bar of a session gets the
    prior session's H/L/C, as charting platforms draw daily pivots on intraday charts.
    On daily (or slower) data it is simply the previous bar.
    """
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")

    anchor = str(anchor)
    if anchor.lower() not in ("auto", "bar") and anchor.upper() not in ("D", "W", "M"):
        raise ValueError(f"anchor must be 'auto', 'bar', 'D', 'W' or 'M', got {anchor!r}")

    times = _time_series(df)
    if anchor.lower() == "auto":
        intraday = times is not None and bool(times.dt.normalize().duplicated().any())
        anchor = "D" if intraday else "bar"

    if anchor.lower() == "bar":
        return high.shift(1), low.shift(1), close.shift(1)

    if times is None:
        raise ValueError(f"anchor={anchor!r} needs a datetime 'Time' column or a DatetimeIndex")

    periods = times.dt.to_period(anchor.upper()).to_numpy()
    frame = pd.DataFrame({"h": high.to_numpy(), "l": low.to_numpy(), "c": close.to_numpy()})
    per_period = frame.groupby(periods).agg(h=("h", "max"), l=("l", "min"), c=("c", "last"))
    previous = per_period.shift(1).reindex(periods)
    return (pd.Series(previous["h"].to_numpy(), index=df.index),
            pd.Series(previous["l"].to_numpy(), index=df.index),
            pd.Series(previous["c"].to_numpy(), index=df.index))


def _rolling_mean_abs_dev(values: np.ndarray, period: int) -> np.ndarray:
    """Rolling mean absolute deviation, vectorised in blocks to bound memory."""
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, period)
    step = max(1, 2_000_000 // period)
    for start in range(0, len(windows), step):
        block = windows[start:start + step]
        out[period - 1 + start: period - 1 + start + len(block)] = (
            np.abs(block - block.mean(axis=1, keepdims=True)).mean(axis=1)
        )
    return out


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
    a = series_a.to_numpy()
    b = series_b.to_numpy() if isinstance(series_b, pd.Series) else np.full(len(a), series_b)
    if len(a) != len(b):
        raise ValueError(f"crossover() needs two series of equal length, got {len(a)} and {len(b)}")
    if len(a) == 0:
        return pd.Series([], index=series_a.index, dtype=bool, name="Crossover")
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
    a = series_a.to_numpy()
    b = series_b.to_numpy() if isinstance(series_b, pd.Series) else np.full(len(a), series_b)
    if len(a) != len(b):
        raise ValueError(f"crossunder() needs two series of equal length, got {len(a)} and {len(b)}")
    if len(a) == 0:
        return pd.Series([], index=series_a.index, dtype=bool, name="Crossunder")
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
    return s.rolling(window=period, min_periods=period).mean().rename(f"SMA_{period}")


def ema(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Exponential Moving Average (EMA), seeded with the SMA of the first window.
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    return _ema_series(s, period).rename(f"EMA_{period}")


def dema(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Double Exponential Moving Average (DEMA).
    DEMA = 2 * EMA(period) - EMA(EMA(period))
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    ema1 = _ema_series(s, period)
    ema2 = _ema_series(ema1, period)
    return (2 * ema1 - ema2).rename(f"DEMA_{period}")


def tema(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Triple Exponential Moving Average (TEMA).
    TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    ema1 = _ema_series(s, period)
    ema2 = _ema_series(ema1, period)
    ema3 = _ema_series(ema2, period)
    return (3 * ema1 - 3 * ema2 + ema3).rename(f"TEMA_{period}")


def wma(df: pd.DataFrame, period: int = 20, column: str = "Close") -> pd.Series:
    """
    Weighted Moving Average (WMA).
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    values = s.to_numpy(dtype=float)
    weights = np.arange(1, period + 1, dtype=float)

    out = np.full(len(values), np.nan)
    if len(values) >= period:
        windows = np.lib.stride_tricks.sliding_window_view(values, period)
        out[period - 1:] = windows @ weights / weights.sum()
    return pd.Series(out, index=s.index, name=f"WMA_{period}")


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
    if fast_period >= slow_period:
        raise ValueError(f"fast_period ({fast_period}) must be smaller than slow_period ({slow_period})")
    s = _get_col(df, column)
    fast_ema = _ema_series(s, fast_period)
    slow_ema = _ema_series(s, slow_period)

    macd_line = fast_ema - slow_ema
    signal_line = _ema_series(macd_line, signal_period)
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

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # The first bar has no predecessor, so its movement and true range are undefined.
    plus_dm.iloc[:1] = np.nan
    minus_dm.iloc[:1] = np.nan
    tr.iloc[:1] = np.nan

    tr_smooth = _rma_series(tr, period)
    plus_dm_smooth = _rma_series(plus_dm, period)
    minus_dm_smooth = _rma_series(minus_dm, period)

    plus_di = 100 * (plus_dm_smooth / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm_smooth / tr_smooth.replace(0, np.nan))

    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
    dx = dx.where(di_sum != 0, 0.0)            # no directional movement at all
    adx_series = _rma_series(dx, period)

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
    Parabolic SAR (Stop and Reverse), following Wilder's rules as TA-Lib implements them.

    Returns DataFrame with columns: PSAR, PSAR_Trend (+1 = Bullish, -1 = Bearish,
    0 on the first bar, where no SAR exists yet).
    """
    _validate_df(df)
    high = _get_col(df, "High").to_numpy(dtype=float)
    low = _get_col(df, "Low").to_numpy(dtype=float)
    n = len(df)

    psar = np.full(n, np.nan)
    trend = np.zeros(n, dtype=int)
    if n < 2:
        return pd.DataFrame({"PSAR": psar, "PSAR_Trend": trend}, index=df.index)

    # The opening direction comes from the first two bars' directional movement.
    up_move = high[1] - high[0]
    down_move = low[0] - low[1]
    is_long = not (down_move > 0 and down_move > up_move)

    if is_long:
        ep, sar = high[1], low[0]
    else:
        ep, sar = low[1], high[0]
    af = af_start
    new_low, new_high = low[1], high[1]

    for i in range(1, n):
        prev_low, prev_high = new_low, new_high
        new_low, new_high = low[i], high[i]

        if is_long:
            if new_low <= sar:
                # Reverse to short. The SAR may never sit inside the last two bars.
                is_long = False
                sar = max(ep, prev_high, new_high)
                psar[i] = sar
                af, ep = af_start, new_low
                sar = max(sar + af * (ep - sar), prev_high, new_high)
            else:
                psar[i] = sar
                if new_high > ep:
                    ep = new_high
                    af = min(af + af_step, af_max)
                sar = min(sar + af * (ep - sar), prev_low, new_low)
        else:
            if new_high >= sar:
                # Reverse to long.
                is_long = True
                sar = min(ep, prev_low, new_low)
                psar[i] = sar
                af, ep = af_start, new_high
                sar = min(sar + af * (ep - sar), prev_low, new_low)
            else:
                psar[i] = sar
                if new_low < ep:
                    ep = new_low
                    af = min(af + af_step, af_max)
                sar = max(sar + af * (ep - sar), prev_high, new_high)

        trend[i] = 1 if is_long else -1

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
    tenkan = (high.rolling(window=tenkan_period, min_periods=tenkan_period).max()
              + low.rolling(window=tenkan_period, min_periods=tenkan_period).min()) / 2.0

    # Kijun-sen (Base Line)
    kijun = (high.rolling(window=kijun_period, min_periods=kijun_period).max()
             + low.rolling(window=kijun_period, min_periods=kijun_period).min()) / 2.0

    # Senkou Span A (Leading Span A) — displaced forward
    senkou_a = ((tenkan + kijun) / 2.0).shift(displacement)

    # Senkou Span B (Leading Span B) — displaced forward
    senkou_b = ((high.rolling(window=senkou_b_period, min_periods=senkou_b_period).max()
                 + low.rolling(window=senkou_b_period, min_periods=senkou_b_period).min()) / 2.0).shift(displacement)

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
    Relative Strength Index (RSI) using Wilder's smoothing, seeded with the simple
    average of the first `period` gains/losses as Wilder defined it.
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    delta = s.diff()

    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)

    avg_gain = _rma_series(gain, period)
    avg_loss = _rma_series(loss, period)

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))

    # Edge cases: no losses in the window => 100; a completely flat window => 50.
    rsi_series = rsi_series.where(avg_loss != 0, 100.0)
    rsi_series = rsi_series.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)

    # `delta` is undefined on the first bar, so the first complete window of
    # `period` deltas only closes at index `period`.
    rsi_series.iloc[:period] = np.nan
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

    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()

    denom = (highest_high - lowest_low).replace(0, np.nan)
    raw_k = 100 * (close - lowest_low) / denom

    stoch_k = raw_k.rolling(window=smooth_k, min_periods=smooth_k).mean()
    stoch_d = stoch_k.rolling(window=d_period, min_periods=d_period).mean()

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
    sma_tp = tp.rolling(window=period, min_periods=period).mean()
    mad = pd.Series(_rolling_mean_abs_dev(tp.to_numpy(dtype=float), period), index=tp.index)

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

    highest_high = high.rolling(window=period, min_periods=period).max()
    lowest_low = low.rolling(window=period, min_periods=period).min()

    denom = (highest_high - lowest_low).replace(0, np.nan)
    wr = -100 * (highest_high - close) / denom
    return wr.rename(f"Williams_R_{period}")


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Money Flow Index (MFI) - a volume-weighted RSI, oscillating between 0 and 100.
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")
    close = _get_col(df, "Close")
    volume = _get_col(df, "Volume")

    tp = (high + low + close) / 3.0
    flow = tp * volume
    change = tp.diff()

    positive = flow.where(change > 0, 0.0)
    negative = flow.where(change < 0, 0.0)
    positive.iloc[:1] = np.nan                  # the first bar has nothing to compare with
    negative.iloc[:1] = np.nan

    pos_sum = positive.rolling(window=period, min_periods=period).sum()
    neg_sum = negative.rolling(window=period, min_periods=period).sum()

    ratio = pos_sum / neg_sum.replace(0, np.nan)
    mfi_series = 100 - (100 / (1 + ratio))
    mfi_series = mfi_series.where(neg_sum != 0, 100.0)
    mfi_series = mfi_series.where(~((pos_sum == 0) & (neg_sum == 0)), 50.0)
    mfi_series = mfi_series.where(pos_sum.notna())
    return mfi_series.rename(f"MFI_{period}")


def roc(df: pd.DataFrame, period: int = 12, column: str = "Close") -> pd.Series:
    """
    Rate of Change (ROC): the percentage move over the last `period` bars.
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    base = s.shift(period).replace(0, np.nan)
    return ((s / base - 1.0) * 100.0).rename(f"ROC_{period}")


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
    Uses the population standard deviation (ddof=0), matching TradingView and
    most charting platforms.
    Returns DataFrame with columns: BB_Upper, BB_Middle, BB_Lower, BB_Bandwidth, BB_PercentB
    """
    _validate_df(df)
    _validate_period(period)
    s = _get_col(df, column)
    middle = s.rolling(window=period, min_periods=period).mean()
    std = s.rolling(window=period, min_periods=period).std(ddof=0)

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
    Average True Range (ATR) using Wilder's smoothing, seeded with the simple
    average of the first `period` true ranges.
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

    # On the first bar there is no previous close, so the true range is High - Low.
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return _rma_series(tr, period).rename(f"ATR_{period}")


def donchian_channel(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Donchian Channel.
    Returns DataFrame with columns: Donchian_Upper, Donchian_Middle, Donchian_Lower
    """
    _validate_df(df)
    _validate_period(period)
    high = _get_col(df, "High")
    low = _get_col(df, "Low")

    upper = high.rolling(window=period, min_periods=period).max()
    lower = low.rolling(window=period, min_periods=period).min()
    middle = (upper + lower) / 2.0

    return pd.DataFrame({
        "Donchian_Upper": upper,
        "Donchian_Middle": middle,
        "Donchian_Lower": lower
    }, index=df.index)


def keltner_channel(
    df: pd.DataFrame,
    period: int = 20,
    multiplier: float = 2.0,
    atr_period: int = 10
) -> pd.DataFrame:
    """
    Keltner Channel: an EMA midline with bands `multiplier` ATRs either side.
    Returns DataFrame with columns: KC_Upper, KC_Middle, KC_Lower
    """
    _validate_df(df)
    _validate_period(period)
    _validate_period(atr_period, "atr_period")
    close = _get_col(df, "Close")

    middle = _ema_series(close, period)
    band = multiplier * atr(df, period=atr_period)

    return pd.DataFrame({
        "KC_Upper": middle + band,
        "KC_Middle": middle,
        "KC_Lower": middle - band
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
    opn = _get_col(df, "Open").to_numpy(dtype=float)
    high = _get_col(df, "High").to_numpy(dtype=float)
    low = _get_col(df, "Low").to_numpy(dtype=float)
    close = _get_col(df, "Close").to_numpy(dtype=float)

    if len(df) == 0:
        return pd.DataFrame(
            {c: np.zeros(0) for c in ("HA_Open", "HA_High", "HA_Low", "HA_Close")},
            index=df.index
        )

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
    method: str = "standard",
    anchor: str = "auto"
) -> pd.DataFrame:
    """
    Pivot Points calculator.

    Levels are derived from the PREVIOUS period's High/Low/Close, so the values on
    any given bar are known before that bar opens.

    Args:
        df: DataFrame with High, Low, Close columns.
        method: 'standard', 'fibonacci', or 'camarilla'.
        anchor: Which previous period to use.
            'auto' (default) - on intraday data (several bars per day, detected from
            the 'Time' column) the previous trading DAY, so every bar of a session
            carries that day's levels, as charting platforms draw them; on daily or
            slower data, the previous bar.
            'bar' - always the previous bar. 'D' / 'W' / 'M' - the previous day,
            week or month (needs a datetime 'Time' column or DatetimeIndex).

    Returns:
        DataFrame with Pivot, R1-R3, S1-S3 columns. Rows of the first period are NaN.
    """
    _validate_df(df)
    high, low, close = _previous_period_hlc(df, anchor)

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


def cpr(df: pd.DataFrame, anchor: str = "auto") -> pd.DataFrame:
    """
    Central Pivot Range (CPR), from the previous period's High/Low/Close.

        Pivot = (H + L + C) / 3,  BC = (H + L) / 2,  TC = 2 * Pivot - BC

    TC is always reported as the upper edge and BC as the lower one, whichever way
    the raw formula falls, so `close > CPR_TC` reads naturally. A narrow range
    (small CPR_Width, in percent of the pivot) often precedes a trending session.

    Args:
        anchor: Same as pivot_points() - 'auto' uses the previous trading day on
            intraday data and the previous bar otherwise.

    Returns:
        DataFrame with CPR_Pivot, CPR_BC, CPR_TC, CPR_Width columns.
    """
    _validate_df(df)
    high, low, close = _previous_period_hlc(df, anchor)

    pivot = (high + low + close) / 3.0
    mid = (high + low) / 2.0
    other = 2 * pivot - mid

    top = mid.where(mid >= other, other)
    bottom = mid.where(mid <= other, other)
    width = (top - bottom) / pivot.replace(0, np.nan) * 100.0

    return pd.DataFrame({
        "CPR_Pivot": pivot,
        "CPR_BC": bottom,
        "CPR_TC": top,
        "CPR_Width": width
    }, index=df.index)


# ============================================================
# Indicator Name Resolution
# ============================================================

#: Maps user-facing names and shorthands onto IndicatorsAPI.add_* methods.
INDICATOR_ALIASES: Dict[str, str] = {
    "sma": "sma", "ema": "ema", "dema": "dema", "tema": "tema", "wma": "wma",
    "rsi": "rsi", "macd": "macd", "adx": "adx",
    "bb": "bollinger_bands", "bollinger": "bollinger_bands",
    "bollinger_bands": "bollinger_bands",
    "atr": "atr",
    "st": "supertrend", "supertrend": "supertrend",
    "stoch": "stochastic", "stochastic": "stochastic",
    "cci": "cci",
    "wr": "williams_r", "williams": "williams_r", "williams_r": "williams_r",
    "vwap": "vwap", "obv": "obv",
    "psar": "parabolic_sar", "parabolic": "parabolic_sar",
    "parabolic_sar": "parabolic_sar",
    "ichi": "ichimoku", "ichimoku": "ichimoku",
    "dc": "donchian_channel", "donchian": "donchian_channel",
    "donchian_channel": "donchian_channel",
    "ha": "heikin_ashi", "heikinashi": "heikin_ashi", "heikin_ashi": "heikin_ashi",
    "pp": "pivot_points", "pivot": "pivot_points", "pivot_points": "pivot_points",
    "cpr": "cpr",
    "mfi": "mfi", "roc": "roc",
    "kc": "keltner_channel", "keltner": "keltner_channel", "keltner_channel": "keltner_channel",
}


def resolve_indicator(name: str) -> Optional[str]:
    """Resolves an indicator name or shorthand to its add_* method suffix."""
    return INDICATOR_ALIASES.get(str(name).strip().lower())


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

    def add_pivot_points(self, df: pd.DataFrame, method: str = "standard", anchor: str = "auto") -> pd.DataFrame:
        df = df.copy()
        pp_df = pivot_points(df, method=method, anchor=anchor)
        for col in pp_df.columns:
            df[col] = pp_df[col]
        return df

    def add_cpr(self, df: pd.DataFrame, anchor: str = "auto") -> pd.DataFrame:
        df = df.copy()
        cpr_df = cpr(df, anchor=anchor)
        for col in cpr_df.columns:
            df[col] = cpr_df[col]
        return df

    def add_mfi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        df[f"MFI_{period}"] = mfi(df, period=period)
        return df

    def add_roc(self, df: pd.DataFrame, period: int = 12, column: str = "Close") -> pd.DataFrame:
        df = df.copy()
        df[f"ROC_{period}"] = roc(df, period=period, column=column)
        return df

    def add_keltner_channel(self, df: pd.DataFrame, period: int = 20, multiplier: float = 2.0,
                            atr_period: int = 10) -> pd.DataFrame:
        df = df.copy()
        kc_df = keltner_channel(df, period=period, multiplier=multiplier, atr_period=atr_period)
        for col in kc_df.columns:
            df[col] = kc_df[col]
        return df

    # Indicators applied by add_all(), in output order.
    CORE_INDICATORS = ("sma", "ema", "rsi", "macd", "bollinger_bands",
                       "atr", "supertrend", "vwap", "obv")
    EXTRA_INDICATORS = ("dema", "tema", "wma", "adx", "stochastic", "cci",
                        "williams_r", "mfi", "roc", "parabolic_sar", "ichimoku",
                        "donchian_channel", "keltner_channel", "heikin_ashi",
                        "pivot_points", "cpr")
    ALL_INDICATORS = CORE_INDICATORS + EXTRA_INDICATORS

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
        bb_std_dev: float = 2.0,
        core_only: bool = False
    ) -> pd.DataFrame:
        """
        Adds every technical indicator in this module to the DataFrame at once.

        Args:
            core_only: Restricts the output to the nine most commonly used
                indicators (SMA, EMA, RSI, MACD, Bollinger, ATR, Supertrend,
                VWAP, OBV) instead of all of them.

        Periods for the core indicators are customisable via keyword arguments;
        the remainder use their documented defaults. Use the individual add_*
        methods when you need to tune those.
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

        if core_only:
            return df

        for name in self.EXTRA_INDICATORS:
            df = getattr(self, f"add_{name}")(df)
        return df

    def add(self, df: pd.DataFrame, *names: str, **kwargs) -> pd.DataFrame:
        """
        Applies indicators by name, so they can be chosen at runtime.

        Aliases match those accepted by
        HistoricalAPI.get_historical_data_with_indicators, e.g. 'bb', 'st', 'psar'.

        Example:
            >>> df = client.indicators.add(df, "rsi", "macd", "supertrend")
            >>> df = client.indicators.add(df, "rsi", period=21)
        """
        methods = []
        for name in names:
            method = resolve_indicator(name)
            if method is None:
                raise ValueError(
                    f"Unknown indicator {name!r}. Available: {', '.join(sorted(INDICATOR_ALIASES))}"
                )
            fn = getattr(self, f"add_{method}")
            methods.append((fn, set(inspect.signature(fn).parameters) - {"df"}))

        # Each keyword goes to the indicators that accept it. One that none of
        # them accepts is a typo, not something to drop silently.
        unused = set(kwargs) - set().union(*(params for _, params in methods)) if methods else set(kwargs)
        if unused:
            raise TypeError(f"add() got keyword argument(s) no selected indicator accepts: {sorted(unused)}")

        for fn, params in methods:
            df = fn(df, **{k: v for k, v in kwargs.items() if k in params})
        return df
