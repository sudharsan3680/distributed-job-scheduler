import json

from fastapi import WebSocket


class ConnectionManager:
    """Broadcasts job/worker/queue events to dashboard clients subscribed to
    a given project. In-process pub/sub — fine for a single API instance;
    a multi-instance deployment would fan events out through Redis pub/sub
    or Postgres LISTEN/NOTIFY instead (see DESIGN_DECISIONS.md)."""

    def __init__(self):
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, project_id: int, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(project_id, set()).add(ws)

    def disconnect(self, project_id: int, ws: WebSocket):
        conns = self._connections.get(project_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._connections.pop(project_id, None)

    async def broadcast(self, project_id: int, event: dict):
        conns = self._connections.get(project_id)
        if not conns:
            return
        message = json.dumps(event, default=str)
        dead = []
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            conns.discard(ws)


manager = ConnectionManager()
