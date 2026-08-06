import unittest
import numpy as np
import pandas as pd

from choice_api.indicators import (
    sma, ema, dema, tema, wma, rsi, macd, bollinger_bands,
    atr, supertrend, adx, stochastic, cci, williams_r,
    vwap, obv, parabolic_sar, ichimoku, donchian_channel,
    heikin_ashi, pivot_points, crossover, crossunder,
    IndicatorsAPI, _validate_period
)
from choice_api.client import ChoiceClient


class TestHelpers(unittest.TestCase):
    """Tests for validation helpers and signal utilities."""

    def test_validate_period_valid(self):
        _validate_period(1)
        _validate_period(100)

    def test_validate_period_zero(self):
        with self.assertRaises(ValueError):
            _validate_period(0)

    def test_validate_period_negative(self):
        with self.assertRaises(ValueError):
            _validate_period(-5)

    def test_validate_period_float(self):
        with self.assertRaises(ValueError):
            _validate_period(3.5)

    def test_crossover(self):
        a = pd.Series([1, 2, 3, 4, 5])
        b = pd.Series([3, 3, 3, 3, 3])
        result = crossover(a, b)
        # a crosses above b between index 2 (a=3, b=3) and index 3 (a=4 > b=3, prev a=3 <= b=3)
        self.assertTrue(result.iloc[3])
        self.assertFalse(result.iloc[0])

    def test_crossunder(self):
        a = pd.Series([5, 4, 3, 2, 1])
        b = pd.Series([3, 3, 3, 3, 3])
        result = crossunder(a, b)
        # a crosses below b between index 2 (a=3, b=3) and index 3 (a=2 < b=3, prev a=3 >= b=3)
        self.assertTrue(result.iloc[3])
        self.assertFalse(result.iloc[0])


