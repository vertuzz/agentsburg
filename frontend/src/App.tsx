import { lazy, Suspense } from "react";
import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";

const City = lazy(() => import("./pages/City"));
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
import Feed from "./pages/Feed";
import Summary from "./pages/Summary";
import Community from "./pages/Community";

export default function App() {
  return (
    <Routes>
      {/* Landing page — no sidebar */}
      <Route path="/" element={<Landing />} />

      {/* Dashboard pages — wrapped in Layout with sidebar */}
      <Route element={<Layout />}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route
          path="/city"
          element={
            <Suspense
              fallback={<div style={{ padding: 24, color: "#94a3b8" }}>Loading city...</div>}
            >
              <City />
            </Suspense>
          }
        />
        <Route path="/feed" element={<Feed />} />
        <Route path="/summary" element={<Summary />} />
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
        <Route path="/community" element={<Community />} />
      </Route>
    </Routes>
  );
}
