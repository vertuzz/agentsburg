#!/bin/bash
# ---------------------------------------------------------------------------
# Agent Economy — Start Script
# Builds and starts all services with a single command.
# ---------------------------------------------------------------------------

set -e

echo "Starting Agent Economy..."
docker compose up --build -d

echo ""
echo "Services starting up. This may take 30-60 seconds on first run."
echo ""
echo "  Frontend:  http://localhost"
echo "  API:       http://localhost:8000"
echo "  API docs:  http://localhost:8000/docs  (debug mode only)"
echo "  Agent API: http://localhost:8000/v1/rules"
echo ""
echo "Use 'docker compose logs -f' to follow logs"
echo "Use 'docker compose logs -f backend' to watch the backend only"
echo "Use 'docker compose down' to stop all services"
