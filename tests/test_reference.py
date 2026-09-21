"""
Checks every smoothed indicator against an INDEPENDENT reference.

The references below are deliberately naive loops written straight from the
published definitions (Wilder, TA-Lib, TradingView), sharing no code with the
library. Where TA-Lib itself is installed it is used as a second opinion.
"""

import unittest

import numpy as np
import pandas as pd

from choice_api import (
    adx, atr, bollinger_bands, cci, cpr, dema, ema, keltner_channel, macd, mfi,
    parabolic_sar, pivot_points, roc, rsi, sma, stochastic, supertrend, tema, williams_r, wma,
    crossover, crossunder, IndicatorsAPI,
)

try:
    import talib
except ImportError:          # optional: CI runs without it
    talib = None


def make_frame(seed=11, n=400):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.1, 1.5, n)
    low = close - rng.uniform(0.1, 1.5, n)
    return pd.DataFrame({
        "Time": pd.date_range("2024-01-01", periods=n, freq="D"),
        "Open": close + rng.normal(0, 0.3, n), "High": high, "Low": low, "Close": close,
        "Volume": rng.integers(1000, 50000, n).astype(float),
    })


# --- naive references -------------------------------------------------------

def ref_recursive(values, n, alpha):
    """Recursive smoothing seeded with the SMA of the first n valid values."""
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < n:
        return out
    start = valid[n - 1]
    out[start] = values[valid[:n]].mean()
    for i in range(start + 1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def ref_ema(values, n):
    return ref_recursive(values, n, 2 / (n + 1))


def ref_rma(values, n):
    return ref_recursive(values, n, 1 / n)


def ref_true_range(high, low, close, first_is_nan):
    tr = np.empty(len(close))
    tr[0] = np.nan if first_is_nan else high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return tr


class ReferenceTestCase(unittest.TestCase):
    def setUp(self):
        self.df = make_frame()
        self.c = self.df["Close"].to_numpy()
        self.h = self.df["High"].to_numpy()
        self.l = self.df["Low"].to_numpy()
        self.v = self.df["Volume"].to_numpy()

    def assertSeriesMatch(self, got, expected, tol=1e-8, label=""):
        got, expected = np.asarray(got, float), np.asarray(expected, float)
        self.assertTrue(np.array_equal(np.isnan(got), np.isnan(expected)),
                        f"{label}: NaN (warmup) pattern differs from the reference")
        both = ~np.isnan(got)
        if both.any():
            self.assertLess(np.max(np.abs(got[both] - expected[both])), tol, f"{label}: values differ")


class TestAgainstNaiveReferences(ReferenceTestCase):
    def test_ema_family(self):
        e1 = ref_ema(self.c, 20)
        e2 = ref_ema(e1, 20)
        e3 = ref_ema(e2, 20)
        self.assertSeriesMatch(ema(self.df, 20), e1, label="EMA")
        self.assertSeriesMatch(dema(self.df, 20), 2 * e1 - e2, label="DEMA")
        self.assertSeriesMatch(tema(self.df, 20), 3 * e1 - 3 * e2 + e3, label="TEMA")

    def test_macd(self):
        line = ref_ema(self.c, 12) - ref_ema(self.c, 26)
        signal = ref_ema(line, 9)
        result = macd(self.df)
        self.assertSeriesMatch(result["MACD"], line, label="MACD")
        self.assertSeriesMatch(result["MACD_Signal"], signal, label="MACD_Signal")
        self.assertSeriesMatch(result["MACD_Hist"], line - signal, label="MACD_Hist")

    def test_rsi(self):
        delta = np.diff(self.c, prepend=np.nan)
        gain, loss = np.where(delta > 0, delta, 0.0), np.where(delta < 0, -delta, 0.0)
        gain[0] = loss[0] = np.nan
        expected = 100 - 100 / (1 + ref_rma(gain, 14) / ref_rma(loss, 14))
        self.assertSeriesMatch(rsi(self.df, 14), expected, label="RSI")

    def test_atr(self):
        expected = ref_rma(ref_true_range(self.h, self.l, self.c, first_is_nan=False), 14)
        self.assertSeriesMatch(atr(self.df, 14), expected, label="ATR")

    def test_adx(self):
        n = len(self.c)
        plus_dm, minus_dm = np.full(n, np.nan), np.full(n, np.nan)
        for i in range(1, n):
            up, down = self.h[i] - self.h[i - 1], self.l[i - 1] - self.l[i]
            plus_dm[i] = up if (up > down and up > 0) else 0.0
            minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr = ref_rma(ref_true_range(self.h, self.l, self.c, first_is_nan=True), 14)
        plus_di, minus_di = 100 * ref_rma(plus_dm, 14) / tr, 100 * ref_rma(minus_dm, 14) / tr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)

        result = adx(self.df, 14)
        self.assertSeriesMatch(result["Plus_DI"], plus_di, label="+DI")
        self.assertSeriesMatch(result["Minus_DI"], minus_di, label="-DI")
        self.assertSeriesMatch(result["ADX"], ref_rma(dx, 14), label="ADX")
        self.assertEqual(result["ADX"].first_valid_index(), 27)          # 2 * period - 1

    def test_wma_vectorised(self):
        weights = np.arange(1, 21)
        expected = np.full(len(self.c), np.nan)
        for i in range(19, len(self.c)):
            expected[i] = np.dot(self.c[i - 19:i + 1], weights) / weights.sum()
        self.assertSeriesMatch(wma(self.df, 20), expected, label="WMA")

    def test_cci_vectorised(self):
        tp = (self.h + self.l + self.c) / 3
        expected = np.full(len(tp), np.nan)
        for i in range(19, len(tp)):
            window = tp[i - 19:i + 1]
            expected[i] = (tp[i] - window.mean()) / (0.015 * np.abs(window - window.mean()).mean())
        self.assertSeriesMatch(cci(self.df, 20), expected, tol=1e-6, label="CCI")

    def test_mfi(self):
        tp = (self.h + self.l + self.c) / 3
        expected = np.full(len(tp), np.nan)
        for i in range(14, len(tp)):
            pos = sum(tp[j] * self.v[j] for j in range(i - 13, i + 1) if tp[j] > tp[j - 1])
            neg = sum(tp[j] * self.v[j] for j in range(i - 13, i + 1) if tp[j] < tp[j - 1])
            expected[i] = 100 - 100 / (1 + pos / neg) if neg else 100.0
        self.assertSeriesMatch(mfi(self.df, 14), expected, label="MFI")

    def test_roc(self):
        expected = np.full(len(self.c), np.nan)
        expected[12:] = (self.c[12:] / self.c[:-12] - 1) * 100
        self.assertSeriesMatch(roc(self.df, 12), expected, label="ROC")

    def test_keltner(self):
        middle = ref_ema(self.c, 20)
        band = 2 * ref_rma(ref_true_range(self.h, self.l, self.c, first_is_nan=False), 10)
        result = keltner_channel(self.df)
        self.assertSeriesMatch(result["KC_Middle"], middle, label="KC_Middle")
        self.assertSeriesMatch(result["KC_Upper"], middle + band, label="KC_Upper")
        self.assertSeriesMatch(result["KC_Lower"], middle - band, label="KC_Lower")

    def test_supertrend_follows_its_own_rules(self):
        result = supertrend(self.df, 10, 3.0)
        body = result.dropna()
        close = self.df["Close"].loc[body.index]
        bullish = body["Supertrend_Direction"] == 1
        self.assertTrue((body["Supertrend"][bullish] <= close[bullish] + 1e-9).all())
        self.assertTrue((body["Supertrend"][~bullish] >= close[~bullish] - 1e-9).all())

    def test_short_series_is_all_nan_not_an_error(self):
        tiny = self.df.iloc[:5]
        for fn in (ema, rsi, atr, wma, cci, mfi):
            with self.subTest(indicator=fn.__name__):
                self.assertTrue(fn(tiny, 14).isna().all())
        self.assertTrue(adx(tiny)["ADX"].isna().all())
        self.assertTrue(macd(tiny)["MACD"].isna().all())


