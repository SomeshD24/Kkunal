import asyncio
import zlib
import logging
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)

class PriceFeedSocketClient:
    """
    Live Price Feed Socket using FIX3.0 delimited ASCII formats and Zlib compression.
    """
    def __init__(self, host: str, port: int, vendor_id: str, access_token: str = ""):
        self.host = host
        self.port = port
        self.vendor_id = vendor_id
        self.access_token = access_token
        self.reader = None
        self.writer = None
        self._connected = False
        self._callbacks = []

    def on_message(self, callback: Callable):
        """Registers a callback for parsed feed messages."""
        self._callbacks.append(callback)

    async def connect(self):
        """Connects to the Web Feed Handler via TCP Socket."""
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            self._connected = True
            logger.info(f"Connected to Price Feed Socket at {self.host}:{self.port}")
            
            # Send Logon Request
            await self.send_login()
            await self._listen()
        except Exception as e:
            logger.error(f"Failed to connect to Price Feed Socket: {e}")

    async def disconnect(self):
        """Disconnects the socket."""
        self._connected = False
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
            logger.info("Disconnected from Price Feed Socket.")

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
        """Sends a compressed message."""
        packet = self._pack_message(msg)
        if self.writer:
            self.writer.write(packet)
            logger.debug(f"Sent: {msg}")

    async def send_login(self):
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

    async def _listen(self):
        """Listens and decompresses incoming messages."""
        try:
            while self._connected and self.reader:
                header = await self.reader.readexactly(6)
                msg_type = header[0:1] # b'\x05' or b'\x02'
                msg_len = int(header[1:6].decode('ascii'))
                
                payload = await self.reader.readexactly(msg_len)
                
                if msg_type == b'\x05': # Compressed
                    try:
                        decompressed = zlib.decompress(payload)
                        text = decompressed.decode("ascii", errors="ignore")
                        self._process_packets(text)
                    except Exception as e:
                        logger.error(f"Zlib decompression failed: {e}")
                elif msg_type == b'\x02': # Uncompressed
                    text = payload.decode("ascii", errors="ignore")
                    self._process_packets(text)
                else:
                    logger.warning(f"Unknown message format indicator: {msg_type}")
        except asyncio.IncompleteReadError:
            logger.info("Connection closed by server.")
            self._connected = False
        except Exception as e:
            logger.error(f"Feed listen error: {e}")

    def _process_packets(self, text: str):
        text = text.replace("\x00", "")
        for p in text.split("\x02"):
            p = p.strip()
            if p:
                for cb in self._callbacks:
                    try:
                        cb(p)
                    except Exception as e:
                        logger.error(f"Feed callback error: {e}")
