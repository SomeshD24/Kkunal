import json
import os
import tempfile
import unittest
from unittest import mock

import requests

from choice_api import (
    ChoiceClient,
    Segment,
    Side,
    Validity,
    OrderType,
    ProductType,
    Resolution,
    to_paisa,
    to_rupees,
    ChoiceAPIError,
    AuthenticationError,
    APIResponseError,
    NetworkError,
    InvalidResponseError,
    ScripMaster,
)


def fake_response(status_code=200, payload=None, text=None):
    """Builds a stand-in for requests.Response covering only what request() touches."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.text = text if text is not None else json.dumps(payload or {})
    if payload is None and text is not None:
        resp.json.side_effect = json.JSONDecodeError("bad", text, 0)
    else:
        resp.json.return_value = payload or {}
    return resp


class TestConstants(unittest.TestCase):
    def test_enums_are_ints(self):
        self.assertEqual(int(Segment.NSE_CASH), 1)
        self.assertEqual(int(Segment.NSE_FO), 2)
        self.assertEqual(int(Side.BUY), 1)
        self.assertEqual(int(Side.SELL), 2)
        self.assertEqual(int(Validity.DAY), 1)

    def test_str_constants_pass_through(self):
        self.assertEqual(OrderType.LIMIT, "RL_LIMIT")
        self.assertEqual(ProductType.DELIVERY, "D")
        self.assertEqual(Resolution.DAY, "D")
        self.assertEqual(Resolution.MIN_5, "5")
        # They must serialise as plain strings in a JSON payload.
        self.assertEqual(json.dumps({"t": OrderType.LIMIT}), '{"t": "RL_LIMIT"}')

    def test_paisa_conversion(self):
        self.assertEqual(to_paisa(1300.50), 130050)
        self.assertEqual(to_paisa(1300), 130000)
        self.assertEqual(to_rupees(130050), 1300.50)
        self.assertEqual(to_rupees(to_paisa(999.99)), 999.99)


class TestExceptions(unittest.TestCase):
    def setUp(self):
        self.client = ChoiceClient("vendor", "key")

    def test_401_raises_authentication_error(self):
        with mock.patch.object(requests, "request", return_value=fake_response(401, {})):
            with self.assertRaises(AuthenticationError):
                self.client.request("GET", "api/OpenAPI/FundsView")

    def test_500_raises_api_response_error(self):
        with mock.patch.object(requests, "request", return_value=fake_response(500, {})):
            with self.assertRaises(APIResponseError) as ctx:
                self.client.request("GET", "api/OpenAPI/FundsView")
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.endpoint, "api/OpenAPI/FundsView")

    def test_transport_failure_raises_network_error(self):
        with mock.patch.object(requests, "request",
                               side_effect=requests.exceptions.ConnectTimeout("timed out")):
            with self.assertRaises(NetworkError):
                self.client.request("GET", "api/OpenAPI/FundsView")

    def test_non_json_body_raises_invalid_response(self):
        with mock.patch.object(requests, "request",
                               return_value=fake_response(200, None, text="<html>nope</html>")):
            with self.assertRaises(InvalidResponseError):
                self.client.request("GET", "api/OpenAPI/FundsView")

    def test_all_derive_from_base(self):
        for exc in (AuthenticationError, APIResponseError, NetworkError, InvalidResponseError):
            self.assertTrue(issubclass(exc, ChoiceAPIError))

    def test_timeout_is_passed_through(self):
        with mock.patch.object(requests, "request", return_value=fake_response(200, {"ok": 1})) as m:
            self.client.request("GET", "api/OpenAPI/FundsView")
        self.assertEqual(m.call_args.kwargs["timeout"], 30.0)

        client = ChoiceClient("v", "k", timeout=5)
        with mock.patch.object(requests, "request", return_value=fake_response(200, {"ok": 1})) as m:
            client.request("GET", "api/OpenAPI/FundsView")
        self.assertEqual(m.call_args.kwargs["timeout"], 5)


class TestClientErgonomics(unittest.TestCase):
    def test_is_authenticated(self):
        client = ChoiceClient("v", "k")
        self.assertFalse(client.is_authenticated)
        client.session_id = "abc"
        self.assertTrue(client.is_authenticated)

    def test_repr_hides_no_secrets(self):
        client = ChoiceClient("vendor", "supersecretkey")
        self.assertNotIn("supersecretkey", repr(client))
        self.assertIn("not logged in", repr(client))

    def test_context_manager_logs_off(self):
        client = ChoiceClient("v", "k")
        client.session_id = "abc"
        with mock.patch.object(client, "logoff") as logoff:
            with client:
                pass
        logoff.assert_called_once()

    def test_context_manager_skips_logoff_when_not_logged_in(self):
        client = ChoiceClient("v", "k")
        with mock.patch.object(client, "logoff") as logoff:
            with client:
                pass
        logoff.assert_not_called()

    def test_session_roundtrip(self):
        client = ChoiceClient("v", "k")
        client.session_id = "sess-1"
        client.access_token = "tok-1"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            self.assertTrue(client.save_session(path))

            restored = ChoiceClient("v", "k")
            with mock.patch.object(restored.scrip_master, "fetch", return_value=True):
                self.assertTrue(restored.load_session(path))
            self.assertEqual(restored.session_id, "sess-1")
            self.assertEqual(restored.access_token, "tok-1")

    def test_login_reuses_saved_session(self):
        client = ChoiceClient("v", "k")
        client.session_id = "sess-1"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            client.save_session(path)

            fresh = ChoiceClient("v", "k")
            with mock.patch.object(fresh.scrip_master, "fetch", return_value=True), \
                 mock.patch.object(requests, "request") as req:
                session_id = fresh.login("9999999999", session_file=path)
            self.assertEqual(session_id, "sess-1")
            req.assert_not_called()          # no network round trip at all

    def test_stale_session_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            with open(path, "w") as f:
                json.dump({"date": "1999-01-01", "session_id": "old"}, f)
            client = ChoiceClient("v", "k")
            self.assertFalse(client.load_session(path))
            self.assertIsNone(client.session_id)


class TestOrderNumbers(unittest.TestCase):
    def test_auto_client_order_no_is_unique_and_monotonic(self):
        from choice_api.orders import _next_client_order_no
        nos = [_next_client_order_no() for _ in range(50)]
        self.assertEqual(len(set(nos)), 50)
        self.assertEqual(nos, sorted(nos))
        self.assertTrue(all(0 < n < 2 ** 31 - 1 for n in nos))


class TestScripMasterResolve(unittest.TestCase):
    def setUp(self):
        self.sm = ScripMaster()
        self.sm._ingest(
            "Token,Symbol,SecDesc,Segment,Exchange,Series,MarketLot\n"
            "2885,RELIANCE,RELIANCE INDUSTRIES,1,NSE,EQ,1\n"
            "500325,RELIANCE,RELIANCE INDUSTRIES,3,BSE,A,1\n"
            "48552,BANKNIFTY,BANKNIFTY FUT,2,NSE,,15\n",
            "01Jan2024",
        )

    def test_ingest_populates_lookups(self):
        self.assertTrue(self.sm.is_loaded)
        self.assertEqual(self.sm.loaded_date, "01Jan2024")
        self.assertEqual(self.sm.get_lot_size("48552"), 15)

    def test_resolve_with_segment(self):
        self.assertEqual(self.sm.resolve("RELIANCE", segment=Segment.NSE_CASH), (1, 2885))
        self.assertEqual(self.sm.resolve("RELIANCE", segment=Segment.BSE_CASH), (3, 500325))

    def test_resolve_without_segment_picks_first(self):
        segment_id, token = self.sm.resolve("BANKNIFTY")
        self.assertEqual((segment_id, token), (2, 48552))

    def test_resolve_unknown_symbol_raises(self):
        with self.assertRaises(KeyError):
            self.sm.resolve("NOSUCHSYMBOL")

    def test_resolve_wrong_segment_raises(self):
        with self.assertRaises(KeyError):
            self.sm.resolve("BANKNIFTY", segment=Segment.BSE_CASH)

    def test_resolve_on_unloaded_master_explains_why(self):
        with self.assertRaises(KeyError) as ctx:
            ScripMaster().resolve("RELIANCE")
        self.assertIn("not loaded", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
