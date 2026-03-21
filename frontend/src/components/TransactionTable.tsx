import type { Transaction, Pagination } from "../types";

interface TransactionTableProps {
  transactions: Transaction[];
  pagination: Pagination;
  onPageChange: (page: number) => void;
  loading?: boolean;
}

const TX_TYPE_LABELS: Record<string, string> = {
  wage: "Wage",
  rent: "Rent",
  food: "Food",
  tax: "Tax",
  fine: "Fine",
  trade: "Direct Trade",
  marketplace: "Marketplace",
  storefront: "Storefront Sale",
  loan_payment: "Loan Payment",
  deposit_interest: "Deposit Interest",
  loan_disbursement: "Loan",
  deposit: "Deposit",
  withdrawal: "Withdrawal",
  gathering: "Gathering",
  business_reg: "Business Reg",
  bankruptcy_liquidation: "Bankruptcy",
};

const TX_TYPE_BADGE: Record<string, string> = {
  wage: "badge-green",
  deposit_interest: "badge-green",
  loan_disbursement: "badge-blue",
  marketplace: "badge-blue",
  storefront: "badge-blue",
  trade: "badge-purple",
  gathering: "badge-cyan",
  rent: "badge-yellow",
  food: "badge-yellow",
  tax: "badge-red",
  fine: "badge-red",
  loan_payment: "badge-yellow",
  deposit: "badge-gray",
  withdrawal: "badge-gray",
  business_reg: "badge-purple",
  bankruptcy_liquidation: "badge-red",
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function TransactionTable({
  transactions,
  pagination,
  onPageChange,
  loading,
}: TransactionTableProps) {
  if (loading) {
    return <div className="loading-box">Loading transactions…</div>;
  }

  if (transactions.length === 0) {
    return <div className="empty-box">No transactions yet</div>;
  }

  return (
    <div>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th className="text-right">Amount</th>
              <th>Direction</th>
              <th>Details</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((tx) => (
              <tr key={tx.id}>
                <td>
                  <span
                    className={`badge ${TX_TYPE_BADGE[tx.type] ?? "badge-gray"}`}
                  >
                    {TX_TYPE_LABELS[tx.type] ?? tx.type}
                  </span>
                </td>
                <td className="text-right font-mono">
                  <span
                    className={
                      tx.direction === "in" ? "value-positive" : "value-negative"
                    }
                  >
                    {tx.direction === "in" ? "+" : "-"}$
                    {tx.amount.toFixed(2)}
                  </span>
                </td>
                <td>
                  <span
                    className={`badge ${tx.direction === "in" ? "badge-green" : "badge-red"}`}
                  >
                    {tx.direction === "in" ? "↓ in" : "↑ out"}
                  </span>
                </td>
                <td style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                  {tx.metadata?.good_slug
                    ? String(tx.metadata.good_slug)
                    : null}
                </td>
                <td style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
                  {formatDate(tx.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pagination.total_pages > 1 && (
        <div className="pagination">
          <button
            className="page-btn"
            disabled={pagination.page <= 1}
            onClick={() => onPageChange(pagination.page - 1)}
          >
            ← Prev
          </button>
          <span style={{ color: "var(--text-muted)", fontSize: "0.875rem" }}>
            Page {pagination.page} of {pagination.total_pages}
          </span>
          <button
            className="page-btn"
            disabled={pagination.page >= pagination.total_pages}
            onClick={() => onPageChange(pagination.page + 1)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
