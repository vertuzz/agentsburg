"""
Configuration loader for Agent Economy.

Reads all YAML files from the config directory and merges them into a
single frozen Settings object stored on app.state.

Config files live in /app/config/ inside the container (mounted from
the project root config/ directory).

Environment variables take priority over .env file values, which take
priority over YAML config values, which take priority over model defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Sub-models for each config section
# ---------------------------------------------------------------------------


class DatabaseSettings(BaseModel):
    # Dev-only default — production must set DATABASE_URL env var or .env file
    url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/agent_economy"
    pool_size: int = 20
    max_overflow: int = 40
    echo: bool = False


class RedisSettings(BaseModel):
    # Dev-only default — production must set REDIS_URL env var or .env file
    url: str = "redis://redis:6379/0"


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    base_url: str = "http://localhost:8000"


class EconomySettings(BaseModel):
    """Global economic parameters loaded from economy.yaml."""

    # Survival costs (deducted per hour in slow tick)
    survival_cost_per_hour: float = 5.0
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
    min_bank_reserves: float = 50_000.0
    default_reserve_ratio: float = 0.10
    # Bankruptcy
    bankruptcy_liquidation_rate: float = 0.50
    max_bankruptcies_before_deactivation: int = 2
    # Business registration cost
    business_registration_cost: float = 200.0
    # Housing relocation cost
    relocation_cost: float = 50.0
    # Tax default (overridden by government template)
    default_tax_rate: float = 0.10
    # Trade escrow timeout in seconds
    trade_escrow_timeout: int = 3600
    # NPC worker efficiency relative to agents (0-1)
    npc_worker_efficiency: float = 0.5
    # NPC worker wage multiplier (relative to posted wage)
    npc_worker_wage_multiplier: float = 2.0

    model_config = ConfigDict(extra="allow")


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


# ---------------------------------------------------------------------------
# Environment loader — reads .env file into os.environ before settings build
# ---------------------------------------------------------------------------


class _EnvLoader(BaseSettings):
    """
    Lightweight BaseSettings used only to load .env file values.

    Reads the env_file into resolved values. Actual model fields are
    defined here so pydantic-settings resolves them; we then use the
    raw env values to populate the full Settings object.

    Priority (highest → lowest):
    1. Real environment variables (already in os.environ)
    2. .env file (if present)
    3. Model defaults below
    """

    # Dev-only defaults — production deployments must set these via env vars or .env
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/agent_economy"
    redis_url: str = "redis://redis:6379/0"
    config_dir: str = "/app/config"
    debug: str = "false"
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def _load_yaml_file(path: Path) -> Any:
    """Load a YAML file, returning empty dict/list on missing file."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_settings(config_dir: str | Path | None = None, env_file: str | None = None) -> Settings:
    """
    Load settings by merging environment variables, .env file, and YAML config files.

    Priority (highest to lowest):
    1. Environment variables (DATABASE_URL, REDIS_URL, etc.)
    2. .env file (auto-detected or specified via env_file)
    3. YAML config files in config_dir
    4. Pydantic model defaults

    Args:
        config_dir: Directory to load YAML configs from.
                    Falls back to CONFIG_DIR env var, then /app/config.
        env_file:   Path to .env file to load. Defaults to ".env" in cwd.
                    Pass ".env.test" to load test settings.
    """
    # Build env loader with optional env_file override
    if env_file is not None:
        loader = _EnvLoader(_env_file=env_file)  # type: ignore[call-arg]
    else:
        loader = _EnvLoader()

    # Resolve config_dir (explicit arg > env var > loader default)
    if config_dir is None:
        config_dir = Path(loader.config_dir)
    config_dir = Path(config_dir)

    # Load each YAML config file
    goods_data = _load_yaml_file(config_dir / "goods.yaml")
    recipes_data = _load_yaml_file(config_dir / "recipes.yaml")
    zones_data = _load_yaml_file(config_dir / "zones.yaml")
    government_data = _load_yaml_file(config_dir / "government.yaml")
    npc_demand_data = _load_yaml_file(config_dir / "npc_demand.yaml")
    economy_data = _load_yaml_file(config_dir / "economy.yaml")
    bootstrap_data = _load_yaml_file(config_dir / "bootstrap.yaml")

    # Build sub-settings from resolved env values
    debug = loader.debug.lower() == "true"

    db_settings = DatabaseSettings(
        url=loader.database_url,
        echo=debug,
    )
    redis_settings = RedisSettings(url=loader.redis_url)
    server_settings = ServerSettings(
        host=loader.host,
        port=loader.port,
        debug=debug,
        base_url=loader.base_url,
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
