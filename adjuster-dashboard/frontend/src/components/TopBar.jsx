import { useNavigate, Link } from "react-router-dom";
import { getUser, clearAuth } from "../api";

const ROLE_LABEL = {
  adjuster: "Adjuster",
  senior_adjuster: "Senior Adjuster",
  siu_investigator: "SIU Investigator",
  admin: "Admin",
};

export default function TopBar({ connected }) {
  const user = getUser();
  const nav = useNavigate();
  return (
    <div className="topbar">
      <div className="title">
        <Link to="/">HomeSite Claims</Link>
        <Link to="/copilot" style={{ marginLeft: 16, fontSize: 13, fontWeight: 500 }}>🤖 Copilot</Link>
      </div>
      <div className="right">
        <span><span className={`dot ${connected ? "on" : "off"}`} /> {connected ? "live" : "offline"}</span>
        <span className="role-chip">{ROLE_LABEL[user?.role] || user?.role}</span>
        <span>{user?.name}</span>
        <button onClick={() => { clearAuth(); nav("/login"); }}>Sign out</button>
      </div>
    </div>
  );
}

export function StatusBadge({ status }) {
  const cls = `badge b-${status.replace(/ /g, ".")}`;
  return <span className={cls}>{status}</span>;
}
