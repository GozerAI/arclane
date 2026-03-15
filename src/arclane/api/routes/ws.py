"""WebSocket route for real-time updates.

Item 201: WebSocket endpoint that streams real-time activity and cycle
progress to connected clients. Alternative to the SSE-based endpoints.
"""

import uuid

from fastapi import status
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from arclane.core.database import async_session
from arclane.core.logging import get_logger
from arclane.models.tables import Business
from arclane.performance.websocket import ws_manager

log = get_logger("api.ws")
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, business_id: int | None = None):
    """WebSocket connection for real-time updates.

    Query params:
        business_id: Optional — subscribe to a specific business's events.

    Client can send JSON messages:
        {"type": "subscribe", "channel": "business:123"}
        {"type": "unsubscribe", "channel": "business:123"}
        {"type": "ping"}
    """
    session = getattr(websocket, "session", None)
    user_email = session.get("user_email") if isinstance(session, dict) else None
    if not user_email:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        return

    async with async_session() as db:
        result = await db.execute(
            select(Business.id)
            .where(Business.owner_email == user_email)
            .where(~Business.slug.startswith("_user-"))
        )
        owned_business_ids = {row[0] for row in result.all()}

    if business_id is not None and business_id not in owned_business_ids:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden")
        return

    await websocket.accept()
    client_id = str(uuid.uuid4())
    allowed_channels = {f"business:{owned_id}" for owned_id in owned_business_ids}
    default_channels = {f"business:{business_id}"} if business_id in owned_business_ids else set()

    client = await ws_manager.connect(
        websocket,
        client_id,
        business_id if business_id in owned_business_ids else None,
        default_channels=default_channels,
    )

    # Send welcome message
    await websocket.send_json({
        "type": "connected",
        "client_id": client_id,
        "subscriptions": list(client.subscriptions),
    })

    try:
        while True:
            raw = await websocket.receive_text()
            response = await ws_manager.handle_client_message(
                client_id,
                raw,
                allowed_channels=allowed_channels,
            )
            if response:
                await websocket.send_json(response)
    except WebSocketDisconnect:
        await ws_manager.disconnect(client_id)
    except Exception:
        await ws_manager.disconnect(client_id)
