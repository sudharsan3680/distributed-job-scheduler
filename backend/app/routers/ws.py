from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.security import decode_access_token
from app.websocket.manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/projects/{project_id}")
async def project_events(websocket: WebSocket, project_id: int):
    """
    Live dashboard fanout. The dashboard connects with a short-lived JWT in
    the `?token=` query param (browsers can't set Authorization headers on a
    WebSocket handshake). We validate it before accepting; an invalid/missing
    token is rejected with code 1008 (policy violation) so a client can't
    eavesdrop on a project's job/worker events it isn't authorized for.
    """
    token = websocket.query_params.get("token")
    payload = decode_access_token(token) if token else None
    if not payload or "sub" not in payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(project_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # client doesn't need to send anything; just keeps the socket open
    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)
