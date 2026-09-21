import asyncio
import json
import logging
import websockets
from typing import Any, Callable, Dict, List, Optional

from .exceptions import scrub

logger = logging.getLogger(__name__)

class InteractiveSocketClient:
    """
    FINX Interactive Socket for receiving order status, trade confirmations, and market events.
    """
    def __init__(self, token: str, host: str = "wss://finxsocket.choiceindia.com/ws/"):
        self.url = f"{host}?token={token}"
        self.ws: Optional[Any] = None
        self._connected = False
        self._callbacks: Dict[str, List[Callable]] = {
            "MKT_STAT": [],
            "ORD_NRML": [],
            "TRD_MSG": [],
            "error": []
        }
        self._keepalive_task: Optional[asyncio.Task] = None
        self._stop = False

    def on(self, event_type: str, callback: Callable):
        """
        Registers a callback for a MessageType (MKT_STAT, ORD_NRML, TRD_MSG), for
        'error', or for '*' to receive every message whatever its type.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callbacks.setdefault(event_type, []).append(callback)

    async def connect(self, reconnect: bool = False, max_backoff: float = 60.0):
        """
        Connects to the interactive socket and listens until it closes.

        Args:
            reconnect: Keep the socket alive - when the connection drops, wait
                (3s, doubling up to max_backoff) and connect again, until
                disconnect() is called. Order updates are easy to miss
                otherwise: a dropped socket used to end silently.
        """
        self._stop = False
        delay = 3.0
        while True:
            try:
                self.ws = await websockets.connect(self.url)
                self._connected = True
                delay = 3.0
                logger.info("Connected to FINX Interactive Socket.")
                self._keepalive_task = asyncio.create_task(self._keepalive())
                await self._listen()
            except Exception as e:
                logger.error("Interactive Socket connection failed: %s", scrub(e))
                self._trigger_callbacks("error", e)
            finally:
                self._connected = False
                if self._keepalive_task:
                    self._keepalive_task.cancel()

            if not reconnect or self._stop:
                return
            logger.warning("Interactive Socket dropped; reconnecting in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_backoff)

    async def disconnect(self):
        """Disconnects the socket (and ends any reconnect loop)."""
        self._stop = True
        self._connected = False
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from Interactive Socket.")

    async def _keepalive(self):
        """Sends '2' every 25 seconds to keep the connection alive."""
        try:
            while self._connected and self.ws:
                await asyncio.sleep(25)
                await self.ws.send("2")
                logger.debug("Sent heartbeat (2)")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Keepalive error: {e}")

    async def _listen(self):
        """Listens for incoming messages."""
        try:
            while self._connected and self.ws:
                message = await self.ws.recv()
                if message == "3":
                    logger.debug("Received heartbeat ack (3)")
                    continue
                
                try:
                    data = json.loads(message)
                    msg_type = data.get("MessageType")
                    if msg_type:
                        self._trigger_callbacks(msg_type, data)
                    else:
                        logger.warning(f"Unknown message format: {message}")
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON message received: {message}")
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed by server.")
            self._connected = False
        except Exception as e:
            logger.error(f"Listen error: {e}")
            self._trigger_callbacks("error", e)

    def _trigger_callbacks(self, event_type: str, data):
        """Triggers all callbacks registered for an event type, then the '*' wildcard ones."""
        handlers = list(self._callbacks.get(event_type, []))
        if event_type != "error":
            handlers += self._callbacks.get("*", [])
        for cb in handlers:
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Error in callback for {event_type}: {e}")
