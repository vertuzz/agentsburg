import { Link } from "react-router-dom";
import { useGoods } from "../api";
import { Loading, ErrorMsg, PageHeader, Section, Badge, DataTable } from "../components/shared";
import { fmt, tierColor } from "../components/formatters";
import type { Column } from "../components/shared";
import type { Good } from "../types";

export default function Market() {
  const { data, isLoading, error } = useGoods();

  if (isLoading) return <Loading text="Scanning marketplace" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;
  const goods = data!.goods;

  // Group by tier
  const tiers = [1, 2, 3];

  const columns: Column<Good>[] = [
    {
      key: "name",
      header: "Good",
      render: (g) => (
        <Link to={`/market/${g.slug}`} style={{ color: tierColor(g.tier), fontWeight: 500 }}>
          {g.name}
        </Link>
      ),
    },
    {
      key: "base",
      header: "Base Value",
      align: "right",
      render: (g) => <span style={{ color: "var(--text-secondary)" }}>{fmt(g.base_value)}</span>,
    },
    {
      key: "market",
      header: "Market Price",
      align: "right",
      render: (g) => {
        const price = g.last_trade_price ?? g.best_sell_price ?? g.best_storefront_price;
        return price != null ? (
          <span style={{ color: "var(--accent)", fontWeight: 500 }}>{fmt(price)}</span>
        ) : (
          <span style={{ color: "var(--text-muted)" }}>—</span>
        );
      },
    },
    {
      key: "spread",
      header: "Best Bid / Ask",
      align: "right",
      render: (g) => (
        <span style={{ fontSize: "var(--text-xs)" }}>
          <span style={{ color: "var(--accent)" }}>
            {g.best_sell_price != null ? fmt(g.best_sell_price) : "—"}
          </span>
          <span style={{ color: "var(--text-muted)" }}> / </span>
          <span style={{ color: "var(--danger)" }}>
            {g.best_storefront_price != null ? fmt(g.best_storefront_price) : "—"}
          </span>
        </span>
      ),
    },
    {
      key: "gather",
      header: "Type",
      align: "center",
      render: (g) =>
        g.is_gatherable ? (
          <Badge color="var(--accent)">gatherable</Badge>
        ) : (
          <Badge color="var(--text-muted)">crafted</Badge>
        ),
    },
    {
      key: "storage",
      header: "Storage",
      align: "right",
      render: (g) => <span style={{ color: "var(--text-secondary)" }}>{g.storage_per_unit}u</span>,
    },
  ];

  return (
    <div className="animate-fade-in">
      <PageHeader title="Marketplace" subtitle="All tradeable goods with current market prices" />

      {tiers.map((tier) => {
        const tierGoods = goods.filter((g) => g.tier === tier);
        if (tierGoods.length === 0) return null;
        const tierLabels = ["", "Raw Resources", "Intermediate Goods", "Finished Products"];
        return (
          <Section key={tier} title={`Tier ${tier} — ${tierLabels[tier]}`}>
            <DataTable columns={columns} data={tierGoods} />
          </Section>
        );
      })}
    </div>
  );
}
