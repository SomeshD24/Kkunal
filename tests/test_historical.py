import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pandas as pd

from choice_api import ChoiceClient, HistoricalDataError, ScripMaster
from choice_api.historical import COLUMNS, EPOCH_1980, MAX_SPAN_DAYS


def epoch(text):
    return int((datetime.strptime(text, "%Y-%m-%d %H:%M:%S") - EPOCH_1980).total_seconds())


def candle(text, o=100.0, h=101.0, low=99.0, c=100.5, v="1500", oi="0", divisor=100):
    return f"{epoch(text)},{o * divisor:.0f},{h * divisor:.0f},{low * divisor:.0f},{c * divisor:.0f},{v},{oi}"


def success(rows, divisor=100):
    return {"Status": "Success", "Response": {"PriceDivisor": divisor, "lstChartHistory": rows}}


class HistoricalTestCase(unittest.TestCase):
    def setUp(self):
        self.client = ChoiceClient("vendor", "key-0123456789", max_retries=0)
        self.client.scrip_master = ScripMaster()
        self.requests = []

    def serve(self, *responses):
        """Queues API responses; records every ChartData payload that is sent."""
        queue = list(responses)

        def fake_request(method, endpoint, data=None, **kwargs):
            self.requests.append(data)
            response = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(response, Exception):
                raise response
            return response

        return mock.patch.object(self.client, "request", side_effect=fake_request)


class TestDateParsing(HistoricalTestCase):
    def test_accepted_forms(self):
        parse = self.client.historical._parse_date
        self.assertEqual(parse("2024-01-15"), epoch("2024-01-15 00:00:00"))
        self.assertEqual(parse("2024-01-15 09:15:00"), epoch("2024-01-15 09:15:00"))
        self.assertEqual(parse("2024-01-15 09:15"), epoch("2024-01-15 09:15:00"))
        self.assertEqual(parse(date(2024, 1, 15)), epoch("2024-01-15 00:00:00"))
        self.assertEqual(parse(datetime(2024, 1, 15, 9, 15)), epoch("2024-01-15 09:15:00"))
        self.assertEqual(parse(pd.Timestamp("2024-01-15 09:15")), epoch("2024-01-15 09:15:00"))
        self.assertEqual(parse(1389744000), 1389744000)

    def test_aware_datetimes_are_converted_to_ist(self):
        utc = datetime(2024, 1, 15, 3, 45, tzinfo=timezone.utc)            # 09:15 IST
        self.assertEqual(self.client.historical._parse_date(utc), epoch("2024-01-15 09:15:00"))

    def test_to_date_without_a_time_means_end_of_day(self):
        parse = self.client.historical._parse_date
        self.assertEqual(parse("2024-01-15", end_of_day=True), epoch("2024-01-15 23:59:59"))
        self.assertEqual(parse(date(2024, 1, 15), end_of_day=True), epoch("2024-01-15 23:59:59"))
        # An explicit time is respected as given.
        self.assertEqual(parse("2024-01-15 10:00:00", end_of_day=True), epoch("2024-01-15 10:00:00"))

    def test_garbage_raises_instead_of_meaning_1980(self):
        for bad in ("15-01-2024", "2024/01/15", "garbage", "", None, 3.5):
            with self.subTest(value=bad), self.assertRaises((ValueError, TypeError)):
                self.client.historical._parse_date(bad)
        with self.assertRaises(TypeError):
            self.client.historical._parse_date(True)


class TestFetch(HistoricalTestCase):
    def test_parses_candles_and_applies_the_divisor(self):
        rows = [candle("2024-01-15 09:15:00"), candle("2024-01-15 09:20:00", c=102.25)]
        with self.serve(success(rows)):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
        self.assertEqual(list(df.columns), COLUMNS)
        self.assertEqual(len(df), 2)
        self.assertEqual(df["Close"].iloc[1], 102.25)
        self.assertEqual(df["Time"].iloc[0], pd.Timestamp("2024-01-15 09:15:00"))
        self.assertEqual(df["Volume"].iloc[0], 1500)

    def test_last_day_is_included(self):
        """to_date='2024-01-15' must cover that day's session, not stop at its midnight."""
        with self.serve(success([])):
            self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
        self.assertEqual(self.requests[0]["ToDate"], epoch("2024-01-15 23:59:59"))

    def test_failure_raises_instead_of_returning_an_empty_frame(self):
        with self.serve({"Status": "Fail", "Reason": "Invalid token"}):
            with self.assertRaises(HistoricalDataError) as ctx:
                self.client.historical.get_historical_data(1, 999999, "2024-01-01", "2024-01-31", "D")
        self.assertIn("Invalid token", str(ctx.exception))

    def test_genuinely_empty_window_is_an_empty_frame_with_columns(self):
        for body in (success([]), {"Status": "Success", "Response": None}):
            with self.subTest(body=body), self.serve(body):
                df = self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-05", "D")
            self.assertTrue(df.empty)
            self.assertEqual(list(df.columns), COLUMNS)

    def test_decimal_volume_does_not_kill_the_batch(self):
        rows = [candle("2024-01-15 09:15:00", v="1234.0", oi="77.0"), candle("2024-01-15 09:20:00")]
        with self.serve(success(rows)):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
        self.assertEqual((df["Volume"].iloc[0], df["OI"].iloc[0]), (1234, 77))

    def test_malformed_rows_are_skipped_and_reported(self):
        rows = [candle("2024-01-15 09:15:00"), "not,a,candle", "", candle("2024-01-15 09:20:00")]
        with self.serve(success(rows)), self.assertLogs("choice_api.historical", level="WARNING"):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
        self.assertEqual(len(df), 2)

    def test_rows_without_volume_default_to_zero(self):
        short = ",".join(candle("2024-01-15 09:15:00").split(",")[:5])
        with self.serve(success([short])):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
        self.assertEqual((df["Volume"].iloc[0], df["OI"].iloc[0]), (0, 0))

    def test_output_is_sorted_and_deduplicated(self):
        rows = [candle("2024-01-15 09:25:00", c=3), candle("2024-01-15 09:15:00", c=1),
                candle("2024-01-15 09:20:00", c=2), candle("2024-01-15 09:15:00", c=1)]
        with self.serve(success(rows)):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
        self.assertEqual(list(df["Close"]), [1.0, 2.0, 3.0])
        self.assertTrue(df["Time"].is_monotonic_increasing)

    def test_bad_divisor_falls_back_to_one(self):
        for divisor in (0, None, "abc", -5):
            with self.subTest(divisor=divisor), self.serve(success([candle("2024-01-15 09:15:00", divisor=1)], divisor)):
                df = self.client.historical.get_historical_data(1, 2885, "2024-01-15", "2024-01-15", "5")
            self.assertEqual(df["Close"].iloc[0], 100.0)       # 100.5 rounded by the helper's :.0f

    def test_unsupported_resolution_fails_before_any_request(self):
        with self.serve(success([])):
            with self.assertRaises(ValueError):
                self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-05", "3")
        self.assertEqual(self.requests, [])

    def test_from_after_to_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.historical.get_historical_data(1, 2885, "2024-02-01", "2024-01-01", "D")

    def test_aliases_are_normalised(self):
        with self.serve(success([])):
            self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-02", "5m")
        self.assertEqual(self.requests[0]["Interval"], "5")


