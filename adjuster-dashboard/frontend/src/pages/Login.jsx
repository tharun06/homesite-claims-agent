import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setAuth } from "../api";

const ROLE_LABEL = {
  adjuster: "Adjuster",
  senior_adjuster: "Senior Adjuster (Team Lead)",
  siu_investigator: "SIU Investigator",
  admin: "Admin / Manager",
};

export default function Login() {
  const [users, setUsers] = useState([]);
  const [roleFilter, setRoleFilter] = useState("");
  const nav = useNavigate();

  useEffect(() => {
    api.loginUsers().then(setUsers).catch(console.error);
  }, []);

  async function pick(email) {
    const r = await api.login(email);
    setAuth(r.access_token, r.user);
    nav("/");
  }

  const shown = roleFilter ? users.filter((u) => u.role === roleFilter) : users;

  return (
    <div className="login-wrap">
      <h1>HomeSite Adjuster Dashboard</h1>
      <p className="muted">Pick a user to sign in (mock auth — no password).</p>
      <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
        <option value="">All roles ({users.length})</option>
        {Object.keys(ROLE_LABEL).map((r) => (
          <option key={r} value={r}>{ROLE_LABEL[r]}</option>
        ))}
      </select>
      <div className="user-list">
        {shown.map((u) => (
          <div key={u.id} className="user-row" onClick={() => pick(u.email)}>
            <div>
              <div style={{ fontWeight: 600 }}>{u.name}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {ROLE_LABEL[u.role]}{u.team ? ` · ${u.team}` : ""}
              </div>
            </div>
            <button className="primary">Sign in</button>
          </div>
        ))}
      </div>
    </div>
  );
}
