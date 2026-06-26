import { useState, useRef, useEffect } from "react";
import { copilot } from "../api";
import { useWebSocket } from "../useWebSocket";
import TopBar from "../components/TopBar.jsx";

const EXAMPLES = [
  "What are my pending tasks?",
  "How many claims do I have, broken down by status?",
  "Show me my fraud-flagged claims",
  "Find repair shops near my most recent claim",
];

export default function Copilot() {
  const [msgs, setMsgs] = useState([
    { role: "assistant", content: "Hi — I'm your claims Copilot. Ask me about your claims, tasks, vehicles, or nearby repair shops." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);
  const { connected } = useWebSocket(() => {});

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy]);

  async function send(text) {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    try {
      const r = await copilot.chat(q);
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, tools: r.tools }]);
    } catch {
      setMsgs((m) => [...m, { role: "assistant", content: "⚠️ Copilot service unavailable. Is it running on port 8200?" }]);
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    await copilot.reset();
    setMsgs([{ role: "assistant", content: "Conversation reset. What would you like to know?" }]);
  }

  return (
    <>
      <TopBar connected={connected} />
      <div className="container" style={{ maxWidth: 760 }}>
        <div className="row-actions">
          <h2 style={{ margin: 0 }}>Copilot</h2>
          <span className="muted">natural-language assistant over your claims</span>
          <button style={{ marginLeft: "auto" }} onClick={reset}>Reset</button>
        </div>

        <div className="panel" style={{ padding: 14 }}>
          <div className="conv" style={{ maxHeight: 440 }}>
            {msgs.map((m, i) => (
              <div key={i} className={`msg ${m.role === "user" ? "adjuster" : "customer"}`} style={{ maxWidth: "92%" }}>
                {m.tools?.length > 0 && (
                  <div className="meta">
                    {m.tools.map((t, j) => (
                      <span key={j} className="badge gray" style={{ marginRight: 4 }}>🔧 {t.name}</span>
                    ))}
                  </div>
                )}
                <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
              </div>
            ))}
            {busy && <div className="msg customer muted">thinking…</div>}
            <div ref={endRef} />
          </div>

          <div className="row-actions" style={{ marginTop: 10 }}>
            <input style={{ flex: 1 }} placeholder="Ask the Copilot…" value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()} disabled={busy} />
            <button className="primary" onClick={() => send()} disabled={busy}>Send</button>
          </div>
        </div>

        <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {EXAMPLES.map((ex) => (
            <button key={ex} onClick={() => send(ex)} disabled={busy}>{ex}</button>
          ))}
        </div>
      </div>
    </>
  );
}
