"""Shared helpers for economy simulation phases."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.helpers import TestAgent


def print_phase(num: int, title: str) -> None:
    print(f"\n\n{'#' * 70}")
    print(f"# PHASE {num}: {title}")
    print(f"{'#' * 70}")


def print_section(title: str) -> None:
    print(f"\n--- {title} ---")


def print_agent_summary(agents: dict[str, TestAgent], statuses: dict[str, dict]) -> None:
    """Print a summary table of all agents."""
    print(f"\n{'=' * 90}")
    print(f"{'Agent':25s} {'Balance':>10s} {'Housing':>12s} {'Bankrupt':>9s} {'Violations':>11s} {'Inv':>5s}")
    print(f"{'-' * 90}")
    for name, status in statuses.items():
        housing = status.get("housing", {})
        zone = housing.get("zone_slug", "homeless") if not housing.get("homeless") else "homeless"
        inv_count = sum(i["quantity"] for i in status.get("inventory", []))
        print(
            f"  {name:23s} {status['balance']:10.2f} {zone:>12s} "
            f"{status['bankruptcy_count']:>9d} {status.get('violation_count', 0):>11d} {inv_count:>5d}"
        )
    print(f"{'=' * 90}")


# Shared constants
AGENT_NAMES = [
    "eco_gatherer1",  # 0: gathers raw resources
    "eco_gatherer2",  # 1: gathers raw resources
    "eco_miller",  # 2: owns a mill
    "eco_baker",  # 3: owns a bakery
    "eco_lumberjack",  # 4: owns a lumber mill
    "eco_worker1",  # 5: employed worker
    "eco_worker2",  # 6: employed worker
    "eco_trader",  # 7: marketplace trader
    "eco_banker",  # 8: banking focus
    "eco_politician",  # 9: government focus
    "eco_criminal",  # 10: will evade taxes / go to jail
    "eco_homeless",  # 11: stays homeless, idle -- will go bankrupt
]
