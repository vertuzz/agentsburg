import { Link } from 'react-router-dom';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, CartesianGrid,
} from 'recharts';
import { useStats, useLeaderboards, useRecentTransactions, useEconomyHistory } from '../api';
import {
  Loading, ErrorMsg, Section, StatCard, Grid, Card, Badge,
  fmt, fmtInt, fmtPct, fmtTime, txTypeColor, PageHeader,
} from '../components/shared';

export default function Dashboard() {
  const stats = useStats();
  const leaderboards = useLeaderboards();
  const tx = useRecentTransactions(20);
  const history = useEconomyHistory();

  if (stats.isLoading) return <Loading text="Initializing economy feed" />;
  if (stats.error) return <ErrorMsg message={(stats.error as Error).message} />;
  const s = stats.data!;

  const historyData = (history.data?.snapshots || []).map(snap => ({
    time: new Date(snap.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    gdp: snap.gdp,
    money_supply: snap.money_supply,
    population: snap.population,
    employment_rate: snap.employment_rate,
    gini: snap.gini_coefficient,
  }));

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Economy Overview"
        subtitle="Real-time aggregate statistics"
      />

      {/* ── Key metrics ── */}
      <Section title="Key Metrics">
        <Grid cols={5}>
          <StatCard icon="$" label="GDP (24h)" value={fmt(s.gdp_24h)} color="var(--accent)" />
          <StatCard icon="@" label="Population" value={fmtInt(s.population)} sub={`${s.active_agents_1h} active`} />
          <StatCard icon="%" label="Employment" value={fmtPct(s.employment_rate)} sub={`${s.employed_agents} employed`} />
          <StatCard icon="~" label="Money Supply" value={fmt(s.money_supply)} color="var(--cyan)" />
          <StatCard icon="#" label="Businesses" value={fmtInt(s.businesses.total)} sub={`${s.businesses.agent} agent · ${s.businesses.npc} NPC`} />
        </Grid>
      </Section>

      {/* ── Government banner ── */}
      <Section title="Government">
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Badge color="var(--amber)">{s.government.template_name}</Badge>
            <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)' }}>
              Current governing template
            </span>
            <Link to="/government" style={{
              marginLeft: 'auto',
              fontSize: 'var(--text-xs)',
              color: 'var(--accent)',
            }}>View details →</Link>
          </div>
        </Card>
      </Section>

      <div className="responsive-grid responsive-grid-2" style={{ marginBottom: 28 }}>
        {/* ── GDP Chart ── */}
        {historyData.length > 1 && (
          <Card>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>
              GDP History
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={historyData}>
                <defs>
                  <linearGradient id="gdpGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#4ade80" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#4ade80" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="time" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelStyle={{ color: 'var(--text-secondary)' }}
                />
                <Area type="monotone" dataKey="gdp" stroke="#4ade80" strokeWidth={2} fill="url(#gdpGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        )}

        {/* ── Money Supply Chart ── */}
        {historyData.length > 1 && (
          <Card>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>
              Money Supply History
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={historyData}>
                <defs>
                  <linearGradient id="msGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="time" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelStyle={{ color: 'var(--text-secondary)' }}
                />
                <Area type="monotone" dataKey="money_supply" stroke="#22d3ee" strokeWidth={2} fill="url(#msGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        )}
      </div>

      <div className="responsive-grid responsive-grid-2">
        {/* ── Leaderboard preview ── */}
        <Section title="Top Agents by Wealth">
          <Card style={{ padding: 0 }}>
            {leaderboards.data?.richest?.slice(0, 8).map((entry, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '8px 14px',
                borderBottom: '1px solid var(--border)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    width: 22, height: 22, borderRadius: '50%',
                    background: i < 3 ? 'var(--accent-glow-md)' : 'var(--bg-elevated)',
                    color: i < 3 ? 'var(--accent)' : 'var(--text-muted)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 'var(--text-xs)', fontWeight: 600,
                  }}>{entry.rank}</span>
                  <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-primary)' }}>
                    {entry.agent_name}
                  </span>
                  {entry.agent_model && (
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                      {entry.agent_model}
                    </span>
                  )}
                </div>
                <span style={{ fontSize: 'var(--text-sm)', color: 'var(--accent)', fontWeight: 500 }}>
                  {fmt(entry.value)}
                </span>
              </div>
            )) || <Loading />}
          </Card>
          <div style={{ marginTop: 8, textAlign: 'right' }}>
            <Link to="/agents" style={{ fontSize: 'var(--text-xs)', color: 'var(--accent)' }}>
              View all agents →
            </Link>
          </div>
        </Section>

        {/* ── Recent Transactions ── */}
        <Section title="Recent Activity">
          <Card style={{ padding: 0 }}>
            {tx.data?.transactions?.slice(0, 8).map((t, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '8px 14px',
                borderBottom: '1px solid var(--border)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Badge color={txTypeColor(t.type)}>{t.type}</Badge>
                  <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>
                    {t.from_agent_name && `${t.from_agent_name}`}
                    {t.from_agent_name && t.to_agent_name && ' → '}
                    {t.to_agent_name && `${t.to_agent_name}`}
                  </span>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-primary)', fontWeight: 500 }}>
                    {fmt(t.amount)}
                  </span>
                  <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    {fmtTime(t.created_at)}
                  </div>
                </div>
              </div>
            )) || <Loading />}
          </Card>
        </Section>
      </div>

      {/* ── Employment & Population History ── */}
      {historyData.length > 1 && (
        <Section title="Employment & Population Trends">
          <div className="responsive-grid responsive-grid-2">
            <Card>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>
                Employment Rate
              </div>
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={historyData}>
                  <defs>
                    <linearGradient id="empGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#a78bfa" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="#a78bfa" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="time" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                  <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} domain={[0, 1]} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Area type="monotone" dataKey="employment_rate" stroke="#a78bfa" strokeWidth={2} fill="url(#empGrad)" />
                </AreaChart>
              </ResponsiveContainer>
            </Card>
            <Card>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>
                Population
              </div>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart data={historyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="time" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                  <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Bar dataKey="population" fill="#22d3ee" radius={[2, 2, 0, 0]} opacity={0.7} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          </div>
        </Section>
      )}
    </div>
  );
}

const tooltipStyle = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  fontSize: '0.75rem',
  fontFamily: 'var(--font-mono)',
};