@unittest.skipUnless(talib, "TA-Lib is not installed")
class TestAgainstTALib(ReferenceTestCase):
    """Bar-for-bar agreement with the reference C implementation, warmup included."""

    def test_exact_matches(self):
        bands = bollinger_bands(self.df, 20, 2.0)
        upper, _, lower = talib.BBANDS(self.c, 20, 2, 2)
        slow_k, slow_d = talib.STOCH(self.h, self.l, self.c, 14, 3, 0, 3, 0)
        cases = {
            "SMA": (sma(self.df, 20), talib.SMA(self.c, 20)),
            "EMA": (ema(self.df, 20), talib.EMA(self.c, 20)),
            "DEMA": (dema(self.df, 20), talib.DEMA(self.c, 20)),
            "TEMA": (tema(self.df, 20), talib.TEMA(self.c, 20)),
            "WMA": (wma(self.df, 20), talib.WMA(self.c, 20)),
            "RSI": (rsi(self.df, 14), talib.RSI(self.c, 14)),
            "CCI": (cci(self.df, 20), talib.CCI(self.h, self.l, self.c, 20)),
            "WILLR": (williams_r(self.df, 14), talib.WILLR(self.h, self.l, self.c, 14)),
            "MFI": (mfi(self.df, 14), talib.MFI(self.h, self.l, self.c, self.v, 14)),
            "ROC": (roc(self.df, 12), talib.ROC(self.c, 12)),
            "BB_Upper": (bands["BB_Upper"], upper),
            "BB_Lower": (bands["BB_Lower"], lower),
            "SAR": (parabolic_sar(self.df)["PSAR"], talib.SAR(self.h, self.l, 0.02, 0.20)),
            "Stoch_D": (stochastic(self.df, 14, 3, 3)["Stoch_D"], slow_d),
        }
        for label, (got, expected) in cases.items():
            with self.subTest(indicator=label):
                self.assertSeriesMatch(got, expected, tol=1e-6, label=label)

    def test_parabolic_sar_on_many_series(self):
        for seed in range(40):
            df = make_frame(seed=seed, n=250)
            with self.subTest(seed=seed):
                self.assertSeriesMatch(parabolic_sar(df)["PSAR"],
                                       talib.SAR(df["High"].to_numpy(), df["Low"].to_numpy(), 0.02, 0.20),
                                       tol=1e-8, label="SAR")

    def test_seeding_differences_wash_out(self):
        """ADX/MACD/ATR differ from TA-Lib only in how the first window is seeded."""
        tail = slice(250, None)
        pairs = {
            "ADX": (adx(self.df, 14)["ADX"], talib.ADX(self.h, self.l, self.c, 14)),
            "MACD": (macd(self.df)["MACD"], talib.MACD(self.c, 12, 26, 9)[0]),
            "ATR": (atr(self.df, 14), talib.ATR(self.h, self.l, self.c, 14)),
        }
        for label, (got, expected) in pairs.items():
            with self.subTest(indicator=label):
                self.assertLess(np.nanmax(np.abs(np.asarray(got)[tail] - expected[tail])), 1e-4)


