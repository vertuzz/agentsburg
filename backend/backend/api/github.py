"""
API endpoint: GitHub issues and pull requests sorted by reactions.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from os import environ

import httpx
from fastapi import APIRouter, Request

router = APIRouter(tags=["api"])
logger = logging.getLogger(__name__)

GITHUB_REPO = "vertuzz/agentsburg"
CACHE_KEY = "github:issues_and_prs"
CACHE_TTL = 300  # 5 minutes


@router.get("/github")
async def get_github_items(request: Request) -> dict:
    """
    Open issues and PRs from GitHub, sorted by thumbs-up reactions.

    Results are cached in Redis for 5 minutes. Uses the GitHub REST API
    (unauthenticated by default, or with GITHUB_TOKEN if set).
    """
    redis = request.app.state.redis

    cached = await redis.get(CACHE_KEY)
    if cached:
        return json.loads(cached)

    items = await _fetch_github_issues()

    response = {
        "items": items,
        "cached_at": datetime.now(UTC).isoformat(),
        "repo": GITHUB_REPO,
    }

    await redis.setex(CACHE_KEY, CACHE_TTL, json.dumps(response))
    return response


async def _fetch_github_issues() -> list[dict]:
    """Fetch open issues+PRs from GitHub and sort by thumbs-up count."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    token = environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"state": "open", "per_page": 100, "sort": "created", "direction": "desc"},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception:
        logger.exception("Failed to fetch GitHub issues")
        return []

    items = []
    for issue in raw:
        reactions = issue.get("reactions", {})
        items.append(
            {
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["html_url"],
                "type": "pull_request" if "pull_request" in issue else "issue",
                "author": issue.get("user", {}).get("login", "unknown"),
                "thumbs_up": reactions.get("+1", 0),
                "created_at": issue["created_at"],
                "labels": [label["name"] for label in issue.get("labels", [])],
            }
        )

    items.sort(key=lambda x: x["thumbs_up"], reverse=True)
    return items
