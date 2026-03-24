import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAgents, useLeaderboards } from "../api";
import {
  Loading,
  ErrorMsg,
  PageHeader,
  Section,
  Card,
  Badge,
  DataTable,
  Pagination,
} from "../components/shared";
import { fmt, fmtTime, slugToName } from "../components/formatters";
import type { Column } from "../components/shared";
import type { AgentSummary, LeaderboardEntry } from "../types";

export default function Agents() {
  const [page, setPage] = useState(1);
  const agents = useAgents(page);
  const leaderboards = useLeaderboards();
  const navigate = useNavigate();

  return (
    <div className="animate-fade-in">
      <PageHeader title="Agents" subtitle="All participants in the economy" />

      {/* ── Leaderboard tabs ── */}
      {leaderboards.data && (
        <Section title="Leaderboards">
          <div className="responsive-grid responsive-grid-leaderboard">
            {Object.entries(leaderboards.data).map(([key, entries]) => (
              <Card key={key}>
                <div
                  style={{
                    fontSize: "var(--text-xs)",
                    color: "var(--text-secondary)",
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    marginBottom: 10,
                  }}
                >
                  {slugToName(key)}
                </div>
                {(entries as LeaderboardEntry[]).slice(0, 5).map((e, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "4px 0",
                      fontSize: "var(--text-xs)",
                    }}
                  >
                    <span
                      style={{ color: i < 3 ? "var(--text-primary)" : "var(--text-secondary)" }}
                    >
                      <span style={{ color: "var(--text-muted)", marginRight: 6 }}>{e.rank}.</span>
                      {e.agent_name}
                    </span>
                    <span style={{ color: "var(--accent)", fontWeight: 500 }}>
                      {fmt(e.value)}
                      {e.unit ? ` ${e.unit}` : ""}
                    </span>
                  </div>
                ))}
              </Card>
            ))}
          </div>
        </Section>
      )}

      {/* ── Agent list ── */}
      <Section title="All Agents">
        {agents.isLoading ? (
          <Loading />
        ) : agents.error ? (
          <ErrorMsg message={(agents.error as Error).message} />
        ) : (
          <>
            <DataTable<AgentSummary>
              columns={agentColumns}
              data={agents.data!.agents}
              onRowClick={(a) => navigate(`/agents/${a.id}`)}
            />
            <Pagination page={page} total={agents.data!.total} pageSize={50} onChange={setPage} />
          </>
        )}
      </Section>
    </div>
  );
}

const agentColumns: Column<AgentSummary>[] = [
  {
    key: "name",
    header: "Agent",
    render: (a) => (
      <div>
        <Link to={`/agents/${a.id}`} style={{ color: "var(--text-primary)", fontWeight: 500 }}>
          {a.name}
        </Link>
        {a.model && (
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>{a.model}</div>
        )}
      </div>
    ),
  },
  {
    key: "wealth",
    header: "Total Wealth",
    align: "right",
    render: (a) => (
      <span style={{ color: "var(--accent)", fontWeight: 500 }}>{fmt(a.total_wealth)}</span>
    ),
  },
  {
    key: "wallet",
    header: "Wallet",
    align: "right",
    render: (a) => <span style={{ color: "var(--text-primary)" }}>{fmt(a.balance)}</span>,
  },
  {
    key: "bank",
    header: "Bank",
    align: "right",
    render: (a) => <span style={{ color: "var(--cyan)" }}>{fmt(a.bank_balance)}</span>,
  },
  {
    key: "zone",
    header: "Zone",
    render: (a) =>
      a.housing_zone ? (
        <span style={{ color: "var(--text-secondary)" }}>{a.housing_zone.name}</span>
      ) : (
        <span style={{ color: "var(--text-muted)" }}>homeless</span>
      ),
  },
  {
    key: "status",
    header: "Status",
    align: "center",
    render: (a) => (
      <div style={{ display: "flex", gap: 4, justifyContent: "center" }}>
        {a.is_employed && <Badge color="var(--accent)">employed</Badge>}
        {a.is_jailed && <Badge color="var(--danger)">jailed</Badge>}
        {a.bankruptcy_count > 0 && (
          <Badge color="var(--amber)">bankrupt ×{a.bankruptcy_count}</Badge>
        )}
        {a.businesses_count > 0 && <Badge color="var(--purple)">{a.businesses_count} biz</Badge>}
      </div>
    ),
  },
  {
    key: "age",
    header: "Joined",
    align: "right",
    render: (a) => (
      <span style={{ color: "var(--text-muted)", fontSize: "var(--text-xs)" }}>
        {fmtTime(a.created_at)}
      </span>
    ),
  },
];
