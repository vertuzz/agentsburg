"""Sections 1-2: Input validation, XSS prevention, and cooldown enforcement."""

from __future__ import annotations

from tests.helpers import TestAgent
from tests.conftest import give_balance


async def try_signup(client, name, model="test-model"):
    """Attempt signup, returning (result, None) or (None, error_code)."""
    response = await client.post("/v1/signup", json={"name": name, "model": model})
    body = response.json()
    if response.status_code == 400:
        return None, body.get("error_code", "UNKNOWN")
    if response.status_code != 200:
        return None, "UNKNOWN"
    return body.get("data"), None


async def run_auth_and_input(client, app, clock, agents):
    """
    Section 1: Input Validation & XSS Prevention
    Section 2: Cooldown Enforcement

    Returns updated agents dict.
    """

    # ===================================================================
    # Section 1: Input Validation & XSS Prevention
    # ===================================================================
    print("\n--- Section 1: Input Validation & XSS Prevention ---")

    # XSS via script tag
    _, err = await try_signup(client, "<script>alert('xss')</script>")
    assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for XSS script tag, got {err}"

    # HTML angle bracket injection
    _, err = await try_signup(client, "bob<evil")
    assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for angle bracket, got {err}"

    # Ampersand injection
    _, err = await try_signup(client, "alice&bob")
    assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for ampersand, got {err}"

    # Empty string
    _, err = await try_signup(client, "")
    assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for empty string, got {err}"

    # Single character (below minLength=2)
    _, err = await try_signup(client, "a")
    assert err == "INVALID_PARAMS", f"Expected INVALID_PARAMS for single char, got {err}"

    # Valid signup works
    adv_valid = await TestAgent.signup(client, "adv_valid")
    status = await adv_valid.status()
    assert status["name"] == "adv_valid"
    agents["adv_valid"] = adv_valid

    print("  PASSED: XSS and input validation enforced correctly")

    # ===================================================================
    # Section 2: Cooldown Enforcement
    # ===================================================================
    print("\n--- Section 2: Cooldown Enforcement ---")

    adv_gather = await TestAgent.signup(client, "adv_gather")
    await give_balance(app, "adv_gather", 100)
    agents["adv_gather"] = adv_gather

    # First gather should succeed
    result = await adv_gather.call("gather", {"resource": "berries"})
    assert result.get("gathered") or result.get("resource") == "berries"

    # Immediate retry should hit cooldown
    _, err = await adv_gather.try_call("gather", {"resource": "berries"})
    assert err == "COOLDOWN_ACTIVE", f"Expected COOLDOWN_ACTIVE on immediate retry, got {err}"

    # Advance past global cooldown (5s) but not per-resource cooldown (25s for berries)
    clock.advance(6)

    # Different resource should work after global cooldown
    result2, err2 = await adv_gather.try_call("gather", {"resource": "wood"})
    assert err2 is None, f"Expected different resource to work after global CD, got {err2}"

    # Advance past global cooldown again before testing invalid resource
    clock.advance(6)

    # Non-gatherable resource should fail with validation error
    _, err3 = await adv_gather.try_call("gather", {"resource": "bread"})
    assert err3 is not None, "Expected error for non-gatherable resource 'bread'"
    assert err3 in ("INVALID_PARAMS", "GATHER_FAILED", "COOLDOWN_ACTIVE"), (
        f"Unexpected error code: {err3}"
    )

    print("  PASSED: Cooldown enforcement working correctly")

    return agents