class TestIndicators(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range(start="2026-01-01", periods=60, freq="D")
        np.random.seed(42)
        close_prices = 100.0 + np.cumsum(np.random.randn(60) * 2.0)
        high_prices = close_prices + np.abs(np.random.randn(60)) + 1.0
        low_prices = close_prices - np.abs(np.random.randn(60)) - 1.0
        open_prices = low_prices + (high_prices - low_prices) * 0.5
        volume = np.random.randint(1000, 10000, size=60)

        self.df = pd.DataFrame({
            "Time": dates,
            "Open": open_prices,
            "High": high_prices,
            "Low": low_prices,
            "Close": close_prices,
            "Volume": volume
        })
        self.ind_api = IndicatorsAPI()

    # --- Moving Averages ---
    def test_sma(self):
        result = sma(self.df, period=10)
        expected_last = self.df["Close"].iloc[-10:].mean()
        self.assertAlmostEqual(result.iloc[-1], expected_last, places=5)
        self.assertEqual(len(result), 60)

    def test_ema(self):
        result = ema(self.df, period=10)
        self.assertEqual(len(result), 60)
        self.assertFalse(np.isnan(result.iloc[-1]))

    def test_dema(self):
        result = dema(self.df, period=10)
        self.assertEqual(len(result), 60)
        self.assertEqual(result.name, "DEMA_10")

    def test_tema(self):
        result = tema(self.df, period=10)
        self.assertEqual(len(result), 60)
        self.assertEqual(result.name, "TEMA_10")

    def test_wma(self):
        result = wma(self.df, period=5)
        last_5 = self.df["Close"].iloc[-5:].values
        expected_last = np.dot(last_5, np.array([1, 2, 3, 4, 5])) / 15.0
        self.assertAlmostEqual(result.iloc[-1], expected_last, places=5)

    # --- Momentum ---
    def test_rsi(self):
        result = rsi(self.df, period=14)
        self.assertEqual(len(result), 60)
        valid_rsi = result.dropna()
        self.assertTrue((valid_rsi >= 0).all() and (valid_rsi <= 100).all())

    def test_macd(self):
        result = macd(self.df)
        self.assertIn("MACD", result.columns)
        self.assertIn("MACD_Signal", result.columns)
        self.assertIn("MACD_Hist", result.columns)
        self.assertTrue(np.allclose(
            result["MACD_Hist"].values,
            (result["MACD"] - result["MACD_Signal"]).values,
            atol=1e-10
        ))

    def test_stochastic(self):
        result = stochastic(self.df, k_period=14, d_period=3)
        self.assertIn("Stoch_K", result.columns)
        self.assertIn("Stoch_D", result.columns)

    def test_cci(self):
        result = cci(self.df, period=20)
        self.assertEqual(len(result), 60)
        self.assertEqual(result.name, "CCI_20")

    def test_williams_r(self):
        result = williams_r(self.df, period=14)
        self.assertEqual(len(result), 60)
        valid = result.dropna()
        self.assertTrue((valid >= -100).all() and (valid <= 0).all())

    # --- Volatility ---
    def test_bollinger_bands(self):
        result = bollinger_bands(self.df, period=20, std_dev=2.0)
        self.assertTrue((result["BB_Upper"] >= result["BB_Middle"] - 1e-10).all())
        self.assertTrue((result["BB_Middle"] >= result["BB_Lower"] - 1e-10).all())
        self.assertIn("BB_PercentB", result.columns)

    def test_atr(self):
        result = atr(self.df, period=14)
        valid_atr = result.dropna()
        self.assertTrue((valid_atr > 0).all())

    def test_donchian_channel(self):
        result = donchian_channel(self.df, period=20)
        self.assertIn("Donchian_Upper", result.columns)
        self.assertIn("Donchian_Middle", result.columns)
        self.assertIn("Donchian_Lower", result.columns)
        self.assertTrue((result["Donchian_Upper"] >= result["Donchian_Lower"]).all())

    # --- Trend ---
    def test_supertrend(self):
        result = supertrend(self.df, period=10, multiplier=3.0)
        self.assertIn("Supertrend", result.columns)
        self.assertIn("Supertrend_Direction", result.columns)
        directions = result["Supertrend_Direction"].unique()
        for d in directions:
            self.assertIn(d, [0, 1, -1])

    def test_adx(self):
        result = adx(self.df, period=14)
        self.assertIn("ADX", result.columns)
        self.assertIn("Plus_DI", result.columns)
        self.assertIn("Minus_DI", result.columns)

    def test_parabolic_sar(self):
        result = parabolic_sar(self.df)
        self.assertIn("PSAR", result.columns)
        self.assertIn("PSAR_Trend", result.columns)
        trends = result["PSAR_Trend"].unique()
        for t in trends:
            self.assertIn(t, [1, -1])

    def test_ichimoku(self):
        result = ichimoku(self.df)
        self.assertIn("Tenkan_Sen", result.columns)
        self.assertIn("Kijun_Sen", result.columns)
        self.assertIn("Senkou_Span_A", result.columns)
        self.assertIn("Senkou_Span_B", result.columns)
        self.assertIn("Chikou_Span", result.columns)

    # --- Volume ---
    def test_vwap(self):
        result = vwap(self.df)
        self.assertEqual(len(result), 60)

    def test_obv(self):
        result = obv(self.df)
        self.assertEqual(len(result), 60)
        self.assertEqual(result.iloc[0], 0)

    # --- Candle Transformations & Levels ---
    def test_heikin_ashi(self):
        result = heikin_ashi(self.df)
        self.assertIn("HA_Open", result.columns)
        self.assertIn("HA_High", result.columns)
        self.assertIn("HA_Low", result.columns)
        self.assertIn("HA_Close", result.columns)
        # HA_High >= HA_Low
        self.assertTrue((result["HA_High"] >= result["HA_Low"] - 1e-10).all())

    def test_pivot_points_standard(self):
        result = pivot_points(self.df, method="standard")
        self.assertIn("Pivot", result.columns)
        self.assertIn("R1", result.columns)
        self.assertIn("S1", result.columns)
        # R1 > S1 (support is below resistance)
        self.assertTrue((result["R1"] >= result["S1"] - 1e-10).all())

    def test_pivot_points_fibonacci(self):
        result = pivot_points(self.df, method="fibonacci")
        self.assertIn("R2", result.columns)

    def test_pivot_points_camarilla(self):
        result = pivot_points(self.df, method="camarilla")
        self.assertIn("R3", result.columns)

    def test_pivot_points_invalid_method(self):
        with self.assertRaises(ValueError):
            pivot_points(self.df, method="invalid")

    # --- Integration ---
    def test_indicators_api_add_all(self):
        res_df = self.ind_api.add_all(self.df)
        expected_cols = ["SMA_20", "EMA_20", "RSI_14", "MACD", "BB_Upper",
                         "BB_PercentB", "ATR_14", "Supertrend", "VWAP", "OBV"]
        for col in expected_cols:
            self.assertIn(col, res_df.columns)

    def test_add_all_custom_periods(self):
        res_df = self.ind_api.add_all(self.df, sma_period=50, ema_period=50, rsi_period=21)
        self.assertIn("SMA_50", res_df.columns)
        self.assertIn("EMA_50", res_df.columns)
        self.assertIn("RSI_21", res_df.columns)

    def test_original_df_not_mutated(self):
        self.ind_api.add_all(self.df)
        self.assertNotIn("RSI_14", self.df.columns)

    def test_choice_client_integration(self):
        client = ChoiceClient(vendor_id="TEST", api_key="TEST")
        self.assertTrue(hasattr(client, "indicators"))
        df_with_rsi = client.indicators.add_rsi(self.df)
        self.assertIn("RSI_14", df_with_rsi.columns)


if __name__ == "__main__":
    unittest.main()
