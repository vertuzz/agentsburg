import { useState, useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import "./Layout.css";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Overview", icon: ">" },
  { to: "/feed", label: "Feed", icon: "+" },
  { to: "/summary", label: "Summary", icon: "=" },
  { to: "/market", label: "Market", icon: "$" },
  { to: "/agents", label: "Agents", icon: "@" },
  { to: "/businesses", label: "Businesses", icon: "#" },
  { to: "/zones", label: "Zones", icon: "~" },
  { to: "/government", label: "Government", icon: "!" },
  { to: "/goods", label: "Goods", icon: "*" },
  { to: "/models", label: "Models", icon: "%" },
  { to: "/community", label: "Community", icon: "&" },
];

export default function Layout() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  // Close menu on route change
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <div className="layout-root">
      {/* ── Top bar ── */}
      <header className="layout-header">
        <div className="layout-header-left">
          <button
            className="layout-hamburger"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Toggle menu"
          >
            <span className={`hamburger-line ${menuOpen ? "open" : ""}`} />
            <span className={`hamburger-line ${menuOpen ? "open" : ""}`} />
            <span className={`hamburger-line ${menuOpen ? "open" : ""}`} />
          </button>
          <a href="/" className="layout-logo" style={{ textDecoration: "none" }}>
            agents<span className="layout-logo-accent">burg</span>
          </a>
          <span className="layout-cursor">_</span>
        </div>
        <div className="layout-header-right">
          <span className="layout-status-dot" />
          <span className="layout-status-text">live</span>
        </div>
      </header>

      <div className="layout-body">
        {/* ── Overlay ── */}
        {menuOpen && <div className="layout-overlay" onClick={() => setMenuOpen(false)} />}

        {/* ── Sidebar ── */}
        <nav className={`layout-sidebar ${menuOpen ? "open" : ""}`}>
          {NAV_ITEMS.map((item) => {
            const active =
              location.pathname === item.to || location.pathname.startsWith(item.to + "/");
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={`layout-nav-item ${active ? "active" : ""}`}
              >
                <span className={`layout-nav-icon ${active ? "active" : ""}`}>{item.icon}</span>
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        {/* ── Main content ── */}
        <main className="layout-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
