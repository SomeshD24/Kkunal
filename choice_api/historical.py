from typing import Dict, Any, Optional, Union, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    import pandas as pd

class HistoricalAPI:
    def __init__(self, client):
        self.client = client
        
    def _parse_date(self, date_val: Union[str, int]) -> int:
        if isinstance(date_val, int):
            return date_val
        epoch_1980 = datetime(1980, 1, 1)
        try:
            if " " in date_val:
                dt = datetime.strptime(date_val, "%Y-%m-%d %H:%M:%S")
            else:
                dt = datetime.strptime(date_val, "%Y-%m-%d")
            return int((dt - epoch_1980).total_seconds())
        except Exception:
            return 0
        
    def get_historical_data(self, segment_id: int, token: int, from_date: Union[str, int], to_date: Union[str, int], resolution: str) -> "pd.DataFrame":
        """
        Retrieves historical chart data (e.g., OHLCV) as a Pandas DataFrame.
        
        Args:
            segment_id: Exchange Segment ID (e.g., 1 for NSE Cash).
            token: Instrument token.
            from_date: Start date (format 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' or seconds from 1980).
            to_date: End date (format 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' or seconds from 1980).
            resolution: Timeframe (e.g., '1', '5', 'D').
        """
        import pandas as pd
        
        payload = {
            "SegmentId": segment_id,
            "Token": token,
            "FromDate": self._parse_date(from_date),
            "ToDate": self._parse_date(to_date),
            "Interval": resolution
        }
        
        resp = self.client.request("POST", "api/OpenGraph/ChartData", payload)
        
        if resp.get("Status") == "Success":
            data = resp.get("Response", {})
            history = data.get("lstChartHistory", [])
            divisor = data.get("PriceDivisor", 1)
            
            if not history:
                return pd.DataFrame()
                
            parsed_data = []
            for row in history:
                parts = row.split(',')
                # Usually: Time, Open, High, Low, Close, Volume, OI
                if len(parts) >= 7:
                    parsed_data.append([
                        int(parts[0]),
                        float(parts[1]) / divisor if divisor else float(parts[1]),
                        float(parts[2]) / divisor if divisor else float(parts[2]),
                        float(parts[3]) / divisor if divisor else float(parts[3]),
                        float(parts[4]) / divisor if divisor else float(parts[4]),
                        int(parts[5]),
                        int(parts[6])
                    ])
                else:
                    parsed_row = [float(p) / divisor if idx in (1,2,3,4) else float(p) for idx, p in enumerate(parts)]
                    parsed_data.append(parsed_row)
            
            columns = ["Time", "Open", "High", "Low", "Close", "Volume", "OI"]
            if parsed_data and len(parsed_data[0]) <= len(columns):
                df = pd.DataFrame(parsed_data, columns=columns[:len(parsed_data[0])])
            else:
                df = pd.DataFrame(parsed_data)
                
            if "Time" in df.columns:
                df["Time"] = pd.to_datetime(df["Time"], unit='s', origin=pd.Timestamp('1980-01-01'))
                
            return df
        
        # If not successful, return empty dataframe or raise? 
        # Return empty DataFrame to maintain return type consistency, 
        # as self.client.request raises Exception for HTTP errors.
        return pd.DataFrame()

    def get_historical_data_with_indicators(
        self,
        segment_id: int,
        token: int,
        from_date: Union[str, int],
        to_date: Union[str, int],
        resolution: str,
        indicators: Optional[Union[str, list]] = "all"
    ) -> "pd.DataFrame":
        """
        Retrieves historical chart data and automatically computes specified technical indicators.

        Args:
            segment_id: Exchange Segment ID (e.g., 1 for NSE Cash).
            token: Instrument token.
            from_date: Start date.
            to_date: End date.
            resolution: Timeframe (e.g., '1', '5', 'D').
            indicators: 'all' or list of indicator names, e.g., ['rsi', 'macd', 'supertrend', 'bb']

        Returns:
            Pandas DataFrame containing OHLCV and requested technical indicator columns.
        """
        df = self.get_historical_data(segment_id, token, from_date, to_date, resolution)
        if df.empty:
            return df

        if hasattr(self.client, "indicators"):
            ind_api = self.client.indicators
            if indicators == "all" or indicators is None:
                return ind_api.add_all(df)

            if isinstance(indicators, list):
                for ind in indicators:
                    ind_lower = str(ind).lower()
                    if ind_lower in ("rsi",):
                        df = ind_api.add_rsi(df)
                    elif ind_lower in ("macd",):
                        df = ind_api.add_macd(df)
                    elif ind_lower in ("sma",):
                        df = ind_api.add_sma(df)
                    elif ind_lower in ("ema",):
                        df = ind_api.add_ema(df)
                    elif ind_lower in ("dema",):
                        df = ind_api.add_dema(df)
                    elif ind_lower in ("tema",):
                        df = ind_api.add_tema(df)
                    elif ind_lower in ("wma",):
                        df = ind_api.add_wma(df)
                    elif ind_lower in ("bb", "bollinger", "bollinger_bands"):
                        df = ind_api.add_bollinger_bands(df)
                    elif ind_lower in ("atr",):
                        df = ind_api.add_atr(df)
                    elif ind_lower in ("supertrend", "st"):
                        df = ind_api.add_supertrend(df)
                    elif ind_lower in ("adx",):
                        df = ind_api.add_adx(df)
                    elif ind_lower in ("stoch", "stochastic"):
                        df = ind_api.add_stochastic(df)
                    elif ind_lower in ("cci",):
                        df = ind_api.add_cci(df)
                    elif ind_lower in ("williams_r", "williams", "wr"):
                        df = ind_api.add_williams_r(df)
                    elif ind_lower in ("vwap",):
                        df = ind_api.add_vwap(df)
                    elif ind_lower in ("obv",):
                        df = ind_api.add_obv(df)
                    elif ind_lower in ("psar", "parabolic_sar", "parabolic"):
                        df = ind_api.add_parabolic_sar(df)
                    elif ind_lower in ("ichimoku", "ichi"):
                        df = ind_api.add_ichimoku(df)
                    elif ind_lower in ("donchian", "donchian_channel", "dc"):
                        df = ind_api.add_donchian_channel(df)
                    elif ind_lower in ("ha", "heikin_ashi", "heikinashi"):
                        df = ind_api.add_heikin_ashi(df)
                    elif ind_lower in ("pivot", "pivot_points", "pp"):
                        df = ind_api.add_pivot_points(df)

        return df

