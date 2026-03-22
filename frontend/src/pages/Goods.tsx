import { Link } from 'react-router-dom';
import { useGoods } from '../api';
import {
  Loading, ErrorMsg, PageHeader, Section, Card, Badge,
  fmt, slugToName, tierColor,
} from '../components/shared';
import type { Good } from '../types';

// Known recipes (static — could be loaded from backend in the future)
const RECIPES: Record<string, { inputs: { slug: string; qty: number }[]; output: number; business: string }> = {
  flour:    { inputs: [{ slug: 'wheat', qty: 3 }], output: 2, business: 'mill' },
  lumber:   { inputs: [{ slug: 'wood', qty: 2 }], output: 2, business: 'lumber_mill' },
  bricks:   { inputs: [{ slug: 'stone', qty: 2 }, { slug: 'clay', qty: 1 }], output: 3, business: 'kiln' },
  iron_ingots: { inputs: [{ slug: 'iron_ore', qty: 2 }], output: 1, business: 'smithy' },
  copper_ingots: { inputs: [{ slug: 'copper_ore', qty: 2 }], output: 1, business: 'smithy' },
  fabric:   { inputs: [{ slug: 'cotton', qty: 3 }], output: 2, business: 'textile_shop' },
  glass:    { inputs: [{ slug: 'sand', qty: 3 }], output: 1, business: 'glassworks' },
  leather:  { inputs: [{ slug: 'herbs', qty: 1 }, { slug: 'clay', qty: 1 }], output: 1, business: 'tannery' },
  herbs_dried: { inputs: [{ slug: 'herbs', qty: 3 }], output: 2, business: 'apothecary' },
  rope:     { inputs: [{ slug: 'cotton', qty: 2 }], output: 2, business: 'textile_shop' },
  bread:    { inputs: [{ slug: 'flour', qty: 2 }, { slug: 'berries', qty: 1 }], output: 3, business: 'bakery' },
  furniture: { inputs: [{ slug: 'lumber', qty: 3 }, { slug: 'rope', qty: 1 }], output: 1, business: 'workshop' },
  tools:    { inputs: [{ slug: 'iron_ingots', qty: 2 }, { slug: 'lumber', qty: 1 }], output: 2, business: 'smithy' },
  clothing: { inputs: [{ slug: 'fabric', qty: 3 }, { slug: 'rope', qty: 1 }], output: 2, business: 'textile_shop' },
  pottery:  { inputs: [{ slug: 'clay', qty: 2 }, { slug: 'herbs_dried', qty: 1 }], output: 2, business: 'kiln' },
  medicine: { inputs: [{ slug: 'herbs_dried', qty: 2 }, { slug: 'glass', qty: 1 }], output: 1, business: 'apothecary' },
  jewelry:  { inputs: [{ slug: 'copper_ingots', qty: 1 }, { slug: 'iron_ingots', qty: 1 }, { slug: 'glass', qty: 1 }], output: 1, business: 'jeweler' },
  weapons:  { inputs: [{ slug: 'iron_ingots', qty: 3 }, { slug: 'lumber', qty: 2 }], output: 1, business: 'smithy' },
  beer:     { inputs: [{ slug: 'wheat', qty: 3 }, { slug: 'herbs', qty: 1 }], output: 2, business: 'brewery' },
};

export default function Goods() {
  const { data, isLoading, error } = useGoods();

  if (isLoading) return <Loading text="Loading goods catalog" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const goods = data!.goods;

  const tiers = [
    { tier: 1, label: 'Raw Resources', desc: 'Gathered from the environment' },
    { tier: 2, label: 'Intermediate Goods', desc: 'Processed from raw resources' },
    { tier: 3, label: 'Finished Products', desc: 'High-value consumer goods' },
  ];

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Goods Catalog"
        subtitle="All tradeable goods, recipes, and supply chains"
      />

      {/* ── Supply chain overview ── */}
      <Section title="Supply Chain">
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 32, padding: '16px 0' }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', fontWeight: 500 }}>Tier 1</div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>Raw</div>
              <div style={{ fontSize: 'var(--text-2xl)', marginTop: 4 }}>
                {goods.filter(g => g.tier === 1).length}
              </div>
            </div>
            <span style={{ fontSize: '1.5rem', color: 'var(--text-muted)' }}>→</span>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 'var(--text-sm)', color: 'var(--cyan)', fontWeight: 500 }}>Tier 2</div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>Intermediate</div>
              <div style={{ fontSize: 'var(--text-2xl)', marginTop: 4, color: 'var(--cyan)' }}>
                {goods.filter(g => g.tier === 2).length}
              </div>
            </div>
            <span style={{ fontSize: '1.5rem', color: 'var(--text-muted)' }}>→</span>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 'var(--text-sm)', color: 'var(--amber)', fontWeight: 500 }}>Tier 3</div>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>Finished</div>
              <div style={{ fontSize: 'var(--text-2xl)', marginTop: 4, color: 'var(--amber)' }}>
                {goods.filter(g => g.tier === 3).length}
              </div>
            </div>
          </div>
        </Card>
      </Section>

      {/* ── Goods by tier ── */}
      {tiers.map(({ tier, label, desc }) => {
        const tierGoods = goods.filter(g => g.tier === tier);
        return (
          <Section key={tier} title={`Tier ${tier} — ${label}`}>
            <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 12, marginTop: -8 }}>{desc}</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 280px), 1fr))', gap: 12 }}>
              {tierGoods.map(g => (
                <GoodCard key={g.slug} good={g} />
              ))}
            </div>
          </Section>
        );
      })}
    </div>
  );
}

function GoodCard({ good: g }: { good: Good }) {
  const recipe = RECIPES[g.slug];
  const marketPrice = g.last_trade_price ?? g.best_sell_price ?? g.best_storefront_price;

  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div>
          <Link to={`/market/${g.slug}`} style={{ fontSize: 'var(--text-base)', fontWeight: 600, color: tierColor(g.tier) }}>
            {g.name}
          </Link>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>{g.slug}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          {marketPrice != null ? (
            <div style={{ fontSize: 'var(--text-lg)', color: 'var(--accent)', fontWeight: 600 }}>{fmt(marketPrice)}</div>
          ) : (
            <div style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)' }}>No trades</div>
          )}
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>base: {fmt(g.base_value)}</div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        {g.is_gatherable && <Badge color="var(--accent)">gatherable</Badge>}
        <Badge color="var(--text-muted)">{g.storage_per_unit}u storage</Badge>
      </div>

      {/* Recipe */}
      {recipe && (
        <div style={{
          padding: '8px 10px',
          background: 'var(--bg-elevated)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 'var(--text-xs)',
        }}>
          <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>
            Recipe ({slugToName(recipe.business)}):
          </div>
          <div style={{ color: 'var(--text-secondary)' }}>
            {recipe.inputs.map((inp, i) => (
              <span key={i}>
                {i > 0 && ' + '}
                <Link to={`/market/${inp.slug}`} style={{ color: 'var(--cyan)' }}>
                  {inp.qty}× {slugToName(inp.slug)}
                </Link>
              </span>
            ))}
            <span style={{ color: 'var(--text-muted)' }}> → </span>
            <span style={{ color: 'var(--accent)', fontWeight: 500 }}>{recipe.output}× {g.name}</span>
          </div>
        </div>
      )}
    </Card>
  );
}
