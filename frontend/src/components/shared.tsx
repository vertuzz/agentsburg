/* ═══════════════════════════════════════════════════════
   Shared UI components — DataCard, Badge, Loading,
   Pagination, StatRow, Section, DataTable, MiniChart
   ═══════════════════════════════════════════════════════ */

import type { ReactNode, CSSProperties } from "react";
import { Link } from "react-router-dom";
import { AreaChart, Area, ResponsiveContainer, YAxis } from "recharts";

/* ── Loading ── */
export function Loading({ text = "Loading" }: { text?: string }) {
  return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
      <span style={{ animation: "pulse 1.5s ease-in-out infinite" }}>{text}...</span>
    </div>
  );
}

export function ErrorMsg({ message }: { message: string }) {
  return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--danger)" }}>Error: {message}</div>
  );
}

/* ── Section header ── */
export function Section({
  title,
  children,
  right,
}: {
  title: string;
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <section style={{ marginBottom: 28 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <h2
          style={{
            fontSize: "var(--text-sm)",
            fontWeight: 500,
            color: "var(--text-secondary)",
            textTransform: "uppercase" as const,
            letterSpacing: "0.08em",
          }}
        >
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

/* ── Page header ── */
export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        marginBottom: 24,
        gap: 16,
      }}
    >
      <div>
        <h1
          style={{
            fontSize: "var(--text-xl)",
            fontWeight: 600,
            color: "var(--text-bright)",
            marginBottom: 4,
          }}
        >
          {title}
        </h1>
        {subtitle && (
          <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>{subtitle}</p>
        )}
      </div>
      {right}
    </div>
  );
}

/* ── Card ── */
export function Card({
  children,
  style: s,
  hover,
}: {
  children: ReactNode;
  style?: CSSProperties;
  hover?: boolean;
}) {
  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: 16,
        transition: hover ? "all 150ms ease" : undefined,
        cursor: hover ? "pointer" : undefined,
        ...s,
      }}
    >
      {children}
    </div>
  );
}

/* ── Stat card ── */
export function StatCard({
  label,
  value,
  sub,
  color,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  icon?: string;
}) {
  return (
    <Card style={{ minWidth: 0 }}>
      <div
        style={{
          fontSize: "var(--text-xs)",
          color: "var(--text-secondary)",
          textTransform: "uppercase" as const,
          letterSpacing: "0.06em",
          marginBottom: 6,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        {icon && <span style={{ color: color || "var(--accent)" }}>{icon}</span>}
        {label}
      </div>
      <div
        style={{
          fontSize: "var(--text-2xl)",
          fontWeight: 600,
          color: color || "var(--text-bright)",
          lineHeight: 1.2,
        }}
      >
        {value}
      </div>
      {sub && (
        <div
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--text-muted)",
            marginTop: 4,
          }}
        >
          {sub}
        </div>
      )}
    </Card>
  );
}

/* ── Grid layouts ── */
export function Grid({ children, cols = 4 }: { children: ReactNode; cols?: number }) {
  return <div className={`responsive-grid responsive-grid-${cols}`}>{children}</div>;
}

/* ── Badge ── */
export function Badge({
  children,
  color = "var(--text-secondary)",
  bg,
}: {
  children: ReactNode;
  color?: string;
  bg?: string;
}) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        fontSize: "var(--text-xs)",
        fontWeight: 500,
        color,
        background: bg || "var(--bg-elevated)",
        border: `1px solid ${color}33`,
        borderRadius: "var(--radius-sm)",
      }}
    >
      {children}
    </span>
  );
}

/* ── Pill link ── */
export function PillLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      style={{
        fontSize: "var(--text-xs)",
        color: "var(--accent)",
        padding: "4px 10px",
        background: "var(--accent-glow)",
        borderRadius: "var(--radius-sm)",
        border: "1px solid var(--accent-muted)",
        textDecoration: "none",
        transition: "all 150ms ease",
      }}
    >
      {children} →
    </Link>
  );
}

/* ── DataTable ── */
export interface Column<T> {
  key: string;
  header: string;
  render: (item: T, index: number) => ReactNode;
  align?: "left" | "right" | "center";
  width?: string;
}

export function DataTable<T>({
  columns,
  data,
  emptyText = "No data",
  onRowClick,
}: {
  columns: Column<T>[];
  data: T[];
  emptyText?: string;
  onRowClick?: (item: T) => void;
}) {
  if (data.length === 0) {
    return (
      <Card>
        <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
          {emptyText}
        </div>
      </Card>
    );
  }

  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  style={{
                    ...thStyle,
                    textAlign: col.align || "left",
                    width: col.width,
                  }}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((item, i) => (
              <tr
                key={i}
                onClick={() => onRowClick?.(item)}
                style={{
                  cursor: onRowClick ? "pointer" : undefined,
                  transition: "background 100ms ease",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = "transparent";
                }}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    style={{
                      ...tdStyle,
                      textAlign: col.align || "left",
                    }}
                  >
                    {col.render(item, i)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const thStyle: CSSProperties = {
  padding: "10px 14px",
  fontSize: "var(--text-xs)",
  fontWeight: 500,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
};

const tdStyle: CSSProperties = {
  padding: "10px 14px",
  fontSize: "var(--text-sm)",
  borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap",
};

/* ── Pagination ── */
export function Pagination({
  page,
  total,
  pageSize,
  onChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onChange: (p: number) => void;
}) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        marginTop: 16,
      }}
    >
      <button onClick={() => onChange(page - 1)} disabled={page <= 1} style={paginationBtn}>
        ← prev
      </button>
      <span style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
        {page} / {totalPages}
      </span>
      <button
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
        style={paginationBtn}
      >
        next →
      </button>
    </div>
  );
}

const paginationBtn: CSSProperties = {
  padding: "4px 12px",
  fontSize: "var(--text-xs)",
  fontFamily: "var(--font-mono)",
  color: "var(--text-secondary)",
  background: "var(--bg-elevated)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  cursor: "pointer",
};

/* ── MiniChart (sparkline) ── */
export function MiniChart({
  data,
  color = "#4ade80",
  height = 40,
}: {
  data: { value: number }[];
  color?: string;
  height?: number;
}) {
  if (!data.length) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <defs>
          <linearGradient id={`mini-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <YAxis domain={["dataMin", "dataMax"]} hide />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#mini-${color.replace("#", "")})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ── Empty state ── */
export function EmptyState({ message }: { message: string }) {
  return (
    <div
      style={{
        padding: 60,
        textAlign: "center",
        color: "var(--text-muted)",
        fontSize: "var(--text-sm)",
      }}
    >
      <div style={{ fontSize: "2rem", marginBottom: 12, opacity: 0.3 }}>∅</div>
      {message}
    </div>
  );
}

/* ── Two-column detail layout ── */
export function DetailGrid({ children }: { children: ReactNode }) {
  return <div className="responsive-grid responsive-grid-2">{children}</div>;
}

/* ── Key-value row ── */
export function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "6px 0",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <span style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>{label}</span>
      <span style={{ fontSize: "var(--text-sm)", color: "var(--text-primary)", fontWeight: 500 }}>
        {children}
      </span>
    </div>
  );
}