class TestPivotAnchoring(unittest.TestCase):
    def intraday(self):
        """Three sessions of five 1-hour bars with known extremes."""
        rows = []
        for day, base in (("2024-01-01", 100), ("2024-01-02", 110), ("2024-01-03", 120)):
            for i, hour in enumerate(("09:15", "10:15", "11:15", "12:15", "13:15")):
                rows.append({"Time": pd.Timestamp(f"{day} {hour}"), "Open": base + i, "High": base + i + 2,
                             "Low": base + i - 2, "Close": base + i + 1, "Volume": 1000})
        return pd.DataFrame(rows)

    def test_intraday_data_uses_the_previous_session(self):
        df = self.intraday()
        levels = pivot_points(df)
        self.assertTrue(levels["Pivot"].iloc[:5].isna().all(), "first session has no prior day")

        # Day 1: High = 100+4+2, Low = 100-2, Close = last bar's 100+4+1
        expected = (106 + 98 + 105) / 3
        day_two = levels["Pivot"].iloc[5:10]
        self.assertTrue(np.allclose(day_two, expected), "every bar of a session carries the same daily pivot")
        self.assertAlmostEqual(levels["Pivot"].iloc[10], (116 + 108 + 115) / 3)

    def test_daily_data_uses_the_previous_bar(self):
        df = make_frame(n=30)
        levels = pivot_points(df)
        self.assertTrue(pd.isna(levels["Pivot"].iloc[0]))
        expected = (df["High"].iloc[4] + df["Low"].iloc[4] + df["Close"].iloc[4]) / 3
        self.assertAlmostEqual(levels["Pivot"].iloc[5], expected)

    def test_explicit_anchors(self):
        df = self.intraday()
        self.assertAlmostEqual(pivot_points(df, anchor="bar")["Pivot"].iloc[1],
                               (df["High"].iloc[0] + df["Low"].iloc[0] + df["Close"].iloc[0]) / 3)
        self.assertTrue(pivot_points(df, anchor="W")["Pivot"].isna().all(), "all bars fall in one week")
        with self.assertRaises(ValueError):
            pivot_points(df, anchor="hourly")
        with self.assertRaises(ValueError):
            pivot_points(df.drop(columns="Time"), anchor="D")

    def test_works_from_a_datetime_index(self):
        df = self.intraday().set_index("Time")
        self.assertAlmostEqual(pivot_points(df)["Pivot"].iloc[5], (106 + 98 + 105) / 3)

    def test_cpr(self):
        df = self.intraday()
        levels = cpr(df)
        pivot, mid = (106 + 98 + 105) / 3, (106 + 98) / 2
        self.assertAlmostEqual(levels["CPR_Pivot"].iloc[5], pivot)
        self.assertAlmostEqual(levels["CPR_TC"].iloc[5], max(mid, 2 * pivot - mid))
        self.assertAlmostEqual(levels["CPR_BC"].iloc[5], min(mid, 2 * pivot - mid))
        body = levels.dropna()
        self.assertTrue((body["CPR_TC"] >= body["CPR_BC"]).all(), "TC is always the upper edge")
        self.assertTrue((body["CPR_Width"] >= 0).all())


