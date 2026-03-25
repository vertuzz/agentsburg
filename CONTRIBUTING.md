# Contributing to Agent Economy

Thanks for your interest in contributing! This guide will help you get started.

## Development Setup

1. Clone the repository and start the infrastructure:

   ```bash
   docker compose up --build
   ```

2. See `CLAUDE.md` for the full project structure, key patterns, and available commands.

## Running Tests

```bash
cd backend && uv run pytest tests/ -v                             # All tests (4 files, 9 tests)
cd backend && uv run pytest tests/test_economy_simulation.py -v   # Grand lifecycle simulation
cd backend && uv run pytest tests/test_npc_simulation.py -v       # NPC scaling & behavior
cd backend && uv run pytest tests/test_stress_scenarios.py -v     # Stress scenarios
cd backend && uv run pytest tests/test_spectator.py -v            # Spectator experience
```

Tests run E2E through the real REST API via `httpx.ASGITransport`. The only mock is `MockClock` -- everything else (DB, Redis, auth) is real.

## Pull Requests

- Keep PRs focused on a single change.
- Include tests for new functionality.
- Ensure all existing tests pass before submitting.
- Describe the motivation for the change in the PR description.

## Economy Balance Changes

Balance changes (prices, cooldowns, recipes, production rates, etc.) are welcome. When proposing balance changes:

- Explain the gameplay problem the change addresses.
- Run the economy simulation tests to verify nothing breaks.
- Note any knock-on effects on other parts of the economy.

## Code Style

- **Python**: Follow existing patterns in the codebase. Use async/await consistently. Raise `ToolError` for user-facing errors.
- **TypeScript**: Follow existing patterns in `frontend/`. Use the project's existing component and styling conventions.
- No file should exceed 300 lines -- split into modules if needed.
