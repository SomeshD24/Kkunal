import logging
import numbers
from typing import List, Optional, Tuple, Union, TYPE_CHECKING
from datetime import date, datetime, timedelta

from .constants import IST, normalize_resolution
from .exceptions import HistoricalDataError
from .indicators import INDICATOR_ALIASES, resolve_indicator
from ._responses import is_success, failure_message

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

CHART_ENDPOINT = "api/OpenGraph/ChartData"
# The API counts seconds from this instant in IST WALL-CLOCK terms, so this and the
# datetimes compared against it are deliberately naive. Timezone-aware inputs are
# converted to IST first (see _parse_date), never subtracted while still aware.
EPOCH_1980 = datetime(1980, 1, 1)
COLUMNS = ["Time", "Open", "High", "Low", "Close", "Volume", "OI"]

# Longest span requested in a single ChartData call, per interval. The endpoint
# caps how much intraday history one request may cover and answers an over-long
# range with an empty result rather than an error, so longer ranges are split
# into windows and stitched back together. Conservative on purpose.
MAX_SPAN_DAYS = {
    "1": 7,
    "5": 30,
    "10": 60,
    "15": 90,
    "30": 120,
    "60": 180,
    "D": 365,
    "W": 1825,
    "M": 3650,
}
_DEFAULT_SPAN_DAYS = 3650

DateLike = Union[str, int, date, datetime]


