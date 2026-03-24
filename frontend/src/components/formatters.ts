/* ═══════════════════════════════════════════════════════
   Shared formatting helpers & non-component utilities
   ═══════════════════════════════════════════════════════ */

export function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (Math.abs(n) >= 10_000) return (n / 1_000).toFixed(1) + "K";
  return n.toFixed(decimals);
}

export function fmtInt(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString();
}

export function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return (n * 100).toFixed(1) + "%";
}

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function slugToName(slug: string): string {
  return slug.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function tierColor(tier: number): string {
  if (tier === 1) return "var(--text-secondary)";
  if (tier === 2) return "var(--cyan)";
  return "var(--amber)";
}

export function txTypeColor(type: string): string {
  const colors: Record<string, string> = {
    wage: "var(--accent)",
    gathering: "var(--accent)",
    storefront: "var(--cyan)",
    marketplace: "var(--cyan)",
    trade: "var(--purple)",
    rent: "var(--danger)",
    food: "var(--danger)",
    tax: "var(--amber)",
    fine: "var(--danger)",
    loan_payment: "var(--amber)",
    deposit_interest: "var(--accent)",
    business_reg: "var(--purple)",
    bankruptcy_liquidation: "var(--danger)",
  };
  return colors[type] || "var(--text-secondary)";
}
