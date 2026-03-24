import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";
import Market from "./pages/Market";
import MarketDetail from "./pages/MarketDetail";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Businesses from "./pages/Businesses";
import BusinessDetail from "./pages/BusinessDetail";
import Zones from "./pages/Zones";
import Government from "./pages/Government";
import Goods from "./pages/Goods";
import Models from "./pages/Models";

export default function App() {
  return (
    <Routes>
      {/* Landing page — no sidebar */}
      <Route path="/" element={<Landing />} />

      {/* Dashboard pages — wrapped in Layout with sidebar */}
      <Route element={<Layout />}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/market" element={<Market />} />
        <Route path="/market/:good" element={<MarketDetail />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/businesses" element={<Businesses />} />
        <Route path="/businesses/:id" element={<BusinessDetail />} />
        <Route path="/zones" element={<Zones />} />
        <Route path="/government" element={<Government />} />
        <Route path="/goods" element={<Goods />} />
        <Route path="/models" element={<Models />} />
      </Route>
    </Routes>
  );
}
