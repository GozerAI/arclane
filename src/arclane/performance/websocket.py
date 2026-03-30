"""WebSocket connection for real-time updates.

Item 201: Provides a WebSocket endpoint that pushes real-time activity,
cycle progress, and system events to connected clients. Replaces
or supplements the SSE-based /feed/stream endpoint.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from arclane.core.logging import get_logger

log = get_logger("performance.websocket")


@dataclass
class WSClient:
    """A connected WebSocket client."""
    client_id: str
    business_id: int | None = None
    connected_at: float = field(default_factory=time.time)
    messages_sent: int = 0
    subscriptions: set[str] = field(default_factory=set)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts messages.

    Supports subscription-based filtering so clients only receive
    messages for their subscribed channels (business-specific or global).
    """

    def __init__(self):
        self._connections: dict[str, Any] = {}  # client_id -> websocket
        self._clients: dict[str, WSClient] = {}
        self._total_messages = 0

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def stats(self) -> dict:
        return {
            "connections": self.connection_count,
            "total_messages_sent": self._total_messages,
            "clients": [
                {
                    "client_id": c.client_id,
                    "business_id": c.business_id,
                    "connected_at": c.connected_at,
                    "messages_sent": c.messages_sent,
                    "subscriptions": list(c.subscriptions),
                }
                for c in self._clients.values()
            ],
        }

    async def connect(
        self,
        websocket: Any,
        client_id: str,
        business_id: int | None = None,
        default_channels: set[str] | None = None,
    ) -> WSClient:
        """Register a new WebSocket connection."""
        self._connections[client_id] = websocket
        client = WSClient(
            client_id=client_id,
            business_id=business_id,
            subscriptions=set(default_channels or set()),
        )
        if business_id is not None:
            client.subscriptions.add(f"business:{business_id}")
        self._clients[client_id] = client
        log.info(
            "WebSocket connected: %s (business=%s)", client_id, business_id,
        )
        return client

    async def disconnect(self, client_id: str) -> None:
        """Remove a WebSocket connection."""
        self._connections.pop(client_id, None)
        self._clients.pop(client_id, None)
        log.debug("WebSocket disconnected: %s", client_id)

    def subscribe(self, client_id: str, channel: str) -> bool:
        """Subscribe a client to a channel."""
        client = self._clients.get(client_id)
        if client:
            client.subscriptions.add(channel)
            return True
        return False

    def unsubscribe(self, client_id: str, channel: str) -> bool:
        """Unsubscribe a client from a channel."""
        client = self._clients.get(client_id)
        if client:
            client.subscriptions.discard(channel)
            return True
        return False

    async def send_to_client(self, client_id: str, message: dict) -> bool:
        """Send a message to a specific client."""
        ws = self._connections.get(client_id)
        if ws is None:
            return False

        try:
            await ws.send_json(message)
            client = self._clients.get(client_id)
            if client:
                client.messages_sent += 1
            self._total_messages += 1
            return True
        except Exception:
            await self.disconnect(client_id)
            return False

    async def broadcast(
        self,
        channel: str,
        message: dict,
        exclude: set[str] | None = None,
    ) -> int:
        """Broadcast a message to all clients subscribed to a channel.

        Args:
            channel: Channel to broadcast on (e.g., "global", "business:123").
            message: Message dict to send.
            exclude: Set of client_ids to skip.

        Returns:
            Number of clients the message was sent to.
        """
        sent = 0
        exclude = exclude or set()

        for client_id, client in list(self._clients.items()):
            if client_id in exclude:
                continue
            if channel in client.subscriptions:
                if await self.send_to_client(client_id, message):
                    sent += 1

        return sent

    async def broadcast_activity(
        self,
        business_id: int,
        action: str,
        detail: str | None = None,
    ) -> int:
        """Broadcast an activity event to business subscribers."""
        message = {
            "type": "activity",
            "business_id": business_id,
            "action": action,
            "detail": detail,
            "timestamp": time.time(),
        }
        # Send to business-specific channel and global
        sent = await self.broadcast(f"business:{business_id}", message)
        sent += await self.broadcast("global", message)
        return sent

    async def broadcast_cycle_progress(
        self,
        business_id: int,
        cycle_id: int,
        status: str,
        progress_pct: float = 0.0,
    ) -> int:
        """Broadcast cycle progress to business subscribers."""
        message = {
            "type": "cycle_progress",
            "business_id": business_id,
            "cycle_id": cycle_id,
            "status": status,
            "progress_pct": progress_pct,
            "timestamp": time.time(),
        }
        return await self.broadcast(f"business:{business_id}", message)

    async def heartbeat(self, interval_seconds: float = 30.0) -> None:
        """Send periodic pings to all connected clients.

        Removes clients that fail to receive the ping (broken connections).
        Run this as a background task during app lifespan.
        """
        while True:
            await asyncio.sleep(interval_seconds)
            dead_clients: list[str] = []
            for client_id in list(self._connections):
                ok = await self.send_to_client(
                    client_id, {"type": "ping", "timestamp": time.time()},
                )
                if not ok:
                    dead_clients.append(client_id)
            for cid in dead_clients:
                await self.disconnect(cid)
            if dead_clients:
                log.debug("Heartbeat removed %d dead connections", len(dead_clients))

    async def handle_client_message(
        self,
        client_id: str,
        raw_message: str,
        *,
        allowed_channels: set[str] | None = None,
    ) -> dict | None:
        """Handle an incoming message from a client.

        Supports subscribe/unsubscribe commands and ping/pong.
        """
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError:
            return {"error": "invalid JSON"}

        msg_type = msg.get("type", "")

        if msg_type == "subscribe":
            channel = msg.get("channel", "")
            if channel:
                if allowed_channels is not None and channel not in allowed_channels:
                    return {"error": "forbidden channel"}
                self.subscribe(client_id, channel)
                return {"type": "subscribed", "channel": channel}

        elif msg_type == "unsubscribe":
            channel = msg.get("channel", "")
            if channel:
                if allowed_channels is not None and channel not in allowed_channels:
                    return {"error": "forbidden channel"}
                self.unsubscribe(client_id, channel)
                return {"type": "unsubscribed", "channel": channel}

        elif msg_type == "ping":
            return {"type": "pong", "timestamp": time.time()}

        return None


# Singleton
ws_manager = WebSocketManager()
