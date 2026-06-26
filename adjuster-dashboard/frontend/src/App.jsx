import { Routes, Route, Navigate } from "react-router-dom";
import { getToken } from "./api";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import ClaimDetail from "./pages/ClaimDetail.jsx";
import Copilot from "./pages/Copilot.jsx";

function RequireAuth({ children }) {
  return getToken() ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
      <Route path="/claims/:id" element={<RequireAuth><ClaimDetail /></RequireAuth>} />
      <Route path="/copilot" element={<RequireAuth><Copilot /></RequireAuth>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
