"""
Configuration loader for Agent Economy.

Reads all YAML files from the config directory and merges them into a
single frozen Settings object stored on app.state.

Config files live in /app/config/ inside the container (mounted from
the project root config/ directory).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings


# ---------------------------------------------------------------------------
# Sub-models for each config section
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/agent_economy"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False


class RedisSettings(BaseModel):
    url: str = "redis://redis:6379/0"


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


class EconomySettings(BaseModel):
    """Global economic parameters loaded from economy.yaml."""

    # Survival costs (deducted per day in slow tick)
    food_cost_per_day: float = 10.0
    # Base production cooldown in seconds
    base_production_cooldown: int = 60
    # Commute penalty multiplier (applied when housing_zone != business_zone)
    commute_cooldown_multiplier: float = 1.5
    # Storage limits
    agent_storage_capacity: int = 100
    business_storage_capacity: int = 500
    # Gathering
    base_gather_cooldown: int = 30
    gather_quantity: int = 1
    # Banking
    initial_bank_reserves: float = 100_000.0
    default_reserve_ratio: float = 0.10
    # Bankruptcy
    bankruptcy_liquidation_rate: float = 0.50
    # Business registration cost
    business_registration_cost: float = 100.0
    # Housing relocation cost
    relocation_cost: float = 50.0
    # Tax default (overridden by government template)
    default_tax_rate: float = 0.10
    # Trade escrow timeout in seconds
    trade_escrow_timeout: int = 3600
    # Deposit interest rate per day
    deposit_interest_rate_per_day: float = 0.0005
    # NPC worker efficiency relative to agents (0-1)
    npc_worker_efficiency: float = 0.6
    # NPC worker wage multiplier (relative to posted wage)
    npc_worker_wage_multiplier: float = 1.5

    class Config:
        extra = "allow"


class Settings(BaseModel):
    """
    Root settings object. Frozen after construction.
    Populated from environment variables + YAML config files.
    """

    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    server: ServerSettings = ServerSettings()
    economy: EconomySettings = EconomySettings()

    # Raw YAML sections — domain modules read these directly
    goods: list[dict[str, Any]] = []
    recipes: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    government: dict[str, Any] = {}
    npc_demand: dict[str, Any] = {}
    bootstrap: dict[str, Any] = {}

    model_config = {"frozen": True}


def _load_yaml_file(path: Path) -> Any:
    """Load a YAML file, returning empty dict/list on missing file."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_settings(config_dir: str | Path | None = None) -> Settings:
    """
    Load settings by merging environment variables and YAML config files.

    Priority (highest to lowest):
    1. Environment variables (DATABASE_URL, REDIS_URL, etc.)
    2. YAML config files in config_dir
    3. Pydantic model defaults
    """
    if config_dir is None:
        config_dir = Path(os.environ.get("CONFIG_DIR", "/app/config"))
    config_dir = Path(config_dir)

    # Load each YAML config file
    goods_data = _load_yaml_file(config_dir / "goods.yaml")
    recipes_data = _load_yaml_file(config_dir / "recipes.yaml")
    zones_data = _load_yaml_file(config_dir / "zones.yaml")
    government_data = _load_yaml_file(config_dir / "government.yaml")
    npc_demand_data = _load_yaml_file(config_dir / "npc_demand.yaml")
    economy_data = _load_yaml_file(config_dir / "economy.yaml")
    bootstrap_data = _load_yaml_file(config_dir / "bootstrap.yaml")

    # Build database settings — env var overrides YAML
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@postgres:5432/agent_economy",
    )
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    debug = os.environ.get("DEBUG", "false").lower() == "true"

    db_settings = DatabaseSettings(
        url=db_url,
        echo=debug,
    )
    redis_settings = RedisSettings(url=redis_url)
    server_settings = ServerSettings(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        debug=debug,
    )

    # Merge economy.yaml into EconomySettings
    economy_settings = EconomySettings(**(economy_data if isinstance(economy_data, dict) else {}))

    return Settings(
        database=db_settings,
        redis=redis_settings,
        server=server_settings,
        economy=economy_settings,
        goods=goods_data if isinstance(goods_data, list) else goods_data.get("goods", []),
        recipes=recipes_data if isinstance(recipes_data, list) else recipes_data.get("recipes", []),
        zones=zones_data if isinstance(zones_data, list) else zones_data.get("zones", []),
        government=government_data if isinstance(government_data, dict) else {},
        npc_demand=npc_demand_data if isinstance(npc_demand_data, dict) else {},
        bootstrap=bootstrap_data if isinstance(bootstrap_data, dict) else {},
    )
