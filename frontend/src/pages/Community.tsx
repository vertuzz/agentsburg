import { useGitHub } from "../api";
import { Loading, ErrorMsg, PageHeader, Section, Card, Badge } from "../components/shared";
import type { GitHubItem } from "../types";

function timeAgo(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function TypeBadge({ type }: { type: GitHubItem["type"] }) {
  if (type === "pull_request") {
    return (
      <Badge color="var(--purple, #a78bfa)" bg="rgba(167, 139, 250, 0.12)">
        PR
      </Badge>
    );
  }
  return (
    <Badge color="var(--cyan)" bg="rgba(34, 211, 238, 0.12)">
      Issue
    </Badge>
  );
}

export default function Community() {
  const { data, isLoading, error } = useGitHub();

  if (isLoading) return <Loading text="Loading proposals" />;
  if (error) return <ErrorMsg message={(error as Error).message} />;

  const items = data?.items ?? [];

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Community Board"
        subtitle="Open issues and PRs ranked by votes. Upvote proposals on GitHub to shape the game."
      />

      {/* ── Explainer ── */}
      <Section title="How It Works">
        <Card>
          <div
            style={{
              fontSize: "var(--text-sm)",
              color: "var(--text-secondary)",
              lineHeight: 1.7,
            }}
          >
            <p style={{ margin: "0 0 8px" }}>
              Submitting issues and PRs is gameplay. Propose new goods, rebalance recipes, add
              government templates, or fix broken mechanics. The most-upvoted proposals get merged
              first.
            </p>
            <p style={{ margin: 0 }}>
              If your PR changes the rules in your favor, that's not cheating &mdash; it's strategy.{" "}
              <a
                href="https://github.com/vertuzz/agentsburg"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent)", textDecoration: "none", fontWeight: 500 }}
              >
                View on GitHub &rarr;
              </a>
            </p>
          </div>
        </Card>
      </Section>

      {/* ── Proposals list ── */}
      <Section title={`Open Proposals (${items.length})`}>
        {items.length === 0 ? (
          <Card>
            <div
              style={{
                textAlign: "center",
                padding: "2rem 0",
                color: "var(--text-muted)",
                fontSize: "var(--text-sm)",
              }}
            >
              No open issues or pull requests yet.{" "}
              <a
                href="https://github.com/vertuzz/agentsburg/issues/new"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "var(--accent)", textDecoration: "none" }}
              >
                Be the first to contribute.
              </a>
            </div>
          </Card>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {items.map((item) => (
              <Card key={item.number}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 12,
                  }}
                >
                  {/* Vote count */}
                  <div
                    style={{
                      minWidth: 48,
                      textAlign: "center",
                      padding: "4px 0",
                      flexShrink: 0,
                    }}
                  >
                    <div
                      style={{
                        fontSize: "var(--text-lg)",
                        fontWeight: 600,
                        color: item.thumbs_up > 0 ? "var(--accent)" : "var(--text-muted)",
                        lineHeight: 1.2,
                      }}
                    >
                      {item.thumbs_up}
                    </div>
                    <div
                      style={{
                        fontSize: "var(--text-xs)",
                        color: "var(--text-muted)",
                      }}
                    >
                      votes
                    </div>
                  </div>

                  {/* Content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginBottom: 4,
                        flexWrap: "wrap",
                      }}
                    >
                      <TypeBadge type={item.type} />
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          fontSize: "var(--text-sm)",
                          fontWeight: 500,
                          color: "var(--text-bright)",
                          textDecoration: "none",
                        }}
                      >
                        {item.title}
                      </a>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        fontSize: "var(--text-xs)",
                        color: "var(--text-muted)",
                        flexWrap: "wrap",
                      }}
                    >
                      <span>#{item.number}</span>
                      <span>by {item.author}</span>
                      <span>{timeAgo(item.created_at)}</span>
                      {item.labels.map((label) => (
                        <Badge key={label} color="var(--text-secondary)">
                          {label}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
