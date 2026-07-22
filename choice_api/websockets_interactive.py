import asyncio
import json
import logging
import websockets
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class InteractiveSocketClient:
    """
    FINX Interactive Socket for receiving order status, trade confirmations, and market events.
    """
    def __init__(self, token: str, host: str = "wss://finxsocket.choiceindia.com/ws/"):
        self.url = f"{host}?token={token}"
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._callbacks = {
            "MKT_STAT": [],
            "ORD_NRML": [],
            "TRD_MSG": [],
            "error": []
        }
        self._keepalive_task = None

    def on(self, event_type: str, callback: Callable):
        """Registers a callback for a specific MessageType (MKT_STAT, ORD_NRML, TRD_MSG)."""
        if event_type in self._callbacks:
            self._callbacks[event_type].append(callback)

    async def connect(self):
        """Connects to the interactive socket."""
        try:
            self.ws = await websockets.connect(self.url)
            self._connected = True
            logger.info("Connected to FINX Interactive Socket.")
            self._keepalive_task = asyncio.create_task(self._keepalive())
            await self._listen()
        except Exception as e:
            logger.error(f"Failed to connect to Interactive Socket: {e}")
            self._trigger_callbacks("error", e)

    async def disconnect(self):
        """Disconnects the socket."""
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
        """Triggers all callbacks registered for an event type."""
        for cb in self._callbacks.get(event_type, []):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Error in callback for {event_type}: {e}")
