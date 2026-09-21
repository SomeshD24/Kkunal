from typing import Dict, Any, Optional, Union, TYPE_CHECKING
from datetime import datetime

from .indicators import INDICATOR_ALIASES, resolve_indicator

if TYPE_CHECKING:
    import pandas as pd

class HistoricalAPI:
    def __init__(self, client):
        self.client = client
        
    def _parse_date(self, date_val: Union[str, int]) -> int:
        """
        Converts a date to the API's epoch (seconds since 1980-01-01).

        Accepts an int (passed through unchanged), a datetime, or a string in
        'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM:SS' form. Anything else raises
        ValueError rather than silently resolving to 1980-01-01.
        """
        if isinstance(date_val, bool):
            raise TypeError("from_date/to_date must be a date string, datetime or int")
        if isinstance(date_val, int):
            return date_val
        epoch_1980 = datetime(1980, 1, 1)
        if isinstance(date_val, datetime):
            return int((date_val - epoch_1980).total_seconds())
        try:
            if " " in date_val:
                dt = datetime.strptime(date_val, "%Y-%m-%d %H:%M:%S")
            else:
                dt = datetime.strptime(date_val, "%Y-%m-%d")
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Could not parse date {date_val!r}. Expected 'YYYY-MM-DD', "
                f"'YYYY-MM-DD HH:MM:SS', a datetime, or an int epoch offset."
            ) from e
        return int((dt - epoch_1980).total_seconds())
        
    def get_by_symbol(
        self,
        symbol: str,
        from_date: Union[str, int],
        to_date: Union[str, int],
        resolution: str = "D",
        segment: Optional[int] = None,
        indicators: Optional[Union[str, list]] = None
    ) -> "pd.DataFrame":
        """
        Fetches historical data by symbol, resolving the token via the Scrip Master.

        Example:
            >>> df = client.historical.get_by_symbol("RELIANCE", "2024-01-01", "2024-06-01")
            >>> df = client.historical.get_by_symbol("RELIANCE", "2024-01-01", "2024-06-01",
            ...                                      indicators=["rsi", "macd"])

        Args:
            symbol: Symbol or SecDesc, e.g. 'RELIANCE'.
            segment: Restrict the lookup to one segment when the symbol is listed
                in several (e.g. Segment.NSE_CASH).
            indicators: Same values accepted by get_historical_data_with_indicators.
                Left as None, plain OHLCV is returned.

        Raises:
            KeyError: if the symbol is not in the scrip master.
        """
        segment_id, token = self.client.scrip_master.resolve(symbol, segment=segment)
        if indicators is None:
            return self.get_historical_data(segment_id, token, from_date, to_date, resolution)
        return self.get_historical_data_with_indicators(
            segment_id, token, from_date, to_date, resolution, indicators=indicators
        )

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
            indicators: 'all' for every indicator, 'core' for the nine most common
                ones, or a list of names/shorthands e.g. ['rsi', 'macd', 'st', 'bb'].
                An unrecognised name raises ValueError rather than being skipped.

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
            if indicators == "core":
                return ind_api.add_all(df, core_only=True)

            if isinstance(indicators, str):
                indicators = [indicators]

            if isinstance(indicators, (list, tuple)):
                unknown = [i for i in indicators if resolve_indicator(i) is None]
                if unknown:
                    raise ValueError(
                        f"Unknown indicator(s): {unknown}. "
                        f"Available: {', '.join(sorted(INDICATOR_ALIASES))}"
                    )
                df = ind_api.add(df, *indicators)

        return df

