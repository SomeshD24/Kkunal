import asyncio
import unittest
import zlib
from unittest import mock

from choice_api import InteractiveSocketClient, PriceFeedSocketClient


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    def send(self, packet, opcode=None):
        self.sent.append(zlib.decompress(packet[6:]).decode())

    def close(self):
        pass

    def codes(self):
        return [message.split("|")[1] for message in self.sent]


class TestPriceFeedSubscriptions(unittest.TestCase):
    def setUp(self):
        # A long delay keeps the fallback timer out of the way; the logon ack drives the flush.
        self.feed = PriceFeedSocketClient(vendor_id="V", access_token="T", resubscribe_delay=300)
        self.ws = FakeWebSocket()
        self.feed.ws = self.ws
        self.addCleanup(self.feed._cancel_flush_timer)

    def connect(self):
        self.feed._on_open(self.ws)
        self.feed._on_message(self.ws, b"")          # first inbound message = logon acknowledgement

    def test_missing_access_token_is_not_sent_as_the_word_none(self):
        feed = PriceFeedSocketClient(vendor_id="V", access_token=None)
        self.assertEqual(feed.access_token, "")

    def test_subscribing_before_connect_is_queued_not_lost(self):
        self.feed.subscribe_touchline("SESSION", 1, 2885)
        self.feed.subscribe_best_five("SESSION", 2, 68407)
        self.assertEqual(self.ws.sent, [])
        self.connect()
        self.assertEqual(self.ws.codes(), ["64=101", "64=206", "64=127"])

    def test_nothing_is_subscribed_before_the_logon_is_acknowledged(self):
        self.feed.subscribe_touchline("SESSION", 1, 2885)
        self.feed._on_open(self.ws)
        self.assertEqual(self.ws.codes(), ["64=101"])

    def test_subscriptions_are_replayed_after_a_reconnect(self):
        self.feed.subscribe_touchline("SESSION", 1, 2885)
        self.connect()
        self.feed._on_close(self.ws, 1006, "network blip")
        self.ws.sent.clear()

        self.connect()
        self.assertEqual(self.ws.codes(), ["64=101", "64=206"])

    def test_live_subscribe_is_immediate(self):
        self.connect()
        self.ws.sent.clear()
        self.feed.subscribe_touchline("SESSION", 1, 11536)
        self.assertEqual(self.ws.codes(), ["64=206"])
        self.assertIn("1=1$7=11536", self.ws.sent[0])

    def test_duplicate_subscription_is_replayed_once(self):
        for _ in range(3):
            self.feed.subscribe_touchline("SESSION", 1, 2885)
        self.connect()
        self.assertEqual(self.ws.codes().count("64=206"), 1)

    def test_fix_framing_round_trips(self):
        framed = self.feed._fix_message_length("63=FIX3.0|64=101|66=2024-01-01 09:00:00|67=V|68=T|400=11|")
        unpacked = self.feed._unpack_packet(self.feed._pack_message(framed))
        self.assertEqual(unpacked, [framed])
        self.assertEqual(self.feed._parse_fix(unpacked[0])["64"], "101")

    def test_market_data_is_delivered_to_callbacks(self):
        received = []
        self.feed.on_message(received.append)
        self.feed._process_packets("63=FIX3.0|64=209|7=2885|8=285050|79=125000|")
        self.assertEqual((received[0]["Token"], received[0]["LTP"], received[0]["Volume"]), ("2885", 285050.0, 125000))


class TestInteractiveSocketCallbacks(unittest.TestCase):
    def setUp(self):
        self.socket = InteractiveSocketClient(token="T")

    def test_known_event(self):
        seen = []
        self.socket.on("ORD_NRML", seen.append)
        self.socket._trigger_callbacks("ORD_NRML", {"MessageType": "ORD_NRML"})
        self.assertEqual(len(seen), 1)

    def test_unlisted_event_types_are_no_longer_dropped(self):
        seen = []
        self.socket.on("ORD_SPECIAL", seen.append)
        self.socket._trigger_callbacks("ORD_SPECIAL", {"MessageType": "ORD_SPECIAL"})
        self.assertEqual(len(seen), 1)

    def test_wildcard_receives_everything_except_errors(self):
        seen = []
        self.socket.on("*", seen.append)
        self.socket._trigger_callbacks("TRD_MSG", {"n": 1})
        self.socket._trigger_callbacks("MKT_STAT", {"n": 2})
        self.socket._trigger_callbacks("error", RuntimeError("x"))
        self.assertEqual(seen, [{"n": 1}, {"n": 2}])

    def test_a_failing_callback_does_not_stop_the_others(self):
        seen = []
        self.socket.on("TRD_MSG", lambda data: 1 / 0)
        self.socket.on("TRD_MSG", seen.append)
        with self.assertLogs("choice_api.websockets_interactive", level="ERROR"):
            self.socket._trigger_callbacks("TRD_MSG", {"n": 1})
        self.assertEqual(seen, [{"n": 1}])

    def test_callback_must_be_callable(self):
        with self.assertRaises(TypeError):
            self.socket.on("TRD_MSG", "not callable")

    def test_disconnect_ends_a_reconnect_loop(self):
        asyncio.run(self.socket.disconnect())
        self.assertTrue(self.socket._stop)

    def test_reconnect_retries_with_backoff_until_stopped(self):
        attempts, waits, errors = [], [], []
        self.socket.on("error", errors.append)

        async def refuse(url):
            attempts.append(url)
            raise ConnectionRefusedError("server down")

        async def instant_sleep(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                self.socket._stop = True          # what disconnect() does

        with mock.patch("choice_api.websockets_interactive.websockets.connect", side_effect=refuse), \
             mock.patch("choice_api.websockets_interactive.asyncio.sleep", side_effect=instant_sleep), \
             self.assertLogs("choice_api.websockets_interactive", level="ERROR"):
            asyncio.run(self.socket.connect(reconnect=True, max_backoff=10))

        self.assertEqual(len(attempts), 4)
        self.assertEqual(waits, [3.0, 6.0, 10.0])      # doubles, then caps at max_backoff
        self.assertEqual(len(errors), 4)

    def test_without_reconnect_a_failure_returns_after_one_attempt(self):
        attempts = []

        async def refuse(url):
            attempts.append(url)
            raise ConnectionRefusedError("server down")

        with mock.patch("choice_api.websockets_interactive.websockets.connect", side_effect=refuse), \
             self.assertLogs("choice_api.websockets_interactive", level="ERROR"):
            asyncio.run(self.socket.connect())
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
