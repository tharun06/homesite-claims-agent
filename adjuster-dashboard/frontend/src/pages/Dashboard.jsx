import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, getUser } from "../api";
import { useWebSocket } from "../useWebSocket";
import TopBar, { StatusBadge } from "../components/TopBar.jsx";

const STATUSES = ["FNOL", "Under Review", "Investigation", "Appraisal",
  "Pending Approval", "Approved", "Denied", "Closed", "SIU Flagged"];

export default function Dashboard() {
  const [metrics, setMetrics] = useState(null);
  const [claims, setClaims] = useState([]);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [feed, setFeed] = useState([]);
  const nav = useNavigate();
  const user = getUser();

  const loadMetrics = useCallback(() => {
    api.metrics().then(setMetrics).catch(console.error);
  }, []);

  const loadClaims = useCallback(() => {
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (search) qs.set("search", search);
    qs.set("limit", "200");
    api.claims(`?${qs}`).then((r) => setClaims(r.claims)).catch(console.error);
  }, [status, search]);

  useEffect(() => { loadMetrics(); }, [loadMetrics]);
  useEffect(() => { loadClaims(); }, [loadClaims]);

  // realtime: metrics refresh + a live feed of events relevant to my scope
  const { connected } = useWebSocket((msg) => {
    if (msg.type === "metrics_tick") {
      loadMetrics();
      loadClaims();
      return;
    }
    const label =
      msg.type === "message" ? `💬 ${msg.payload.sender_name}: ${msg.payload.content.slice(0, 40)}…`
      : msg.type === "status_change" ? `🔄 Claim #${msg.claim_id} → ${msg.payload.status}`
      : msg.type === "assignment" ? `📋 Claim #${msg.claim_id} → ${msg.payload.adjuster_name}`
      : null;
    if (label) {
      setFeed((f) => [{ id: Date.now() + Math.random(), label }, ...f].slice(0, 5));
    }
  });

  return (
    <>
      <TopBar connected={connected} />
      <div className="container">
        {metrics && (
          <div className="metrics">
            <Metric n={metrics.total} l="Total in scope" />
            <Metric n={metrics.open} l="Open" />
            <Metric n={metrics.sla_breaches} l="SLA breaches" />
            <Metric n={metrics.fraud_flagged} l="Fraud flagged" />
          </div>
        )}

        <div className="filters">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input placeholder="Search claim #…" value={search}
            onChange={(e) => setSearch(e.target.value)} />
          <span className="muted">{claims.length} claims</span>
        </div>

        <div className="panel">
          <table>
            <thead>
              <tr>
                <th>Claim #</th><th>Status</th><th>Vehicle</th><th>Customer</th>
                <th>Peril</th><th>Estimate</th><th>Adjuster</th><th></th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c) => (
                <tr key={c.id} style={{ cursor: "pointer" }} onClick={() => nav(`/claims/${c.id}`)}>
                  <td>{c.claim_number}{c.fraud_flagged && <span className="badge fraud" style={{ marginLeft: 6 }}>fraud</span>}</td>
                  <td><StatusBadge status={c.status} /></td>
                  <td>{c.vehicle ? `${c.vehicle.year} ${c.vehicle.make} ${c.vehicle.model}` : "—"}</td>
                  <td>{c.customer?.name || "—"}</td>
                  <td>{c.peril_type}</td>
                  <td>${c.estimated_amount?.toLocaleString()}</td>
                  <td>{c.adjuster?.name || "—"}</td>
                  <td>→</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="feed">
        {feed.map((f) => <div key={f.id} className="item">{f.label}</div>)}
      </div>
    </>
  );
}

function Metric({ n, l }) {
  return <div className="metric"><div className="n">{n}</div><div className="l">{l}</div></div>;
}
