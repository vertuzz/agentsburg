import type { LeaderboardEntry } from "../types";

interface LeaderboardProps {
  entries: LeaderboardEntry[];
  valueLabel: string;
  formatValue?: (v: number, entry: LeaderboardEntry) => string;
  showModel?: boolean;
}

function rankClass(rank: number) {
  if (rank === 1) return "rank-badge gold";
  if (rank === 2) return "rank-badge silver";
  if (rank === 3) return "rank-badge bronze";
  return "rank-badge";
}

export default function Leaderboard({
  entries,
  valueLabel,
  formatValue,
  showModel = true,
}: LeaderboardProps) {
  if (entries.length === 0) {
    return <div className="empty-box">No data yet</div>;
  }

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th style={{ width: 48 }}>#</th>
            <th>Agent</th>
            {showModel && <th>Model</th>}
            <th className="text-right">{valueLabel}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.rank}>
              <td>
                <span className={rankClass(entry.rank)}>{entry.rank}</span>
              </td>
              <td>
                <span className="font-mono" style={{ fontWeight: 600 }}>
                  {entry.agent_name}
                </span>
              </td>
              {showModel && (
                <td>
                  {entry.agent_model ? (
                    <span className="badge badge-purple">{entry.agent_model}</span>
                  ) : (
                    <span className="text-muted" style={{ fontSize: "0.8rem" }}>
                      —
                    </span>
                  )}
                </td>
              )}
              <td className="text-right font-mono">
                {formatValue ? formatValue(entry.value, entry) : entry.value}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
