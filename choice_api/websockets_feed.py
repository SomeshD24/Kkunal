import asyncio
import zlib
import logging
from typing import Callable

logger = logging.getLogger(__name__)

class PriceFeedSocketClient:
    """
    Live Price Feed Socket using FIX3.0 delimited ASCII formats and Zlib compression.
    """
    def __init__(self, host: str, port: int, user_id: str):
        self.host = host
        self.port = port
        self.user_id = user_id
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

    async def send_login(self):
        """Sends the Logon Request (101)."""
        # Format: 63=FIX3.0|64=101|65=66|66=2022-05-04 133022|400=11|67=USERID|68=|
        login_msg = f"63=FIX3.0|64=101|65=66|66=2023-03-06 113929|67={self.user_id}|68=|400=11"
        self._send_raw(login_msg)

    def subscribe_touchline(self, session_id: str, segment_id: int, token: int):
        """Subscribes to Touchline (Market Data)."""
        # Message code 206
        msg = f"63=FIX3.0|64=206|65=107|66=2023-02-11 19:02:31|1=1$7={token}|230=1|4={session_id}|"
        self._send_raw(msg)

    def subscribe_best_five(self, session_id: str, segment_id: int, token: int):
        """Subscribes to Best Five (Depth Data)."""
        # Message code 127
        msg = f"63=FIX3.0|64=127|65=84|66=2023-03-06 19:02:31|1=1|7={token}|230=1|4={session_id}"
        self._send_raw(msg)

    def _send_raw(self, msg: str):
        """Sends a raw uncompressed message (first byte = '2', next 5 bytes = length)."""
        length_str = str(len(msg)).zfill(5)
        packet = f"2{length_str}{msg}".encode('utf-8')
        if self.writer:
            self.writer.write(packet)
            logger.debug(f"Sent: {msg}")

    async def _listen(self):
        """Listens and decompresses incoming messages."""
        try:
            while self._connected and self.reader:
                # Read 6 byte header: 1 byte type, 5 bytes length
                header = await self.reader.readexactly(6)
                msg_type = chr(header[0])
                msg_len = int(header[1:6].decode('ascii'))
                
                payload = await self.reader.readexactly(msg_len)
                
                if msg_type == '5': # Compressed
                    try:
                        decompressed = zlib.decompress(payload)
                        self._process_message(decompressed.decode('ascii', errors='ignore'))
                    except Exception as e:
                        logger.error(f"Zlib decompression failed: {e}")
                elif msg_type == '2': # Uncompressed
                    self._process_message(payload.decode('ascii', errors='ignore'))
                else:
                    logger.warning(f"Unknown message format indicator: {msg_type}")
        except asyncio.IncompleteReadError:
            logger.info("Connection closed by server.")
            self._connected = False
        except Exception as e:
            logger.error(f"Feed listen error: {e}")

    def _process_message(self, raw_str: str):
        """Parses the FIX3.0 pipe delimited string."""
        # Simple split by pipe
        for cb in self._callbacks:
            try:
                cb(raw_str)
            except Exception as e:
                logger.error(f"Feed callback error: {e}")
