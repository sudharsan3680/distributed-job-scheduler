import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";

export interface LiveEvent { event: string; [key: string]: unknown }

export function useProjectEvents(projectId: number | null, onEvent?: (e: LiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!projectId) return;
    const token = localStorage.getItem("access_token");
    const wsUrl = API_BASE.replace(/^http/, "ws") + `/ws/projects/${projectId}?token=${encodeURIComponent(token || "")}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (msg) => {
      try {
        onEvent?.(JSON.parse(msg.data));
      } catch {
        // ignore malformed frames
      }
    };
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  return { connected };
}
