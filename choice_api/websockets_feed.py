import zlib
import logging
import time
import threading
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple
import websocket

from .constants import ist_now

logger = logging.getLogger(__name__)

DEFAULT_FEED_HOST = "wss://brd.choiceindia.co.in:4520"

class PriceFeedSocketClient:
    """
    Live Price Feed Socket using FIX3.0 delimited ASCII formats and Zlib compression.
    Connects to the secure WebSocket (wss) endpoint using a background thread.

    Subscriptions are remembered: they may be requested before the socket is up,
    and are replayed automatically after every reconnect, so a network blip no
    longer leaves the feed connected but silent.
    """
    def __init__(self, vendor_id: str, access_token: str = "", host: Optional[str] = DEFAULT_FEED_HOST,
                 port: Optional[int] = None, resubscribe_delay: float = 2.0):
        # We default to the secure websocket endpoint. `host=client.bcast_ip` is None when the
        # login response carried no broadcast address, so fall back rather than crash the thread.
        self.host = host or DEFAULT_FEED_HOST
        self.port = port
        self.vendor_id = vendor_id
        self.access_token = access_token or ""
        self.resubscribe_delay = resubscribe_delay

        self.ws = None
        self.thread = None
        self._connected = False
        self._callbacks: List[Callable] = []
        self._is_running = False

        # (kind, segment_id, token) -> session_id, in subscription order.
        self._subscriptions: Dict[Tuple[str, int, int], str] = OrderedDict()
        self._sub_lock = threading.RLock()
        self._session_ready = False
        self._flush_timer: Optional[threading.Timer] = None
        self._reconnect_delay = 3.0

    def on_message(self, callback: Callable):
        """Registers a callback for parsed feed messages."""
        self._callbacks.append(callback)

    def start_websocket(self):
        """Starts the WebSocket connection in a background thread."""
        if self._is_running:
            return
            
        self._is_running = True
        self.thread = threading.Thread(target=self._ws_run, daemon=True)
        self.thread.start()
        logger.info("PriceFeedSocketClient thread started.")

    def stop_websocket(self):
        """Stops the WebSocket connection."""
        self._is_running = False
        self._cancel_flush_timer()
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("PriceFeedSocketClient stopped.")

    def _now(self):
        """Exchange-clock timestamp for the FIX header, not the machine's local time."""
        return ist_now().strftime("%Y-%m-%d %H:%M:%S")

    def _fix_message_length(self, msg: str) -> str:
        parts = [p for p in msg.split("|") if p and not p.startswith("65=")]
        temp = "|".join(parts) + "|"
        body_length = len(temp)
        parts.insert(2, f"65={body_length}")
        return "|".join(parts) + "|"

    def _pack_message(self, msg: str) -> bytes:
        compressed = zlib.compress(msg.encode("ascii"), level=6)
        packet_len = len(compressed)
        header = b"\x05" + f"{packet_len:05d}".encode('ascii')
        return header + compressed

    def _send_raw(self, msg: str):
        """Sends a compressed message over the websocket."""
        packet = self._pack_message(msg)
        if self.ws and self._connected:
            self.ws.send(packet, opcode=websocket.ABNF.OPCODE_BINARY)
            logger.debug(f"Sent: {msg}")

    def send_login(self):
        """Sends the Logon Request (101)."""
        msg = f"63=FIX3.0|64=101|66={self._now()}|67={self.vendor_id}|68={self.access_token}|400=11|"
        final_msg = self._fix_message_length(msg)
        self._send_raw(final_msg)

    def _send_subscription(self, kind: str, session_id: str, segment_id: int, token: int):
        if kind == "touchline":
            msg = f"63=FIX3.0|64=206|66={self._now()}|1={segment_id}$7={token}|230=1|4={session_id}|"
        else:
            msg = f"63=FIX3.0|64=127|66={self._now()}|1={segment_id}|7={token}|230=1|4={session_id}|"
        self._send_raw(self._fix_message_length(msg))

    def _subscribe(self, kind: str, session_id: str, segment_id: int, token: int):
        with self._sub_lock:
            self._subscriptions[(kind, int(segment_id), int(token))] = session_id
            ready = self._session_ready
        # Before the logon completes the request is only recorded; it is sent
        # by _flush_subscriptions() as soon as the session is ready.
        if ready:
            self._send_subscription(kind, session_id, int(segment_id), int(token))

    def subscribe_touchline(self, session_id: str, segment_id: int, token: int):
        """Subscribes to Touchline (Market Data). Safe to call before the socket is connected."""
        self._subscribe("touchline", session_id, segment_id, token)

    def subscribe_best_five(self, session_id: str, segment_id: int, token: int):
        """Subscribes to Best Five (Depth Data). Safe to call before the socket is connected."""
        self._subscribe("best_five", session_id, segment_id, token)

    def _cancel_flush_timer(self):
        timer, self._flush_timer = self._flush_timer, None
        if timer:
            timer.cancel()

    def _flush_subscriptions(self):
        """Sends every remembered subscription, once per connection."""
        with self._sub_lock:
            if self._session_ready or not self._connected:
                return
            self._session_ready = True
            pending = list(self._subscriptions.items())
        self._cancel_flush_timer()
        for (kind, segment_id, token), session_id in pending:
            try:
                self._send_subscription(kind, session_id, segment_id, token)
            except Exception as e:
                logger.error("Failed to (re)subscribe %s %s/%s: %s", kind, segment_id, token, e)
        if pending:
            logger.info("Sent %d subscription(s) after logon.", len(pending))

    def _on_open(self, ws):
        logger.info("WEBSOCKET CONNECTED to Price Feed.")
        self._connected = True
        self._reconnect_delay = 3.0
        with self._sub_lock:
            self._session_ready = False
        self.send_login()
        # Subscriptions go out on the first message after the logon (its
        # acknowledgement). The timer covers a server that never sends one.
        self._cancel_flush_timer()
        self._flush_timer = threading.Timer(self.resubscribe_delay, self._flush_subscriptions)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _on_error(self, ws, error):
        logger.error(f"WEBSOCKET ERROR => {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        with self._sub_lock:
            self._session_ready = False
        self._cancel_flush_timer()
        logger.warning(f"WEBSOCKET CLOSED => Code={close_status_code} | Msg={close_msg}")

    def _parse_fix(self, text: str) -> dict:
        parsed = {}
        # The payload often contains length prefixes like '0031063=FIX3.0'. Strip everything before '63='
        idx = text.find("63=")
        if idx != -1:
            text = text[idx:]
            
        for item in text.split("|"):
            if "=" in item:
                k, v = item.split("=", 1)
                parsed[k] = v
        return parsed

    def _format_market_data(self, parsed: dict) -> dict:
        msg_code = parsed.get("64")
        if msg_code not in ["209", "128"]: # Touchline or Best Five
            return parsed # return raw if it's something else like Logon response
            
        # Common fields
        formatted = {
            "MessageType": msg_code, # 209: Touchline, 128: BestFive
            "Token": parsed.get("7"),
            "Raw": parsed
        }
        
        # Prices are delivered in paisa and deliberately left as-is
        price_fields = {
            "8": "LTP",
            "75": "Open",
            "76": "Close",
            "77": "High",
            "78": "Low",
            "80": "ATP",
            "250": "LowerCircuit",
        }
        
        for fix_key, name in price_fields.items():
            val = parsed.get(fix_key)
            if val:
                try:
                    formatted[name] = float(val)
                except ValueError:
                    pass
                    
        # Other numbers (Volume, OI, etc)
        int_fields = {
            "79": "Volume",
            "88": "OpenInterest",
            "81": "TotalBuyQty",
            "82": "TotalSellQty"
        }
        
        for fix_key, name in int_fields.items():
            val = parsed.get(fix_key)
            if val:
                try:
                    formatted[name] = int(val)
                except ValueError:
                    pass

        return formatted

    def _process_packets(self, text: str):
        text = text.replace("\x00", "")
        for p in text.split("\x02"):
            p = p.strip()
            if p:
                parsed = self._parse_fix(p)
                if not parsed:
                    continue
                
                formatted = self._format_market_data(parsed)
                for cb in self._callbacks:
                    try:
                        cb(formatted)
                    except Exception as e:
                        logger.error(f"Feed callback error: {e}")

    def _on_message(self, ws, message):
        if not self._session_ready:
            self._flush_subscriptions()
        try:
            if isinstance(message, str):
                message = message.encode()

            packets = self._unpack_packet(message)
            for packet in packets:
                self._process_packets(packet)
        except Exception as e:
            logger.error(f"Error in _on_message: {e}")

    def _unpack_packet(self, data: bytes):
        packets = []
        try:
            idx = 0
            while idx < len(data):
                packet_type = data[idx:idx + 1]
                if packet_type not in [b"\x05", b"\x02"]:
                    break
                packet_len = int(data[idx + 1:idx + 6].decode('ascii', errors='ignore'))
                body_start = idx + 6
                body_end = body_start + packet_len
                body = data[body_start:body_end]

                if packet_type == b"\x05":
                    decompressed = zlib.decompress(body)
                    text = decompressed.decode("ascii", errors="ignore")
                else:
                    text = body.decode("ascii", errors="ignore")

                text = text.replace("\x00", "")
                for p in text.split("\x02"):
                    p = p.strip()
                    if p:
                        packets.append(p)
                idx = body_end
        except Exception as e:
            logger.error(f"UNPACK ERROR: {e}")
        return packets

    def _ws_run(self):
        websocket.enableTrace(False)
        url = self.host
        if self.port:
            # If a port is explicitly provided and host doesn't look like a full URL
            if not url.startswith("ws"):
                url = f"wss://{self.host}:{self.port}"
                
        while self._is_running:
            try:
                self.ws = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f"WebSocket Loop Error: {e}")
            
            if self._is_running:
                logger.warning("Reconnecting websocket in %.0f seconds...", self._reconnect_delay)
                time.sleep(self._reconnect_delay)
                # Back off while the server stays down; _on_open resets this.
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)