class TestChunking(HistoricalTestCase):
    def test_long_ranges_are_split_into_contiguous_windows(self):
        with self.serve(success([])):
            self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-03-31", "1")
        span = MAX_SPAN_DAYS["1"] * 86400
        self.assertEqual(len(self.requests), 13)                         # 91 days / 7
        self.assertEqual(self.requests[0]["FromDate"], epoch("2024-01-01 00:00:00"))
        self.assertEqual(self.requests[-1]["ToDate"], epoch("2024-03-31 23:59:59"))
        for earlier, later in zip(self.requests, self.requests[1:]):
            self.assertEqual(later["FromDate"], earlier["ToDate"] + 1)   # no gap, no overlap
            self.assertLessEqual(earlier["ToDate"] - earlier["FromDate"], span)

    def test_short_range_is_a_single_request(self):
        with self.serve(success([])):
            self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-06-30", "D")
        self.assertEqual(len(self.requests), 1)

    def test_windows_are_stitched_together(self):
        first = success([candle("2024-01-02 09:15:00", c=1)])
        second = success([candle("2024-01-09 09:15:00", c=2)])
        with self.serve(first, second):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-10", "1")
        self.assertEqual(list(df["Close"]), [1.0, 2.0])

    def test_partial_failure_keeps_the_rest_and_says_so(self):
        good = success([candle("2024-01-02 09:15:00")])
        bad = {"Status": "Fail", "Reason": "throttled"}
        with self.serve(good, bad), self.assertLogs("choice_api.historical", level="WARNING"):
            df = self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-10", "1")
        self.assertEqual(len(df), 1)
        self.assertEqual(len(df.attrs["failed_windows"]), 1)
        self.assertIn("throttled", df.attrs["failed_windows"][0][2])

    def test_allow_partial_false_raises(self):
        good = success([candle("2024-01-02 09:15:00")])
        with self.serve(good, {"Status": "Fail", "Reason": "throttled"}):
            with self.assertRaises(HistoricalDataError):
                self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-10", "1",
                                                           allow_partial=False)

    def test_every_window_failing_raises(self):
        with self.serve({"Status": "Fail", "Reason": "Invalid token"}):
            with self.assertRaises(HistoricalDataError):
                self.client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-01-20", "1")


class TestIndicatorsOnFetch(HistoricalTestCase):
    def rows(self, n=80):
        start = datetime(2024, 1, 1)
        return [candle((start + timedelta(days=i)).strftime("%Y-%m-%d 00:00:00"), c=100 + i % 7) for i in range(n)]

    def test_unknown_indicator_fails_before_the_network_call(self):
        with self.serve(success(self.rows())):
            with self.assertRaises(ValueError):
                self.client.historical.get_historical_data_with_indicators(
                    1, 2885, "2024-01-01", "2024-03-31", "D", indicators=["rsi", "nope"])
        self.assertEqual(self.requests, [])

    def test_named_indicators_are_applied(self):
        with self.serve(success(self.rows())):
            df = self.client.historical.get_historical_data_with_indicators(
                1, 2885, "2024-01-01", "2024-03-31", "D", indicators=["rsi", "bb"])
        self.assertIn("RSI_14", df.columns)
        self.assertIn("BB_Upper", df.columns)

    def test_get_by_symbol_resolves_the_token(self):
        self.client.scrip_master._ingest(
            "Token,Symbol,SecDesc,Segment,Exchange,Series,Instrument,Expiry,StrikePrice,OptionType,PriceDivisor,MarketLot\n"
            "2885,RELIANCE,RELIANCE INDUSTRIES,1,NSE,EQ,,,,,100,1\n", "01Jan2024")
        with self.serve(success(self.rows())):
            self.client.historical.get_by_symbol("RELIANCE", "2024-01-01", "2024-03-31")
        self.assertEqual((self.requests[0]["SegmentId"], self.requests[0]["Token"]), (1, 2885))


if __name__ == "__main__":
    unittest.main()
