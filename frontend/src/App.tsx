import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";

import Navbar from "./components/Navbar";
import PublicDashboard from "./pages/PublicDashboard";
import AgentDashboard from "./pages/AgentDashboard";
import MarketDetail from "./pages/MarketDetail";

export default function App() {
  return (
    <BrowserRouter>
      <Navbar />
      <Routes>
        {/* Public dashboard — no auth required */}
        <Route path="/" element={<PublicDashboard />} />

        {/* Per-good market detail page */}
        <Route path="/market/:good" element={<MarketDetail />} />

        {/* Private agent dashboard — view_token in query param */}
        <Route path="/dashboard" element={<AgentDashboard />} />

        {/* Catch-all redirect to home */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