class HistoricalAPI:
    def __init__(self, client):
        self.client = client

    def _parse_date(self, date_val: DateLike, end_of_day: bool = False) -> int:
        """
        Converts a date to the API's epoch (seconds since 1980-01-01, IST wall-clock).

        Accepts an int (passed through unchanged), a date, a datetime, a pandas
        Timestamp, or a string in 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM[:SS]' form.
        Anything else raises ValueError rather than silently resolving to 1980-01-01.

        Args:
            end_of_day: Resolve a value that carries no time to 23:59:59 instead of
                00:00:00. Used for to_date, so that the final day's intraday candles
                are included rather than silently cut off at midnight.
        """
        if isinstance(date_val, bool):
            raise TypeError("from_date/to_date must be a date string, date, datetime or int")
        if isinstance(date_val, numbers.Integral):
            return int(date_val)

        dt: Optional[datetime] = None
        has_time = True
        if isinstance(date_val, datetime):          # also covers pandas.Timestamp
            # The API speaks IST wall-clock, so an aware value is converted, not just stripped.
            dt = date_val.astimezone(IST).replace(tzinfo=None) if date_val.tzinfo else date_val
            if hasattr(dt, "to_pydatetime"):
                dt = dt.to_pydatetime()
        elif isinstance(date_val, date):
            dt = datetime(date_val.year, date_val.month, date_val.day)
            has_time = False
        elif isinstance(date_val, str):
            text = date_val.strip()
            for fmt, carries_time in (("%Y-%m-%d %H:%M:%S", True), ("%Y-%m-%d %H:%M", True),
                                      ("%Y-%m-%dT%H:%M:%S", True), ("%Y-%m-%d", False)):
                try:
                    dt = datetime.strptime(text, fmt)
                    has_time = carries_time
                    break
                except ValueError:
                    continue

        if dt is None:
            raise ValueError(
                f"Could not parse date {date_val!r}. Expected 'YYYY-MM-DD', "
                f"'YYYY-MM-DD HH:MM:SS', a date/datetime, or an int epoch offset."
            )
        if end_of_day and not has_time:
            dt = dt + timedelta(days=1) - timedelta(seconds=1)
        return int((dt - EPOCH_1980).total_seconds())

    def get_by_symbol(
        self,
        symbol: str,
        from_date: DateLike,
        to_date: DateLike,
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
            symbol: Symbol or exact SecDesc, e.g. 'RELIANCE' or 'NIFTY26SEPFUT'.
            segment: Restrict the lookup to one segment when the symbol is listed
                in several (e.g. Segment.NSE_CASH).
            indicators: Same values accepted by get_historical_data_with_indicators.
                Left as None, plain OHLCV is returned.

        Raises:
            KeyError: if the symbol is not in the scrip master, or is ambiguous
                (AmbiguousSymbolError) - as it is for a derivative's underlying name.
        """
        segment_id, token = self.client.scrip_master.resolve(symbol, segment=segment)
        if indicators is None:
            return self.get_historical_data(segment_id, token, from_date, to_date, resolution)
        return self.get_historical_data_with_indicators(
            segment_id, token, from_date, to_date, resolution, indicators=indicators
        )

    # ------------------------------------------------------------------ fetch

    def _request_window(self, segment_id: int, token: int, start: int, end: int,
                        resolution: str) -> Tuple[List[str], float]:
        """One ChartData call. Returns (raw rows, price divisor) or raises."""
        payload = {
            "SegmentId": int(segment_id),
            "Token": int(token),
            "FromDate": start,
            "ToDate": end,
            "Interval": resolution
        }
        resp = self.client.request("POST", CHART_ENDPOINT, payload, retry=True)

        if not is_success(resp):
            raise HistoricalDataError(
                f"ChartData failed for token {token} (segment {segment_id}, "
                f"interval {resolution}): {failure_message(resp)}",
                response=resp, endpoint=CHART_ENDPOINT
            )

        # "Response" may be present but null.
        data = resp.get("Response") or {}
        if not isinstance(data, dict):
            raise HistoricalDataError(
                f"ChartData returned an unexpected Response type ({type(data).__name__})",
                response=resp, endpoint=CHART_ENDPOINT
            )

        try:
            divisor = float(data.get("PriceDivisor") or 1)
        except (TypeError, ValueError):
            divisor = 1.0
        if divisor <= 0:
            divisor = 1.0
        return list(data.get("lstChartHistory") or []), divisor

    @staticmethod
    def _parse_rows(rows: List[str], divisor: float) -> Tuple[List[list], int]:
        """
        Parses 'time,open,high,low,close,volume,oi' strings, tolerating bad rows.

        Volume and OI go through int(float(x)) because the API sends them as
        '1234.0' often enough that a bare int() would reject the whole batch.
        """
        parsed, bad = [], 0
        for row in rows:
            parts = str(row).split(',')
            if len(parts) < 5:
                bad += 1
                continue
            try:
                parsed.append([
                    int(float(parts[0])),
                    float(parts[1]) / divisor,
                    float(parts[2]) / divisor,
                    float(parts[3]) / divisor,
                    float(parts[4]) / divisor,
                    int(float(parts[5])) if len(parts) > 5 and parts[5].strip() else 0,
                    int(float(parts[6])) if len(parts) > 6 and parts[6].strip() else 0,
                ])
            except (TypeError, ValueError):
                bad += 1
        return parsed, bad

    def get_historical_data(
        self,
        segment_id: int,
        token: int,
        from_date: DateLike,
        to_date: DateLike,
        resolution: str,
        allow_partial: bool = True
    ) -> "pd.DataFrame":
        """
        Retrieves historical chart data (e.g., OHLCV) as a Pandas DataFrame.

        Ranges longer than the API serves in one call are fetched in windows and
        stitched together, so a multi-year intraday request just works. Rows come
        back sorted by time with duplicates removed. `Time` is IST wall-clock.

        Args:
            segment_id: Exchange Segment ID (e.g., 1 for NSE Cash).
            token: Instrument token.
            from_date: Start ('YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', date, datetime,
                or seconds from 1980).
            to_date: End, same formats. A value without a time means the END of
                that day, so its intraday candles are included.
            resolution: Interval - a Resolution constant, an API code ('1', '5',
                '10', '15', '30', '60', 'D', 'W', 'M') or an alias ('5m', '1h', 'daily').
            allow_partial: When a long range is fetched in several windows and
                only some fail, keep the rest and log a warning (the failures are
                listed in df.attrs['failed_windows']). False raises instead.

        Returns:
            DataFrame with Time, Open, High, Low, Close, Volume, OI. Empty (but with
            those columns) when the window genuinely contains no candles.

        Raises:
            HistoricalDataError: the API rejected the request - bad token,
                unsupported interval, expired session. Never returned as an
                empty frame, so "no data" and "request failed" stay distinguishable.
            ValueError: unparseable date, unsupported resolution, or from > to.
        """
        import pandas as pd

        resolution = normalize_resolution(resolution)
        start = self._parse_date(from_date)
        end = self._parse_date(to_date, end_of_day=True)
        if start > end:
            raise ValueError(f"from_date ({from_date!r}) is after to_date ({to_date!r})")

        span = MAX_SPAN_DAYS.get(resolution, _DEFAULT_SPAN_DAYS) * 86400
        windows = []
        cursor = start
        while cursor <= end:
            window_end = min(cursor + span, end)
            windows.append((cursor, window_end))
            cursor = window_end + 1

        rows: List[list] = []
        bad_rows = 0
        failures: List[Tuple[int, int, str]] = []
        for window_start, window_end in windows:
            try:
                raw, divisor = self._request_window(segment_id, token, window_start, window_end, resolution)
            except HistoricalDataError as e:
                if len(windows) == 1 or not allow_partial:
                    raise
                failures.append((window_start, window_end, str(e)))
                continue
            parsed, bad = self._parse_rows(raw, divisor)
            rows.extend(parsed)
            bad_rows += bad

        if failures and len(failures) == len(windows):
            raise HistoricalDataError(
                f"All {len(windows)} ChartData windows failed; first error: {failures[0][2]}",
                endpoint=CHART_ENDPOINT
            )

        if bad_rows:
            logger.warning("Skipped %d malformed candle row(s) for token %s", bad_rows, token)

        df = pd.DataFrame(rows, columns=COLUMNS)
        if df.empty:
            # Keep the schema, so `df["Close"]` works on a window with no candles.
            df = df.astype({"Open": float, "High": float, "Low": float, "Close": float,
                            "Volume": "int64", "OI": "int64"})
            df["Time"] = pd.to_datetime(df["Time"])
        else:
            df = (df.drop_duplicates(subset="Time", keep="last")
                    .sort_values("Time")
                    .reset_index(drop=True))
            df["Time"] = pd.to_datetime(df["Time"], unit='s', origin=pd.Timestamp('1980-01-01'))

        if failures:
            readable = [
                (str(EPOCH_1980 + timedelta(seconds=s)), str(EPOCH_1980 + timedelta(seconds=e)), msg)
                for s, e, msg in failures
            ]
            df.attrs["failed_windows"] = readable
            logger.warning("%d of %d ChartData windows failed for token %s; data has gaps: %s",
                           len(failures), len(windows), token, readable)
        return df

    def get_historical_data_with_indicators(
        self,
        segment_id: int,
        token: int,
        from_date: DateLike,
        to_date: DateLike,
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
        # Validate names before spending a network round trip on the candles.
        if isinstance(indicators, str) and indicators not in ("all", "core"):
            indicators = [indicators]
        if isinstance(indicators, (list, tuple)):
            unknown = [i for i in indicators if resolve_indicator(i) is None]
            if unknown:
                raise ValueError(
                    f"Unknown indicator(s): {unknown}. "
                    f"Available: {', '.join(sorted(INDICATOR_ALIASES))}"
                )

        df = self.get_historical_data(segment_id, token, from_date, to_date, resolution)
        if df.empty:
            return df

        ind_api = self.client.indicators
        if indicators == "all" or indicators is None:
            return ind_api.add_all(df)
        if indicators == "core":
            return ind_api.add_all(df, core_only=True)
        return ind_api.add(df, *indicators)