class TestApiSurface(unittest.TestCase):
    def setUp(self):
        self.df = make_frame(n=150)
        self.api = IndicatorsAPI()

    def test_add_all_includes_the_new_indicators(self):
        added = set(self.api.add_all(self.df).columns) - set(self.df.columns)
        for column in ("MFI_14", "ROC_12", "KC_Upper", "CPR_TC"):
            self.assertIn(column, added)

    def test_add_by_alias(self):
        added = set(self.api.add(self.df, "mfi", "kc", "cpr").columns) - set(self.df.columns)
        self.assertTrue({"MFI_14", "KC_Middle", "CPR_Pivot"} <= added)

    def test_shared_keyword_reaches_every_indicator_that_takes_it(self):
        result = self.api.add(self.df, "rsi", "macd", period=21)
        self.assertIn("RSI_21", result.columns)       # macd has no `period`; it is simply not given one
        self.assertIn("MACD", result.columns)

    def test_a_typo_in_a_keyword_is_an_error_not_a_no_op(self):
        with self.assertRaises(TypeError):
            self.api.add(self.df, "rsi", perod=21)

    def test_crossover_length_mismatch_is_explained(self):
        with self.assertRaises(ValueError) as ctx:
            crossover(pd.Series([1, 2, 3, 4]), pd.Series([1, 2]))
        self.assertIn("equal length", str(ctx.exception))
        self.assertEqual(len(crossunder(pd.Series([], dtype=float), pd.Series([], dtype=float))), 0)


if __name__ == "__main__":
    unittest.main()
