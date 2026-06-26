import { useEffect, useRef, useState } from "react";
import { WS_BASE, getToken } from "./api";

// Single live connection. Calls onEvent(msg) for every server event.
export function useWebSocket(onEvent) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const ws = new WebSocket(`${WS_BASE}?token=${token}`);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (e) => {
      try {
        cbRef.current(JSON.parse(e.data));
      } catch {}
    };
    // keepalive ping
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 25000);

    return () => {
      clearInterval(ping);
      ws.close();
    };
  }, []);

  return { connected };
}
