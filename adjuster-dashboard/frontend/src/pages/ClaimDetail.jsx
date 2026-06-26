import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, getUser } from "../api";
import { useWebSocket } from "../useWebSocket";
import TopBar, { StatusBadge } from "../components/TopBar.jsx";

const STATUSES = ["FNOL", "Under Review", "Investigation", "Appraisal",
  "Pending Approval", "Approved", "Denied", "Closed", "SIU Flagged"];

export default function ClaimDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const user = getUser();
  const [claim, setClaim] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [events, setEvents] = useState([]);
  const [shops, setShops] = useState(null);
  const [note, setNote] = useState("");
  const [flashId, setFlashId] = useState(null);
  const convRef = useRef(null);

  const load = useCallback(() => {
    api.claim(id).then(setClaim).catch(() => nav("/"));
    api.conversations(id).then(setMsgs).catch(() => {});
    api.events(id).then(setEvents).catch(() => {});
  }, [id, nav]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (convRef.current) convRef.current.scrollTop = convRef.current.scrollHeight;
  }, [msgs]);

  // realtime: append messages / refresh on events for THIS claim
  const { connected } = useWebSocket((msg) => {
    if (String(msg.claim_id) !== String(id)) return;
    if (msg.type === "message") {
      const m = {
        id: Date.now(), sender_type: "customer", sender_name: msg.payload.sender_name,
        channel: "phone", source: msg.payload.source, content: msg.payload.content,
        timestamp: new Date().toISOString(),
      };
      setMsgs((prev) => [...prev, m]);
      setFlashId(m.id);
    } else if (msg.type === "status_change" || msg.type === "assignment") {
      load();
    }
  });

  if (!claim) return <><TopBar connected={connected} /><div className="container">Loading…</div></>;

  const canReassign = user?.role === "senior_adjuster" || user?.role === "admin";

  async function changeStatus(s) { await api.setStatus(id, s); load(); }
  async function submitNote() {
    if (!note.trim()) return;
    await api.addNote(id, note); setNote(""); load();
  }
  async function findShops() { setShops(await api.shopsNear(id, 1500)); }

  return (
    <>
      <TopBar connected={connected} />
      <div className="container">
        <div className="row-actions">
          <button onClick={() => nav("/")}>← Back</button>
          <h2 style={{ margin: 0 }}>{claim.claim_number}</h2>
          <StatusBadge status={claim.status} />
          {claim.fraud_flagged && <span className="badge fraud">fraud {Math.round(claim.fraud_score * 100)}%</span>}
          <select value={claim.status} onChange={(e) => changeStatus(e.target.value)}>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        <div className="detail-grid">
          {/* left column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="card">
              <h3>Claim</h3>
              <KV k="Peril" v={claim.peril_type} />
              <KV k="Loss date" v={claim.loss_date} />
              <KV k="Reported" v={claim.reported_date} />
              <KV k="Incident" v={`${claim.incident_city}, ${claim.incident_state}`} />
              <KV k="Estimate" v={`$${claim.estimated_amount?.toLocaleString()}`} />
              <KV k="Reserve" v={`$${claim.reserve_amount?.toLocaleString()}`} />
              <KV k="Deductible" v={`$${claim.deductible?.toLocaleString()}`} />
              {claim.approved_amount != null && <KV k="Approved payout" v={`$${claim.approved_amount?.toLocaleString()}`} />}
              <KV k="Adjuster" v={claim.adjuster?.name} />
            </div>

            <div className="card">
              <h3>Vehicle & Policy</h3>
              <KV k="Vehicle" v={claim.vehicle ? `${claim.vehicle.year} ${claim.vehicle.make} ${claim.vehicle.model}` : "—"} />
              <KV k="VIN" v={claim.vehicle?.vin} />
              <KV k="Policy" v={claim.policy?.policy_number} />
              <KV k="Coverage" v={claim.policy?.coverage_type} />
              <KV k="In force" v={claim.policy?.in_force ? "Yes" : "No"} />
              <KV k="Customer" v={claim.customer?.name} />
              <KV k="Phone" v={claim.customer?.phone} />
            </div>

            <div className="card">
              <h3>Nearby repair shops (geo)</h3>
              <button onClick={findShops}>Find shops near incident</button>
              {shops && (
                <div style={{ marginTop: 10 }}>
                  <div className="muted" style={{ marginBottom: 6 }}>{shops.count} within {shops.radius_km} km</div>
                  {shops.shops.slice(0, 6).map((s) => (
                    <div key={s.id} className="kv">
                      <span className="k">{s.name} {s.in_network && <span className="badge gray">network</span>}</span>
                      <span>{s.distance_km} km</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* right column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="card">
              <h3>Conversation {connected && <span className="muted" style={{ fontSize: 11 }}>· live</span>}</h3>
              <div className="conv" ref={convRef}>
                {msgs.map((m) => (
                  <div key={m.id} className={`msg ${m.sender_type} ${m.id === flashId ? "flash" : ""}`}>
                    <div className="meta">
                      {m.sender_name} · {m.channel}
                      {m.source === "voice_transcript" && <span className="voice"> · 🎙 voice→text</span>}
                    </div>
                    {m.content}
                  </div>
                ))}
              </div>
              <div className="row-actions" style={{ marginTop: 10 }}>
                <input style={{ flex: 1 }} placeholder="Add a note…" value={note}
                  onChange={(e) => setNote(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && submitNote()} />
                <button className="primary" onClick={submitNote}>Send</button>
              </div>
            </div>

            <div className="card">
              <h3>Timeline</h3>
              {events.map((e, i) => (
                <div key={i} className="kv">
                  <span className="k">{e.detail}</span>
                  <span className="muted" style={{ fontSize: 11 }}>{e.actor}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function KV({ k, v }) {
  return <div className="kv"><span className="k">{k}</span><span>{v ?? "—"}</span></div>;
}
