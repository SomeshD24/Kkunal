"""
Quick Start Demo - Testing Technical Indicators with choice_api (kkunal)
Run this script using: python example_demo.py
"""

import pandas as pd
import numpy as np
from choice_api import (
    ChoiceClient,
    IndicatorsAPI,
    rsi,
    macd,
    supertrend,
    bollinger_bands,
    sma,
    ema
)

def main():
    print("=" * 60)
    print("  Kkunal (choice_api) - Technical Indicators Demo")
    print("=" * 60)

    # 1. Generate sample 30-day OHLCV market data
    print("\n[Step 1] Generating sample OHLCV data...")
    dates = pd.date_range(start="2026-01-01", periods=30, freq="D")
    np.random.seed(42)
    close_prices = 2500.0 + np.cumsum(np.random.randn(30) * 15.0)
    high_prices = close_prices + np.random.uniform(5.0, 20.0, 30)
    low_prices = close_prices - np.random.uniform(5.0, 20.0, 30)
    open_prices = low_prices + (high_prices - low_prices) * 0.5
    volume = np.random.randint(50000, 200000, size=30)

    df = pd.DataFrame({
        "Time": dates,
        "Open": np.round(open_prices, 2),
        "High": np.round(high_prices, 2),
        "Low": np.round(low_prices, 2),
        "Close": np.round(close_prices, 2),
        "Volume": volume
    })

    print("\nInitial Raw DataFrame (First 5 Rows):")
    print(df.head())

    # 2. Method 1: Using client.indicators.add_all()
    print("\n" + "-" * 60)
    print("[Step 2] Calculating indicators using IndicatorsAPI (add_all)...")
    ind_api = IndicatorsAPI()
    df_with_indicators = ind_api.add_all(df)

    print("\nDataFrame with Technical Indicators (Last 5 Rows):")
    cols_to_show = ["Time", "Close", "SMA_20", "EMA_20", "RSI_14", "MACD", "Supertrend", "BB_Upper", "VWAP"]
    print(df_with_indicators[cols_to_show].tail())

    # 3. Method 2: Using standalone functions
    print("\n" + "-" * 60)
    print("[Step 3] Calculating standalone RSI & Supertrend...")
    rsi_14 = rsi(df, period=14)
    st_df = supertrend(df, period=10, multiplier=3.0)

    latest_close = df['Close'].iloc[-1]
    latest_rsi = rsi_14.iloc[-1]
    latest_st = st_df['Supertrend'].iloc[-1]
    latest_st_dir = "[BULLISH]" if st_df['Supertrend_Direction'].iloc[-1] == 1 else "[BEARISH]"


    print(f"Latest Close Price:  Rs. {latest_close:.2f}")
    print(f"Latest RSI (14):     {latest_rsi:.2f}")
    print(f"Latest Supertrend:   Rs. {latest_st:.2f} ({latest_st_dir})")

    print("=" * 60)
    print("\nAll indicators calculated cleanly without error!")

if __name__ == "__main__":
    main()
