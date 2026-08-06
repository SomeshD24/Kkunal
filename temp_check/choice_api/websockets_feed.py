import zlib
import logging
import time
import threading
from datetime import datetime
from typing import Callable
import websocket

logger = logging.getLogger(__name__)

class PriceFeedSocketClient:
    """
    Live Price Feed Socket using FIX3.0 delimited ASCII formats and Zlib compression.
    Connects to the secure WebSocket (wss) endpoint using a background thread.
    """
    def __init__(self, vendor_id: str, access_token: str = "", host: str = "wss://brd.choiceindia.co.in:4520", port: int = None):
        # We default to the secure websocket endpoint
        self.host = host
        self.port = port
        self.vendor_id = vendor_id
        self.access_token = access_token
        
        self.ws = None
        self.thread = None
        self._connected = False
        self._callbacks = []
        self._is_running = False

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
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("PriceFeedSocketClient stopped.")

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

    def subscribe_touchline(self, session_id: str, segment_id: int, token: int):
        """Subscribes to Touchline (Market Data)."""
        msg = f"63=FIX3.0|64=206|66={self._now()}|1={segment_id}$7={token}|230=1|4={session_id}|"
        final_msg = self._fix_message_length(msg)
        self._send_raw(final_msg)

    def subscribe_best_five(self, session_id: str, segment_id: int, token: int):
        """Subscribes to Best Five (Depth Data)."""
        msg = f"63=FIX3.0|64=127|66={self._now()}|1={segment_id}|7={token}|230=1|4={session_id}|"
        final_msg = self._fix_message_length(msg)
        self._send_raw(final_msg)

    def _on_open(self, ws):
        logger.info("WEBSOCKET CONNECTED to Price Feed.")
        self._connected = True
        self.send_login()

    def _on_error(self, ws, error):
        logger.error(f"WEBSOCKET ERROR => {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self._connected = False
        logger.warning(f"WEBSOCKET CLOSED => Code={close_status_code} | Msg={close_msg}")

    def _on_message(self, ws, message):
        try:
            if isinstance(message, str):
                message = message.encode()

            packets = self._unpack_packet(message)
            for packet in packets:
                for cb in self._callbacks:
                    try:
                        cb(packet)
                    except Exception as e:
                        logger.error(f"Feed callback error: {e}")
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
                logger.warning("Reconnecting websocket in 3 seconds...")
                time.sleep(3)
