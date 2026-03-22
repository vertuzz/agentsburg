import { useParams, Link } from 'react-router-dom';
import { useBusiness } from '../api';
import {
  Loading, ErrorMsg, PageHeader, Section, Card, Badge,
  KV, DetailGrid, fmt, slugToName,
} from '../components/shared';

export default function BusinessDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useBusiness(id!);

  if (isLoading) return <Loading text="Loading business details" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const b = data!;

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={b.name}
        subtitle={`${slugToName(b.type_slug)} · ${b.zone.name}${b.is_npc ? ' · NPC-owned' : ''}`}
        right={<Link to="/businesses" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>← Back to businesses</Link>}
      />

      <DetailGrid>
        {/* ── Info ── */}
        <Section title="Details">
          <Card>
            <KV label="Owner">
              <Link to={`/agents/${b.owner_id}`} style={{ color: 'var(--accent)' }}>
                {b.owner_name}
              </Link>
            </KV>
            <KV label="Type">{slugToName(b.type_slug)}</KV>
            <KV label="Zone">{b.zone.name}</KV>
            <KV label="Status">{b.is_open ? <Badge color="var(--accent)">Open</Badge> : <Badge color="var(--danger)">Closed</Badge>}</KV>
            <KV label="Storage Capacity">{b.storage_capacity} units</KV>
            <KV label="NPC">{b.is_npc ? 'Yes' : 'No'}</KV>
          </Card>
        </Section>

        {/* ── Inventory ── */}
        <Section title="Inventory">
          <Card>
            {b.inventory.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)' }}>Empty</div>
            ) : (
              b.inventory.map((item, i) => (
                <div key={i} style={{
                  display: 'flex', justifyContent: 'space-between',
                  padding: '6px 0', borderBottom: '1px solid var(--border)',
                }}>
                  <Link to={`/market/${item.good_slug}`} style={{ color: 'var(--text-primary)', fontSize: 'var(--text-sm)' }}>
                    {slugToName(item.good_slug)}
                  </Link>
                  <span style={{ color: 'var(--accent)', fontWeight: 500, fontSize: 'var(--text-sm)' }}>
                    ×{item.quantity}
                  </span>
                </div>
              ))
            )}
          </Card>
        </Section>
      </DetailGrid>

      {/* ── Storefront Prices ── */}
      {b.storefront_prices.length > 0 && (
        <Section title="Storefront Prices">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
            {b.storefront_prices.map((sp, i) => (
              <Card key={i}>
                <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-primary)', marginBottom: 4 }}>
                  {slugToName(sp.good_slug)}
                </div>
                <div style={{ fontSize: 'var(--text-xl)', color: 'var(--accent)', fontWeight: 600 }}>
                  {fmt(sp.price)}
                </div>
              </Card>
            ))}
          </div>
        </Section>
      )}

      {/* ── Employees ── */}
      <Section title={`Employees (${b.employees.length})`}>
        {b.employees.length === 0 ? (
          <Card>
            <div style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)' }}>No employees</div>
          </Card>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 12 }}>
            {b.employees.map((emp, i) => (
              <Card key={i}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Link to={`/agents/${emp.agent_id}`} style={{ color: 'var(--text-primary)', fontWeight: 500, fontSize: 'var(--text-sm)' }}>
                    {emp.agent_name}
                  </Link>
                  <Badge color="var(--accent)">{fmt(emp.wage_per_work)}/work</Badge>
                </div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginTop: 4 }}>
                  Producing: {slugToName(emp.product_slug)}
                </div>
              </Card>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
