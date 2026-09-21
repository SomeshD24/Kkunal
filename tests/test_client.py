import json
import os
import tempfile
import unittest
from unittest import mock

import requests

from choice_api import (
    ChoiceClient,
    BASE_URL_OMNE,
    BASE_URL_FINX,
    Segment,
    Side,
    Validity,
    OrderType,
    ProductType,
    Resolution,
    normalize_resolution,
    to_paisa,
    to_rupees,
    ChoiceAPIError,
    AuthenticationError,
    StaticIPError,
    APIResponseError,
    RateLimitError,
    NetworkError,
    InvalidResponseError,
    OrderValidationError,
)
from choice_api.exceptions import scrub, remember_secret


def fake_response(status_code=200, payload=None, text=None, headers=None):
    """Builds a stand-in for requests.Response covering only what request() touches."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.headers = headers or {}
    resp.text = text if text is not None else json.dumps(payload or {})
    if payload is None and text is not None:
        resp.json.side_effect = json.JSONDecodeError("bad", text, 0)
    else:
        resp.json.return_value = payload if payload is not None else {}
    return resp


def make_client(**kwargs):
    client = ChoiceClient("vendor", "key-0123456789", **kwargs)
    # No test may touch the network for the scrip master.
    client.scrip_master.fetch = mock.Mock(return_value=True)
    return client


LOGIN_OK = [
    fake_response(200, {"Status": "Success"}),
    fake_response(200, {"Status": "Success", "Response": "123456"}),
]


class TestConstants(unittest.TestCase):
    def test_enums_are_ints(self):
        self.assertEqual(int(Segment.NSE_CASH), 1)
        self.assertEqual(int(Segment.NSE_FO), 2)
        self.assertEqual(int(Segment.BSE_FO), 4)
        self.assertEqual(int(Segment.MCX), 5)
        self.assertEqual(int(Segment.NSE_CURRENCY), 13)
        self.assertEqual(int(Side.BUY), 1)
        self.assertEqual(int(Side.SELL), 2)
        self.assertEqual(int(Validity.DAY), 1)

    def test_no_unverified_constants(self):
        """Values that could not be confirmed against the API must not be offered."""
        self.assertFalse(hasattr(Validity, "IOC"))
        self.assertFalse(hasattr(Resolution, "MIN_3"))     # ChartData rejects a 3-minute interval

    def test_str_constants_pass_through(self):
        self.assertEqual(OrderType.LIMIT, "RL_LIMIT")
        self.assertEqual(ProductType.DELIVERY, "D")
        self.assertEqual(Resolution.DAY, "D")
        self.assertEqual(Resolution.MIN_5, "5")
        # They must serialise as plain strings in a JSON payload.
        self.assertEqual(json.dumps({"t": OrderType.LIMIT}), '{"t": "RL_LIMIT"}')

    def test_normalize_resolution(self):
        self.assertEqual(normalize_resolution("5"), "5")
        self.assertEqual(normalize_resolution(Resolution.HOUR_1), "60")
        self.assertEqual(normalize_resolution("5m"), "5")
        self.assertEqual(normalize_resolution("1h"), "60")
        self.assertEqual(normalize_resolution("daily"), "D")
        self.assertEqual(normalize_resolution("M"), "M")             # monthly, as the API defines it
        for bad in ("3", "2", "7m", "m", "h", "", "weekly-ish"):
            with self.subTest(resolution=bad), self.assertRaises(ValueError):
                normalize_resolution(bad)

    def test_paisa_conversion(self):
        self.assertEqual(to_paisa(1300.50), 130050)
        self.assertEqual(to_paisa(1300), 130000)
        self.assertEqual(to_rupees(130050), 1300.50)
        self.assertEqual(to_rupees(to_paisa(999.99)), 999.99)


class TestErrorMapping(unittest.TestCase):
    def setUp(self):
        self.client = make_client(max_retries=0)

    def _request_with(self, response, method="GET", **kwargs):
        with mock.patch.object(self.client._http, "request", return_value=response):
            return self.client.request(method, "api/OpenAPI/FundsView", **kwargs)

    def test_401_raises_authentication_error(self):
        with self.assertRaises(AuthenticationError):
            self._request_with(fake_response(401, {}))

    def test_static_ip_rejection_is_its_own_error(self):
        with self.assertRaises(StaticIPError) as ctx:
            self._request_with(fake_response(403, None, text="Request from invalid IP address"))
        self.assertIsInstance(ctx.exception, AuthenticationError)
        self.assertIn("static IP", str(ctx.exception))

    def test_market_session_wording_is_not_an_auth_error(self):
        """'session' alone must not be read as an expired login."""
        with self.assertRaises(APIResponseError) as ctx:
            self._request_with(fake_response(400, None, text="Order not allowed in this market session"))
        self.assertNotIsInstance(ctx.exception, AuthenticationError)

    def test_429_carries_retry_after(self):
        with self.assertRaises(RateLimitError) as ctx:
            self._request_with(fake_response(429, {}, headers={"Retry-After": "7"}))
        self.assertEqual(ctx.exception.retry_after, 7.0)

    def test_500_raises_api_response_error(self):
        with self.assertRaises(APIResponseError) as ctx:
            self._request_with(fake_response(500, {}))
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.endpoint, "api/OpenAPI/FundsView")

    def test_transport_failure_raises_network_error(self):
        with mock.patch.object(self.client._http, "request",
                               side_effect=requests.exceptions.ConnectTimeout("timed out")):
            with self.assertRaises(NetworkError):
                self.client.request("GET", "api/OpenAPI/FundsView")

    def test_order_network_error_warns_about_unknown_state(self):
        with mock.patch.object(self.client._http, "request",
                               side_effect=requests.exceptions.ReadTimeout("timed out")):
            with self.assertRaises(NetworkError) as ctx:
                self.client.request("POST", "api/OpenAPI/V2/NewOrder", {}, is_order=True)
        self.assertIn("check the order book", str(ctx.exception))

    def test_non_json_body_raises_invalid_response(self):
        with self.assertRaises(InvalidResponseError):
            self._request_with(fake_response(200, None, text="<html>nope</html>"))

    def test_all_derive_from_base(self):
        for exc in (AuthenticationError, StaticIPError, APIResponseError, RateLimitError,
                    NetworkError, InvalidResponseError, OrderValidationError):
            self.assertTrue(issubclass(exc, ChoiceAPIError))

    def test_timeout_is_passed_through(self):
        with mock.patch.object(self.client._http, "request",
                               return_value=fake_response(200, {"ok": 1})) as m:
            self.client.request("GET", "api/OpenAPI/FundsView")
        self.assertEqual(m.call_args.kwargs["timeout"], 30.0)

        client = make_client(timeout=5)
        with mock.patch.object(client._http, "request", return_value=fake_response(200, {"ok": 1})) as m:
            client.request("GET", "api/OpenAPI/FundsView")
        self.assertEqual(m.call_args.kwargs["timeout"], 5)

    def test_raise_on_error_is_opt_in(self):
        failed = {"Status": "Fail", "Reason": "Insufficient funds"}
        self.assertEqual(self._request_with(fake_response(200, failed)), failed)

        strict = make_client(max_retries=0, raise_on_error=True)
        with mock.patch.object(strict._http, "request", return_value=fake_response(200, failed)):
            with self.assertRaises(APIResponseError) as ctx:
                strict.request("GET", "api/OpenAPI/FundsView")
        self.assertIn("Insufficient funds", str(ctx.exception))


class TestSecretScrubbing(unittest.TestCase):
    def test_known_secrets_never_reach_a_message(self):
        remember_secret("live-session-id-abcdef")
        error = ChoiceAPIError("boom live-session-id-abcdef happened")
        self.assertNotIn("live-session-id-abcdef", str(error))

    def test_secret_shaped_values_are_redacted(self):
        text = scrub("{'SessionId': 'abc123456', 'AccessToken': \"tok987654\", 'Qty': 5}")
        self.assertNotIn("abc123456", text)
        self.assertNotIn("tok987654", text)
        self.assertIn("'Qty': 5", text)

    def test_jwt_is_redacted(self):
        jwt = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJleGFtcGxlLXVzZXIiLCJuYW1lIjoiVGVzdCJ9.c2lnbmF0dXJl"        # synthetic: {"alg": "none"}.{"sub": "example-user"}
        self.assertNotIn(jwt[:12], scrub(f"Bearer header was {jwt}"))

    def test_api_key_is_scrubbed_from_errors(self):
        client = make_client(max_retries=0)
        with mock.patch.object(client._http, "request",
                               return_value=fake_response(400, None, text="bad key key-0123456789")):
            with self.assertRaises(APIResponseError) as ctx:
                client.request("GET", "api/OpenAPI/FundsView")
        self.assertNotIn("key-0123456789", str(ctx.exception))


@mock.patch("choice_api.client.time.sleep", return_value=None)
class TestRetryPolicy(unittest.TestCase):
    def test_get_is_retried_on_5xx(self, _sleep):
        client = make_client(max_retries=2)
        responses = [fake_response(503, {}), fake_response(502, {}), fake_response(200, {"ok": 1})]
        with mock.patch.object(client._http, "request", side_effect=responses) as m:
            self.assertEqual(client.request("GET", "api/OpenAPI/Holdings"), {"ok": 1})
        self.assertEqual(m.call_count, 3)

    def test_post_is_not_retried_by_default(self, _sleep):
        client = make_client(max_retries=2)
        with mock.patch.object(client._http, "request", return_value=fake_response(503, {})) as m:
            with self.assertRaises(APIResponseError):
                client.request("POST", "api/OpenAPI/SomethingMutating", {})
        self.assertEqual(m.call_count, 1)

    def test_orders_are_never_retried(self, _sleep):
        client = make_client(max_retries=5)
        with mock.patch.object(client._http, "request",
                               side_effect=requests.exceptions.ReadTimeout("slow")) as m:
            with self.assertRaises(NetworkError):
                client.orders.place_order(segment_id=1, token=2885, order_type="RL_LIMIT", bs=1, qty=1,
                                          price=130000, trigger_price=0, validity=1, product_type="D")
        self.assertEqual(m.call_count, 1, "a timed-out order may have reached the exchange")

    def test_read_only_post_opts_in(self, _sleep):
        client = make_client(max_retries=1)
        responses = [fake_response(500, {}), fake_response(200, {"Status": "Success"})]
        with mock.patch.object(client._http, "request", side_effect=responses) as m:
            client.market.get_multiple_touchline("1@2885")
        self.assertEqual(m.call_count, 2)

    def test_429_waits_for_retry_after(self, sleep):
        client = make_client(max_retries=1)
        responses = [fake_response(429, {}, headers={"Retry-After": "4"}), fake_response(200, {"ok": 1})]
        with mock.patch.object(client._http, "request", side_effect=responses):
            client.request("GET", "api/OpenAPI/Holdings")
        self.assertGreaterEqual(sleep.call_args[0][0], 4.0)

    def test_failover_to_the_other_gateway(self, _sleep):
        client = make_client(max_retries=1)
        outcomes = [requests.exceptions.ConnectionError("down"), fake_response(200, {"ok": 1})]
        with mock.patch.object(client._http, "request", side_effect=outcomes) as m:
            client.request("GET", "api/OpenAPI/Holdings")
        self.assertTrue(m.call_args_list[0][0][1].startswith(BASE_URL_OMNE))
        self.assertTrue(m.call_args_list[1][0][1].startswith(BASE_URL_FINX))
        self.assertEqual(client.active_base_url, BASE_URL_FINX)

    def test_no_failover_for_a_custom_base_url(self, _sleep):
        client = ChoiceClient("v", "k" * 12, base_url="https://proxy.internal", max_retries=1)
        with mock.patch.object(client._http, "request",
                               side_effect=requests.exceptions.ConnectionError("down")):
            with self.assertRaises(NetworkError):
                client.request("GET", "api/OpenAPI/Holdings")
        self.assertEqual(client.active_base_url, "https://proxy.internal")


class TestLogin(unittest.TestCase):
    def test_string_session_response_still_sets_access_token(self):
        """ValidateTOTP may return the session as a bare string; the feed token must not stay None."""
        client = make_client()
        responses = LOGIN_OK + [fake_response(200, {"Status": "Success", "Response": "SESSION-STRING-1"})]
        with mock.patch.object(client._http, "request", side_effect=responses):
            self.assertEqual(client.login("9999999999"), "SESSION-STRING-1")
        self.assertEqual(client.access_token, client.api_key)

    def test_dict_session_response(self):
        client = make_client()
        body = {"Status": "success", "Response": {"SessionId": "S-2", "AccessToken": "A-2",
                                                  "OdinBcastIP": "1.2.3.4", "OdinBcastPort": "4520"}}
        with mock.patch.object(client._http, "request", side_effect=LOGIN_OK + [fake_response(200, body)]):
            client.login("9999999999")
        self.assertEqual((client.session_id, client.access_token), ("S-2", "A-2"))
        self.assertEqual((client.bcast_ip, client.bcast_port), ("1.2.3.4", 4520))

    def test_failed_step_raises_with_the_brokers_reason(self):
        client = make_client()
        with mock.patch.object(client._http, "request",
                               return_value=fake_response(200, {"Status": "Fail", "Reason": "Unknown mobile"})):
            with self.assertRaises(AuthenticationError) as ctx:
                client.login("9999999999")
        self.assertIn("Unknown mobile", str(ctx.exception))

    def test_ip_rejection_during_login(self):
        client = make_client()
        with mock.patch.object(client._http, "request",
                               return_value=fake_response(200, {"Status": "Fail", "Reason": "Invalid IP address"})):
            with self.assertRaises(StaticIPError):
                client.login("9999999999")

    def test_mobile_number_is_required(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            make_client().login()

    def test_from_env(self):
        env = {"CHOICE_VENDOR_ID": "V1", "CHOICE_API_KEY": "K" * 16, "CHOICE_BASE_URL": BASE_URL_FINX}
        with mock.patch.dict(os.environ, env, clear=True):
            client = ChoiceClient.from_env(timeout=9)
        self.assertEqual((client.vendor_id, client.base_url, client.timeout), ("V1", BASE_URL_FINX, 9))
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            ChoiceClient.from_env()

    def test_auto_relogin_replays_the_request_once(self):
        client = make_client(max_retries=0)
        session = {"Status": "Success", "Response": "FRESH-SESSION-1"}
        with mock.patch.object(client._http, "request", side_effect=LOGIN_OK + [fake_response(200, session)]):
            client.login("9999999999")

        session2 = {"Status": "Success", "Response": "FRESH-SESSION-2"}
        outcomes = ([fake_response(401, {})]                                  # session died
                    + LOGIN_OK + [fake_response(200, session2)]               # transparent re-login
                    + [fake_response(200, {"Status": "Success", "Response": []})])   # replayed call
        with mock.patch.object(client._http, "request", side_effect=outcomes) as m:
            result = client.portfolio.get_holdings()
        self.assertEqual(result["Status"], "Success")
        self.assertEqual(client.session_id, "FRESH-SESSION-2")
        self.assertEqual(m.call_count, 5)

    def test_second_401_is_not_retried_forever(self):
        client = make_client(max_retries=0)
        session = {"Status": "Success", "Response": "FRESH-SESSION-3"}
        with mock.patch.object(client._http, "request", side_effect=LOGIN_OK + [fake_response(200, session)]):
            client.login("9999999999")
        outcomes = [fake_response(401, {})] + LOGIN_OK + [fake_response(200, session), fake_response(401, {})]
        with mock.patch.object(client._http, "request", side_effect=outcomes):
            with self.assertRaises(AuthenticationError):
                client.portfolio.get_holdings()

    def test_no_relogin_without_a_prior_login(self):
        client = make_client(max_retries=0)
        client.session_id = "loaded-from-somewhere"
        with mock.patch.object(client._http, "request", return_value=fake_response(401, {})) as m:
            with self.assertRaises(AuthenticationError):
                client.portfolio.get_holdings()
        self.assertEqual(m.call_count, 1)


class TestSessionPersistence(unittest.TestCase):
    def test_is_authenticated(self):
        client = make_client()
        self.assertFalse(client.is_authenticated)
        client.session_id = "abc"
        self.assertTrue(client.is_authenticated)

    def test_repr_hides_no_secrets(self):
        client = ChoiceClient("vendor", "supersecretkey")
        self.assertNotIn("supersecretkey", repr(client))
        self.assertIn("not logged in", repr(client))

    def test_session_roundtrip(self):
        client = make_client()
        client.session_id, client.access_token = "sess-1-abcdef", "tok-1-abcdef"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            self.assertTrue(client.save_session(path))
            self.assertEqual([f for f in os.listdir(d)], ["session.json"], "no temp file left behind")

            restored = make_client()
            self.assertTrue(restored.load_session(path))
            self.assertEqual((restored.session_id, restored.access_token), ("sess-1-abcdef", "tok-1-abcdef"))

    def test_login_reuses_saved_session(self):
        client = make_client()
        client.session_id = "sess-1-abcdef"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            client.save_session(path)

            fresh = make_client()
            with mock.patch.object(fresh._http, "request") as req:
                session_id = fresh.login("9999999999", session_file=path)
            self.assertEqual(session_id, "sess-1-abcdef")
            req.assert_not_called()          # no network round trip at all

    def test_force_ignores_the_saved_session(self):
        client = make_client()
        client.session_id = "sess-old-abcdef"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            client.save_session(path)

            fresh = make_client()
            body = {"Status": "Success", "Response": "sess-new-abcdef"}
            with mock.patch.object(fresh._http, "request", side_effect=LOGIN_OK + [fake_response(200, body)]):
                fresh.login("9999999999", session_file=path, force=True)
            self.assertEqual(fresh.session_id, "sess-new-abcdef")
            with open(path) as f:
                self.assertEqual(json.load(f)["session_id"], "sess-new-abcdef")

    def test_stale_or_empty_session_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            for content in ({"date": "1999-01-01", "session_id": "old"},
                            {"date": __import__("datetime").date.today().isoformat(), "session_id": None},
                            ["not", "a", "dict"]):
                with open(path, "w") as f:
                    json.dump(content, f)
                client = make_client()
                self.assertFalse(client.load_session(path), content)
                self.assertIsNone(client.session_id)

    def test_logoff_discards_the_saved_session(self):
        """A logged-off session must never be reloaded by the next run."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            client = make_client(max_retries=0)
            body = {"Status": "Success", "Response": "sess-x-abcdef"}
            with mock.patch.object(client._http, "request", side_effect=LOGIN_OK + [fake_response(200, body)]):
                client.login("9999999999", session_file=path)
            self.assertTrue(os.path.exists(path))

            with mock.patch.object(client._http, "request", return_value=fake_response(200, {"Status": "Success"})):
                client.logoff()
            self.assertFalse(os.path.exists(path))
            self.assertIsNone(client.session_id)

    def test_context_manager_logs_off(self):
        client = make_client()
        client.session_id = "abc"
        with mock.patch.object(client, "logoff") as logoff:
            with client:
                pass
        logoff.assert_called_once()

    def test_context_manager_keeps_a_persisted_session_alive(self):
        """With session_file the session is meant to outlive the process, so no logoff."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session.json")
            client = make_client()
            body = {"Status": "Success", "Response": "sess-keep-abcdef"}
            with mock.patch.object(client._http, "request", side_effect=LOGIN_OK + [fake_response(200, body)]):
                client.login("9999999999", session_file=path)
            with mock.patch.object(client, "logoff") as logoff:
                with client:
                    pass
            logoff.assert_not_called()
            self.assertTrue(os.path.exists(path))

    def test_context_manager_skips_logoff_when_not_logged_in(self):
        client = make_client()
        with mock.patch.object(client, "logoff") as logoff:
            with client:
                pass
        logoff.assert_not_called()


class TestOrders(unittest.TestCase):
    ORDER = dict(segment_id=1, token=2885, order_type="RL_LIMIT", bs=1, qty=10,
                 price=130000, trigger_price=0, validity=1, product_type="D")

    def setUp(self):
        self.client = make_client(max_retries=0)

    def _place(self, **overrides):
        with mock.patch.object(self.client._http, "request",
                               return_value=fake_response(200, {"Status": "Success"})) as m:
            self.client.orders.place_order(**{**self.ORDER, **overrides})
        return m

    def test_valid_order_is_sent(self):
        m = self._place()
        self.assertEqual(m.call_args.kwargs["json"]["Qty"], 10)

    def test_invalid_orders_never_leave_the_machine(self):
        for bad in ({"qty": 0}, {"qty": -5}, {"qty": 1.5}, {"bs": 3}, {"price": -1},
                    {"trigger_price": -1}, {"disclosed_qty": 11}):
            with self.subTest(order=bad):
                with mock.patch.object(self.client._http, "request") as m:
                    with self.assertRaises(OrderValidationError):
                        self.client.orders.place_order(**{**self.ORDER, **bad})
                m.assert_not_called()

    def test_validation_error_is_also_a_value_error(self):
        with self.assertRaises(ValueError):
            self.client.orders.place_order(**{**self.ORDER, "qty": 0})

    def test_fractional_price_warns_about_paisa(self):
        with self.assertLogs("choice_api.orders", level="WARNING") as logs:
            self._place(price=1300.5)
        self.assertIn("PAISA", logs.output[0])

    def test_auto_client_order_no_is_unique_and_monotonic(self):
        from choice_api.orders import _next_client_order_no
        nos = [_next_client_order_no() for _ in range(50)]
        self.assertEqual(len(set(nos)), 50)
        self.assertEqual(nos, sorted(nos))
        self.assertTrue(all(0 < n < 2 ** 31 - 1 for n in nos))


class TestMarket(unittest.TestCase):
    def setUp(self):
        self.client = make_client(max_retries=0)

    def _touchline(self, arg):
        with mock.patch.object(self.client._http, "request",
                               return_value=fake_response(200, {"Status": "Success"})) as m:
            self.client.market.get_multiple_touchline(arg)
        return m.call_args.kwargs["json"]["MultipleSegToken"]

    def test_string_is_passed_through(self):
        self.assertEqual(self._touchline("1@2885,1@11536"), "1@2885,1@11536")

    def test_pairs_are_formatted(self):
        self.assertEqual(self._touchline([(1, 2885), (Segment.NSE_FO, 68407)]), "1@2885,2@68407")

    def test_contract_dicts_are_formatted(self):
        self.assertEqual(self._touchline([{"Segment": 2, "Token": 68407, "SecDesc": "NIFTY26SEPFUT"}]), "2@68407")

    def test_empty_list_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.market.get_multiple_touchline([])


if __name__ == "__main__":
    unittest.main()
