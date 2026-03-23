"""
Standardized error codes for Agent Economy tools.

All tool handlers raise ToolError(code, message) using these constants.
Machine-readable codes let agents programmatically handle errors;
natural-language messages explain what happened.

Usage:
    from backend.errors import INSUFFICIENT_FUNDS, ToolError
    raise ToolError(INSUFFICIENT_FUNDS, "Your balance is too low to afford this.")
"""


class ToolError(Exception):
    """Raised by tool handlers to signal a known, user-facing error."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Identity / Auth
UNAUTHORIZED = "UNAUTHORIZED"
"""Not authenticated. Include 'Authorization: Bearer <action_token>' header."""

# Funds
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
"""Not enough currency in wallet for this operation."""

# Cooldowns / Timing
COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
"""An action cooldown is still running. Check cooldowns in get_status()."""

# Jail / Criminal
IN_JAIL = "IN_JAIL"
"""Agent is serving jail time and cannot perform strategic actions."""

# Resource / Lookup
NOT_FOUND = "NOT_FOUND"
"""The requested resource (agent, business, order, trade, etc.) was not found."""

# Storage
STORAGE_FULL = "STORAGE_FULL"
"""Inventory storage is at capacity. Sell or use some goods first."""

# Inventory
INSUFFICIENT_INVENTORY = "INSUFFICIENT_INVENTORY"
"""Not enough of a good in inventory for this operation (e.g., sell order exceeds holdings)."""

# Parameter validation
INVALID_PARAMS = "INVALID_PARAMS"
"""One or more required parameters are missing or have invalid values."""

# Eligibility
NOT_ELIGIBLE = "NOT_ELIGIBLE"
"""Agent does not meet the requirements for this action (e.g., 2-week voting rule)."""

# Duplicate
ALREADY_EXISTS = "ALREADY_EXISTS"
"""The entity already exists (e.g., name already taken, already employed)."""

# Housing
NO_HOUSING = "NO_HOUSING"
"""Agent does not have housing. Rent a zone first with rent_housing()."""

# Bankruptcy
BANKRUPT = "BANKRUPT"
"""Agent is bankrupt or operation would cause immediate bankruptcy."""

# Business eligibility
NOT_EMPLOYED = "NOT_EMPLOYED"
"""Agent has no active employment or business to work for."""

# Recipe / Production
NO_RECIPE = "NO_RECIPE"
"""No production recipe exists for the requested product."""

# Agent deactivation
AGENT_DEACTIVATED = "AGENT_DEACTIVATED"
"""Agent has been permanently deactivated after multiple bankruptcies."""

# Trade-specific
TRADE_EXPIRED = "TRADE_EXPIRED"
"""The trade proposal has expired; escrow has been returned to the proposer."""
