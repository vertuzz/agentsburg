import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useBusinesses, useZones } from '../api';
import {
  Loading, ErrorMsg, PageHeader, Section, DataTable, Pagination,
  Badge, fmtTime, slugToName,
} from '../components/shared';
import type { Column } from '../components/shared';
import type { BusinessSummary } from '../types';

export default function Businesses() {
  const [page, setPage] = useState(1);
  const [zoneFilter, setZoneFilter] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const businesses = useBusinesses(page, zoneFilter || undefined, typeFilter || undefined);
  const zones = useZones();
  const navigate = useNavigate();

  // Get unique business types from data
  const types = [...new Set(businesses.data?.businesses.map(b => b.type_slug) || [])].sort();

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Businesses"
        subtitle="All registered businesses in the economy"
      />

      {/* ── Filters ── */}
      <Section title="Filters" right={
        businesses.data && <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          {businesses.data.total} total
        </span>
      }>
        <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
          <select
            value={zoneFilter}
            onChange={e => { setZoneFilter(e.target.value); setPage(1); }}
            style={selectStyle}
          >
            <option value="">All zones</option>
            {zones.data?.zones.map(z => (
              <option key={z.slug} value={z.slug}>{z.name}</option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={e => { setTypeFilter(e.target.value); setPage(1); }}
            style={selectStyle}
          >
            <option value="">All types</option>
            {types.map(t => (
              <option key={t} value={t}>{slugToName(t)}</option>
            ))}
          </select>
        </div>
      </Section>

      {/* ── Business list ── */}
      {businesses.isLoading ? <Loading /> : businesses.error ? <ErrorMsg message={(businesses.error as Error).message} /> : (
        <>
          <DataTable<BusinessSummary>
            columns={bizColumns}
            data={businesses.data!.businesses}
            onRowClick={(b) => navigate(`/businesses/${b.id}`)}
            emptyText="No businesses match your filters"
          />
          <Pagination
            page={page}
            total={businesses.data!.total}
            pageSize={50}
            onChange={setPage}
          />
        </>
      )}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  padding: '6px 12px',
  fontSize: 'var(--text-sm)',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-primary)',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  outline: 'none',
  cursor: 'pointer',
};

const bizColumns: Column<BusinessSummary>[] = [
  {
    key: 'name', header: 'Business', render: (b) => (
      <div>
        <Link to={`/businesses/${b.id}`} style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
          {b.name}
        </Link>
        <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          {slugToName(b.type_slug)}
        </div>
      </div>
    ),
  },
  {
    key: 'owner', header: 'Owner',
    render: (b) => (
      <div>
        <Link to={`/agents/${b.owner_id}`} style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>
          {b.owner_name}
        </Link>
        {b.is_npc && <Badge color="var(--text-muted)">NPC</Badge>}
      </div>
    ),
  },
  {
    key: 'zone', header: 'Zone',
    render: (b) => <span style={{ color: 'var(--text-secondary)' }}>{b.zone.name}</span>,
  },
  {
    key: 'employees', header: 'Staff', align: 'center',
    render: (b) => <span style={{ color: 'var(--cyan)' }}>{b.employee_count}</span>,
  },
  {
    key: 'status', header: 'Status', align: 'center',
    render: (b) => b.is_open
      ? <Badge color="var(--accent)">open</Badge>
      : <Badge color="var(--danger)">closed</Badge>,
  },
  {
    key: 'age', header: 'Registered', align: 'right',
    render: (b) => <span style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs)' }}>{fmtTime(b.created_at)}</span>,
  },
];
