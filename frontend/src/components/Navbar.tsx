import { Link, useLocation } from "react-router-dom";

export default function Navbar() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const token = params.get("token");

  const isActive = (path: string) => {
    if (path === "/") return location.pathname === "/";
    return location.pathname.startsWith(path);
  };

  return (
    <nav className="navbar">
      <div className="navbar-inner">
        <Link to="/" className="navbar-brand">
          <div className="brand-icon">⚡</div>
          <span>Agent Economy</span>
        </Link>

        <div className="navbar-links">
          <Link
            to="/"
            className={`navbar-link ${isActive("/") && !isActive("/market") && !isActive("/dashboard") ? "active" : ""}`}
          >
            Dashboard
          </Link>
          <Link
            to="/market/wheat"
            className={`navbar-link ${isActive("/market") ? "active" : ""}`}
          >
            Markets
          </Link>
          {token && (
            <Link
              to={`/dashboard?token=${token}`}
              className={`navbar-link ${isActive("/dashboard") ? "active" : ""}`}
            >
              My Agent
            </Link>
          )}
        </div>

        <span className="navbar-tagline">Powered by AI Agents</span>
      </div>
    </nav>
  );
}
