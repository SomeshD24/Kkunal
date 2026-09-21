import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from unittest import mock

from choice_api import AmbiguousSymbolError, ScripMaster, Segment
from choice_api.scrip_master import _parse_expiry


def expiry_code(d):
    return d.strftime("%d") + ("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()[d.month - 1]) + d.strftime("%y")


TODAY = date.today()
NEAR = TODAY + timedelta(days=5)
FAR = TODAY + timedelta(days=33)
PAST = TODAY - timedelta(days=9)

HEADER = "Token,Symbol,SecDesc,Segment,Exchange,Series,Instrument,Expiry,StrikePrice,OptionType,PriceDivisor,MarketLot\n"
ROWS = [
    "2885,RELIANCE,RELIANCE INDUSTRIES,1,NSE,EQ,,,,,100,1",
    "500325,RELIANCE,RELIANCE INDUSTRIES,3,BSE,A,,,,,100,1",
    f"48987,RELIANCE,RELIANCEFARFUT,2,NSEFO,XX,FUTSTK,{expiry_code(FAR)},-1,,100,500",
    "26000,NIFTY,Nifty 50,1,NSE,,,,,,100,1",
    f"60001,NIFTY,NIFTYPASTFUT,2,NSEFO,XX,FUTIDX,{expiry_code(PAST)},-1,,100,65",
    f"68407,NIFTY,NIFTYNEARFUT,2,NSEFO,XX,FUTIDX,{expiry_code(NEAR)},-1,,100,65",
    f"48704,NIFTY,NIFTYFARFUT,2,NSEFO,XX,FUTIDX,{expiry_code(FAR)},-1,,100,65",
    f"57082,NIFTY,NIFTYNEAR23750CE,2,NSEFO,XX,OPTIDX,{expiry_code(NEAR)},2375000,CE,100,65",
    f"57083,NIFTY,NIFTYNEAR23750PE,2,NSEFO,XX,OPTIDX,{expiry_code(NEAR)},2375000,PE,100,65",
    f"57090,NIFTY,NIFTYNEAR23800CE,2,NSEFO,XX,OPTIDX,{expiry_code(NEAR)},2380000,CE,100,65",
    f"51597,NIFTY,NIFTYFAR23750CE,2,NSEFO,XX,OPTIDX,{expiry_code(FAR)},2375000,CE,100,65",
    "318,SDL AP 7.0% 2039,SDL AP 7.0% 2039,1,NSE,SG,,,,,100,1",
    "6564,SDL AP 7.0% 2039,SDL AP 7.0% 2039,1,NSE,SG,,,,,100,1",
]


class ScripMasterTestCase(unittest.TestCase):
    def setUp(self):
        self.sm = ScripMaster()
        self.sm._ingest(HEADER + "\n".join(ROWS) + "\n", "01Jan2024")


class TestResolve(ScripMasterTestCase):
    def test_ingest_populates_lookups(self):
        self.assertTrue(self.sm.is_loaded)
        self.assertEqual(self.sm.loaded_date, "01Jan2024")
        self.assertEqual(self.sm.get_lot_size("48987"), 500)

    def test_cash_symbol_prefers_nse_then_bse(self):
        self.assertEqual(self.sm.resolve("RELIANCE"), (1, 2885))
        self.assertEqual(self.sm.resolve("reliance"), (1, 2885))

    def test_explicit_segment(self):
        self.assertEqual(self.sm.resolve("RELIANCE", segment=Segment.BSE_CASH), (3, 500325))

    def test_index_resolves_to_its_spot_row(self):
        self.assertEqual(self.sm.resolve("NIFTY"), (1, 26000))

    def test_exact_secdesc_resolves_a_contract(self):
        self.assertEqual(self.sm.resolve("NIFTYNEARFUT"), (2, 68407))

    def test_underlying_in_a_derivative_segment_is_never_guessed(self):
        """'NIFTY' in F&O matches many contracts; returning one at random would trade the wrong thing."""
        with self.assertRaises(AmbiguousSymbolError) as ctx:
            self.sm.resolve("NIFTY", segment=Segment.NSE_FO)
        self.assertIn("find_future", str(ctx.exception))
        self.assertTrue(ctx.exception.candidates)
        self.assertIsInstance(ctx.exception, KeyError)

    def test_single_derivative_in_segment_is_unambiguous(self):
        self.assertEqual(self.sm.resolve("RELIANCE", segment=Segment.NSE_FO), (2, 48987))

    def test_instruments_sharing_a_cash_symbol_are_ambiguous(self):
        with self.assertRaises(AmbiguousSymbolError):
            self.sm.resolve("SDL AP 7.0% 2039")

    def test_unknown_symbol_and_segment(self):
        with self.assertRaises(KeyError):
            self.sm.resolve("NOSUCHSYMBOL")
        with self.assertRaises(KeyError):
            self.sm.resolve("RELIANCE", segment=Segment.MCX)

    def test_unloaded_master_explains_why(self):
        with self.assertRaises(KeyError) as ctx:
            ScripMaster().resolve("RELIANCE")
        self.assertIn("not loaded", str(ctx.exception))


class TestDerivatives(ScripMasterTestCase):
    def test_find_future_defaults_to_the_nearest_unexpired(self):
        future = self.sm.find_future("NIFTY")
        self.assertEqual((future["Segment"], future["Token"], future["SecDesc"]), (2, 68407, "NIFTYNEARFUT"))
        self.assertEqual(future["Expiry"], NEAR)
        self.assertEqual(future["MarketLot"], 65)
        self.assertIsNone(future["StrikePrice"])
        self.assertIsInstance(future["Token"], int)

    def test_find_future_by_expiry_in_any_form(self):
        for form in (FAR, FAR.isoformat(), expiry_code(FAR)):
            with self.subTest(expiry=form):
                self.assertEqual(self.sm.find_future("NIFTY", expiry=form)["Token"], 48704)

    def test_unknown_expiry_lists_the_real_ones(self):
        with self.assertRaises(KeyError) as ctx:
            self.sm.find_future("NIFTY", expiry="2031-01-01")
        self.assertIn(NEAR.isoformat(), str(ctx.exception))

    def test_expiries(self):
        self.assertEqual(self.sm.expiries("NIFTY"), [NEAR, FAR])                       # options
        self.assertEqual(self.sm.expiries("NIFTY", options=False), [PAST, NEAR, FAR])  # futures

    def test_strikes_are_in_rupees(self):
        self.assertEqual(self.sm.strikes("NIFTY"), [23750.0, 23800.0])
        self.assertEqual(self.sm.strikes("NIFTY", option_type="PE"), [23750.0])

    def test_find_option(self):
        option = self.sm.find_option("NIFTY", 23750, "ce")
        self.assertEqual((option["Token"], option["OptionType"], option["StrikePrice"]), (57082, "CE", 23750.0))
        self.assertEqual(self.sm.find_option("NIFTY", 23750, "CE", expiry=FAR)["Token"], 51597)

    def test_missing_strike_suggests_the_nearest(self):
        with self.assertRaises(KeyError) as ctx:
            self.sm.find_option("NIFTY", 23760, "CE")
        self.assertIn("23750", str(ctx.exception))

    def test_bad_option_type(self):
        with self.assertRaises(ValueError):
            self.sm.find_option("NIFTY", 23750, "CALL")

    def test_option_chain_is_sorted(self):
        chain = self.sm.option_chain("NIFTY")
        self.assertEqual([(c["StrikePrice"], c["OptionType"]) for c in chain],
                         [(23750.0, "CE"), (23750.0, "PE"), (23800.0, "CE")])

    def test_symbol_without_derivatives(self):
        with self.assertRaises(KeyError):
            self.sm.find_future("SDL AP 7.0% 2039")

    def test_search_filters(self):
        self.assertEqual(len(self.sm.search("NIFTY", instrument="FUTIDX")), 3)
        self.assertEqual(len(self.sm.search("NIFTY", limit=2)), 2)
        self.assertEqual(len(self.sm.search("RELIANCE", segment=Segment.NSE_CASH)), 1)
        self.assertIn("Expiry", self.sm.search("NIFTY", limit=1)[0])

    def test_get_token_is_backward_compatible(self):
        self.assertEqual(self.sm.get_token("RELIANCE", segment="1"), "2885")
        self.assertEqual(self.sm.get_token("RELIANCE", segment=3), "500325")
        self.assertIsNone(self.sm.get_token("RELIANCE", segment="13"))
        self.assertEqual(len(self.sm.get_token("RELIANCE")), 3)


class TestExpiryParsing(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(_parse_expiry("29SEP26"), date(2026, 9, 29))
        self.assertEqual(_parse_expiry("29sep2026"), date(2026, 9, 29))
        self.assertEqual(_parse_expiry("2026-09-29"), date(2026, 9, 29))
        self.assertEqual(_parse_expiry(date(2026, 9, 29)), date(2026, 9, 29))
        for junk in ("", None, "31FEB26", "soon"):
            self.assertIsNone(_parse_expiry(junk))


class TestCache(unittest.TestCase):
    def test_write_is_atomic_and_old_files_are_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            sm = ScripMaster(cache_dir=d, cache_keep=2)
            for i, day in enumerate(("01Jan2024", "02Jan2024", "03Jan2024", "04Jan2024")):
                sm._write_cache(day, HEADER + ROWS[0] + "\n")
                os.utime(sm._cache_path(day), (time.time() + i, time.time() + i))
            sm._prune_cache()
            self.assertEqual(sorted(os.listdir(d)), ["SCRIP_MASTER_03Jan2024.csv", "SCRIP_MASTER_04Jan2024.csv"])
            self.assertFalse([f for f in os.listdir(d) if f.endswith(".tmp")])

    def test_the_file_just_written_survives_pruning_even_on_a_timestamp_tie(self):
        with tempfile.TemporaryDirectory() as d:
            sm = ScripMaster(cache_dir=d, cache_keep=1)
            sm._write_cache("01Jan2024", HEADER + ROWS[0])
            stamp = os.path.getmtime(sm._cache_path("01Jan2024")) + 50
            os.utime(sm._cache_path("01Jan2024"), (stamp, stamp))      # the older file looks NEWER
            sm._write_cache("02Jan2024", HEADER + ROWS[0])
            self.assertEqual(os.listdir(d), ["SCRIP_MASTER_02Jan2024.csv"])

    def test_cached_file_is_used_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            sm = ScripMaster(cache_dir=d)
            from choice_api.scrip_master import _MONTHS
            now = __import__("datetime").datetime.now()
            today = f"{now.day:02d}{list(_MONTHS)[now.month - 1].title()}{now.year}"
            sm._write_cache(today, HEADER + "\n".join(ROWS) + "\n")

            fresh = ScripMaster(cache_dir=d)
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                self.assertTrue(fresh.fetch())
            self.assertEqual(fresh.resolve("RELIANCE"), (1, 2885))

    def test_failed_ingest_keeps_the_previous_tables(self):
        sm = ScripMaster()
        sm._ingest(HEADER + ROWS[0] + "\n", "01Jan2024")
        self.assertEqual(sm._ingest("garbage-without-rows", "02Jan2024"), 0)
        self.assertEqual(sm.resolve("RELIANCE"), (1, 2885))
        self.assertEqual(sm.loaded_date, "01Jan2024")


if __name__ == "__main__":
    unittest.main()
