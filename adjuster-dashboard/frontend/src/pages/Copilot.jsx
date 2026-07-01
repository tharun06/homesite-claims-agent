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
  const [pending, setPending] = useState(null); // { tool, args } while a write awaits approval
  const [status, setStatus] = useState("");     // transient "🔍 looking up…" line during the wait
  const endRef = useRef(null);
  const { connected } = useWebSocket(() => {});

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy, status]);

  function appendToLastMessage(text) {
    setMsgs((m) => {
      const updated = [...m];
      const last = updated[updated.length - 1];
      updated[updated.length - 1] = { ...last, content: last.content + text };
      return updated;
    });
  }

  function replaceLastMessage(content) {
    setMsgs((m) => {
      const updated = [...m];
      updated[updated.length - 1] = { ...updated[updated.length - 1], content };
      return updated;
    });
  }

  async function send(text) {
    const q = (text ?? input).trim();
    if (!q || busy || pending) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    setStatus("Thinking…"); // immediate, before the server sends its first progress line

    // Deltas often arrive in fast bursts (many chunks within milliseconds) — too
    // fast for a human to see as "typing". Buffer them here and drip-feed a
    // couple characters per tick instead, decoupling arrival speed from display speed.
    let queue = "";
    let streamEnded = false;
    let final = null;
    let bubbleStarted = false;
    const CHARS_PER_TICK = 2;
    const TICK_MS = 20;

    // create the empty answer bubble lazily — only once real text (or the final
    // answer) is ready, so the wait shows just the status line, not an empty box
    const startBubble = () => {
      if (bubbleStarted) return;
      bubbleStarted = true;
      setStatus("");
      setMsgs((m) => [...m, { role: "assistant", content: "" }]);
    };

    const timer = setInterval(() => {
      if (queue.length > 0) {
        startBubble();
        appendToLastMessage(queue.slice(0, CHARS_PER_TICK));
        queue = queue.slice(CHARS_PER_TICK);
      } else if (streamEnded) {
        clearInterval(timer);
        startBubble(); // ensures a bubble exists even for the zero-delta (pending) case
        replaceLastMessage(final.answer); // authoritative final text
        setPending(final.pending ? final.action : null);
        setBusy(false);
      }
    }, TICK_MS);

    try {
      final = await copilot.chat(
        q,
        (delta) => { queue += delta; },
        (s) => { if (!bubbleStarted) setStatus(s); }, // ignore late status once text is flowing
      );
      streamEnded = true; // the timer above will notice the drained queue and finalize
    } catch {
      clearInterval(timer);
      setStatus("");
      startBubble();
      replaceLastMessage("⚠️ Copilot service unavailable. Is it running on port 8200?");
      setBusy(false);
    }
  }

  async function decide(approved) {
    setBusy(true);
    setStatus(approved ? "Applying the change…" : "Cancelling…");
    try {
      const r = approved ? await copilot.approve() : await copilot.reject();
      setMsgs((m) => [...m, { role: "assistant", content: r.answer }]);
    } catch {
      setMsgs((m) => [...m, { role: "assistant", content: "⚠️ Copilot service unavailable. Is it running on port 8200?" }]);
    } finally {
      setStatus("");
      setPending(null);
      setBusy(false);
    }
  }

  async function reset() {
    await copilot.reset();
    setMsgs([{ role: "assistant", content: "Conversation reset. What would you like to know?" }]);
    setPending(null);
    setStatus("");
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
            {status && <div className="msg customer muted">{status}</div>}
            <div ref={endRef} />
          </div>

          {pending && (
            <div className="pending-action">
              <div className="tool">🔧 {pending.tool}</div>
              <div className="args">{JSON.stringify(pending.args)}</div>
              <div className="row-actions" style={{ margin: 0 }}>
                <button className="primary" onClick={() => decide(true)} disabled={busy}>✅ Approve</button>
                <button onClick={() => decide(false)} disabled={busy}>❌ Reject</button>
              </div>
            </div>
          )}

          <div className="row-actions" style={{ marginTop: 10 }}>
            <input style={{ flex: 1 }} placeholder={pending ? "Approve or reject the pending action above…" : "Ask the Copilot…"} value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()} disabled={busy || !!pending} />
            <button className="primary" onClick={() => send()} disabled={busy || !!pending}>Send</button>
          </div>
        </div>

        <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {EXAMPLES.map((ex) => (
            <button key={ex} onClick={() => send(ex)} disabled={busy || !!pending}>{ex}</button>
          ))}
        </div>
      </div>
    </>
  );
}
