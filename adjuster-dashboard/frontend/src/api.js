// Thin API client. Token stored in localStorage; every call sends it.
const BASE = "http://localhost:8100";

export function getToken() {
  return localStorage.getItem("token");
}
export function getUser() {
  const u = localStorage.getItem("user");
  return u ? JSON.parse(u) : null;
}
export function setAuth(token, user) {
  localStorage.setItem("token", token);
  localStorage.setItem("user", JSON.stringify(user));
}
export function clearAuth() {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
}

async function req(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { ...opts, headers });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

function form(obj) {
  const fd = new FormData();
  Object.entries(obj).forEach(([k, v]) => fd.append(k, v));
  return fd;
}

export const api = {
  // auth
  loginUsers: () => req("/auth/users"),
  login: (email) => req("/auth/login", { method: "POST", body: form({ email }) }),
  me: () => req("/me"),
  // claims
  claims: (q = "") => req(`/claims${q}`),
  claim: (id) => req(`/claims/${id}`),
  conversations: (id) => req(`/claims/${id}/conversations`),
  events: (id) => req(`/claims/${id}/events`),
  setStatus: (id, s) =>
    req(`/claims/${id}/status`, { method: "PATCH", body: form({ new_status: s }) }),
  reassign: (id, adjuster_id) =>
    req(`/claims/${id}/reassign`, { method: "POST", body: form({ adjuster_id }) }),
  addNote: (id, note) =>
    req(`/claims/${id}/notes`, { method: "POST", body: form({ note }) }),
  // work
  metrics: () => req("/metrics/queue"),
  tasks: () => req("/me/tasks"),
  // directory
  vehicle: (vin) => req(`/vehicles/${vin}`),
  shopsNear: (claimId, radius = 1500) =>
    req(`/repair-shops?claim_id=${claimId}&radius_km=${radius}`),
};

export const WS_BASE = "ws://localhost:8100/ws";

// Copilot service (separate process, port 8200)
const COPILOT = "http://localhost:8200";

function threadId() {
  const user = getUser();
  return `user-${user?.id ?? "anon"}`;
}

async function copilotPost(path, body) {
  const res = await fetch(`${COPILOT}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error("copilot error");
  return res.json();
}

export const copilot = {
  chat: (message) => copilotPost("/chat", { message, thread_id: threadId() }),
  // approve/reject don't use `message`, but /approve and /reject both expect the same
  // {message, thread_id} shape as /chat, so we send a placeholder string.
  approve: () => copilotPost("/approve", { message: "approve", thread_id: threadId() }),
  reject: () => copilotPost("/reject", { message: "reject", thread_id: threadId() }),
  reset: () =>
    fetch(`${COPILOT}/chat/reset`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },
    }),
};
