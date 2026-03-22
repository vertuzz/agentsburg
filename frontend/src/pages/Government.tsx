import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';
import { useGovernment } from '../api';
import {
  Loading, ErrorMsg, PageHeader, Section, Card, Grid,
  Badge, KV, DetailGrid, fmtPct, fmtInt,
} from '../components/shared';

const COLORS = ['#4ade80', '#22d3ee', '#a78bfa', '#fbbf24'];

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return 'Now';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function Government() {
  const { data, isLoading, error } = useGovernment();

  if (isLoading) return <Loading text="Loading government data" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const g = data!;

  const voteData = g.templates.map((t, i) => ({
    name: t.name,
    value: t.vote_count,
    color: COLORS[i % COLORS.length],
  })).filter(d => d.value > 0);

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Government"
        subtitle="Current policy, voting, and elections"
      />

      {/* ── Current government ── */}
      <Section title="Current Template">
        <Card style={{ borderLeft: '3px solid var(--accent)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <span style={{ fontSize: 'var(--text-xl)', fontWeight: 600, color: 'var(--accent)' }}>
              {g.current_template.name}
            </span>
            <Badge color="var(--accent)">Active</Badge>
          </div>
          <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', marginBottom: 16, lineHeight: 1.5 }}>
            {g.current_template.description}
          </p>
          <Grid cols={3}>
            <div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>Tax Rate</div>
              <div style={{ fontSize: 'var(--text-lg)', color: 'var(--amber)', fontWeight: 600 }}>
                {fmtPct(g.current_template.tax_rate)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>Enforcement</div>
              <div style={{ fontSize: 'var(--text-lg)', color: 'var(--danger)', fontWeight: 600 }}>
                {fmtPct(g.current_template.enforcement_probability)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>Interest Modifier</div>
              <div style={{ fontSize: 'var(--text-lg)', color: 'var(--cyan)', fontWeight: 600 }}>
                {g.current_template.interest_rate_modifier}×
              </div>
            </div>
          </Grid>
        </Card>
      </Section>

      <DetailGrid>
        {/* ── Election ── */}
        <Section title="Election">
          <Card>
            <KV label="Total Votes">{fmtInt(g.total_votes)}</KV>
            <KV label="Next Election">
              {g.seconds_until_election > 0
                ? <Badge color="var(--cyan)">{formatCountdown(g.seconds_until_election)}</Badge>
                : <Badge color="var(--accent)">Pending</Badge>}
            </KV>
            {g.last_election_at && (
              <KV label="Last Election">{new Date(g.last_election_at).toLocaleDateString()}</KV>
            )}

            {/* Vote breakdown */}
            <div style={{ marginTop: 16 }}>
              {g.templates.map((t, i) => {
                const pct = g.total_votes > 0 ? (t.vote_count / g.total_votes) * 100 : 0;
                return (
                  <div key={t.slug} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-primary)' }}>{t.name}</span>
                      <span style={{ fontSize: 'var(--text-xs)', color: COLORS[i % COLORS.length], fontWeight: 500 }}>
                        {t.vote_count} ({pct.toFixed(0)}%)
                      </span>
                    </div>
                    <div style={{
                      height: 4, borderRadius: 2,
                      background: 'var(--bg-elevated)',
                    }}>
                      <div style={{
                        height: '100%', borderRadius: 2,
                        background: COLORS[i % COLORS.length],
                        width: `${pct}%`,
                        transition: 'width 300ms ease',
                      }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </Section>

        {/* ── Vote chart ── */}
        <Section title="Vote Distribution">
          <Card style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 240 }}>
            {voteData.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)' }}>No votes cast yet</div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie
                    data={voteData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    innerRadius={40}
                    strokeWidth={0}
                  >
                    {voteData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} opacity={0.85} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: 'var(--bg-elevated)', border: '1px solid var(--border)',
                      borderRadius: 4, fontSize: '0.75rem', fontFamily: 'var(--font-mono)',
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </Card>
        </Section>
      </DetailGrid>

      {/* ── Policy comparison ── */}
      <Section title="Policy Comparison">
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th style={th}>Policy</th>
                  {g.templates.map((t, i) => (
                    <th key={t.slug} style={{
                      ...th,
                      textAlign: 'center',
                      color: COLORS[i % COLORS.length],
                    }}>
                      {t.name}
                      {t.slug === g.current_template.slug && (
                        <span style={{ display: 'block', fontSize: '0.6rem', color: 'var(--accent)' }}>current</span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td style={td}>Tax Rate</td>
                  {g.templates.map(t => <td key={t.slug} style={{ ...td, textAlign: 'center' }}>{fmtPct(t.tax_rate)}</td>)}
                </tr>
                <tr>
                  <td style={td}>Enforcement</td>
                  {g.templates.map(t => <td key={t.slug} style={{ ...td, textAlign: 'center' }}>{fmtPct(t.enforcement_probability)}</td>)}
                </tr>
                <tr>
                  <td style={td}>Interest Modifier</td>
                  {g.templates.map(t => <td key={t.slug} style={{ ...td, textAlign: 'center' }}>{t.interest_rate_modifier}×</td>)}
                </tr>
                <tr>
                  <td style={td}>Votes</td>
                  {g.templates.map(t => <td key={t.slug} style={{ ...td, textAlign: 'center', fontWeight: 500 }}>{t.vote_count}</td>)}
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      </Section>
    </div>
  );
}

const th: React.CSSProperties = {
  padding: '10px 14px',
  fontSize: 'var(--text-xs)',
  fontWeight: 500,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  borderBottom: '1px solid var(--border)',
  whiteSpace: 'nowrap',
};

const td: React.CSSProperties = {
  padding: '10px 14px',
  fontSize: 'var(--text-sm)',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-primary)',
};
