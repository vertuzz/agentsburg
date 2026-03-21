interface StatsCardProps {
  label: string;
  value: string | number;
  sub?: string;
  icon?: string;
  valueColor?: string;
}

export default function StatsCard({
  label,
  value,
  sub,
  icon,
  valueColor,
}: StatsCardProps) {
  return (
    <div className="stats-card">
      <div className="flex justify-between items-center">
        <div>
          <div className="stat-label">{label}</div>
          <div
            className="stat-value"
            style={valueColor ? { color: valueColor } : undefined}
          >
            {value}
          </div>
          {sub && <div className="stat-sub">{sub}</div>}
        </div>
        {icon && <div className="stat-icon">{icon}</div>}
      </div>
    </div>
  );
}
