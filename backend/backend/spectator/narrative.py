"""
Template-based narrative strings for spectator events.

Translates raw event data into human-readable text with category
and drama metadata. No LLM calls — pure string formatting.
"""

from __future__ import annotations


def _fmt_amount(amount: float | int) -> str:
    """Format currency amounts nicely."""
    if amount >= 1000:
        return f"{amount:,.0f}"
    return f"{amount:.2f}"


def narrate(event_type: str, detail: dict) -> dict:
    """
    Convert a raw event into narrative text.

    Returns {"text": "...", "category": "economy|crime|politics|market|business"}.
    """
    handler = _HANDLERS.get(event_type, _default_handler)
    return handler(detail)


# --- Event handlers ---


def _bankruptcy(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    count = d.get("bankruptcy_count", 1)
    deactivated = d.get("deactivated", False)
    if deactivated:
        text = f"{name} went bankrupt for the {_ordinal(count)} time and has been permanently deactivated"
    else:
        text = f"{name} went bankrupt (#{count}) — all assets liquidated"
    return {"text": text, "category": "economy"}


def _eviction(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    zone = d.get("zone_name", d.get("zone", "their zone"))
    text = f"{name} was evicted from {zone} — couldn't afford rent"
    return {"text": text, "category": "economy"}


def _audit_violation(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    fine = _fmt_amount(d.get("fine_amount", 0))
    jailed = d.get("jailed", False)
    jail_part = " and sent to jail" if jailed else ""
    text = f"{name} was audited and fined {fine} for unreported trade income{jail_part}"
    return {"text": text, "category": "crime"}


def _audit_clean(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    text = f"{name} was audited — books are clean"
    return {"text": text, "category": "crime"}


def _election(d: dict) -> dict:
    winner_name = d.get("winner_name", d.get("winner", "Unknown"))
    pct = d.get("vote_pct", "?")
    changed = d.get("changed", False)
    previous = d.get("previous_name", d.get("previous", ""))
    if changed:
        text = f"Election results: {winner_name} wins with {pct}% — replacing {previous}"
    else:
        text = f"Election results: {winner_name} re-elected with {pct}%"
    return {"text": text, "category": "politics"}


def _marketplace_fill(d: dict) -> dict:
    buyer = d.get("buyer_name", "A buyer")
    seller = d.get("seller_name", "a seller")
    qty = d.get("quantity", 0)
    good = d.get("good_slug", "goods")
    price = d.get("price", 0)
    total = _fmt_amount(price * qty)
    text = f"{buyer} bought {qty}x {good} from {seller} for {total}"
    return {"text": text, "category": "market"}


def _business_registered(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    biz_name = d.get("business_name", "a business")
    zone = d.get("zone_name", d.get("zone", ""))
    zone_part = f" in {zone}" if zone else ""
    text = f"{name} registered {biz_name}{zone_part}"
    return {"text": text, "category": "business"}


def _business_closed(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    biz_name = d.get("business_name", "a business")
    reason = d.get("reason", "")
    reason_part = f" ({reason})" if reason else ""
    text = f"{name} closed {biz_name}{reason_part}"
    return {"text": text, "category": "business"}


def _loan_disbursed(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    amount = _fmt_amount(d.get("amount", 0))
    rate = d.get("interest_rate", 0)
    text = f"{name} took a {amount} loan at {rate:.1%} interest"
    return {"text": text, "category": "economy"}


def _loan_default(d: dict) -> dict:
    name = d.get("agent_name", "An agent")
    amount = _fmt_amount(d.get("remaining_balance", 0))
    text = f"{name} defaulted on a loan with {amount} remaining"
    return {"text": text, "category": "economy"}


def _survival_costs(d: dict) -> dict:
    count = d.get("agents_charged", 0)
    total = _fmt_amount(d.get("total_deducted", 0))
    text = f"Survival costs: {count} agents charged {total} total"
    return {"text": text, "category": "economy"}


def _rent_summary(d: dict) -> dict:
    charged = d.get("agents_charged", 0)
    evicted = d.get("agents_evicted", 0)
    total = _fmt_amount(d.get("total_collected", 0))
    parts = [f"{charged} agents paid rent ({total} collected)"]
    if evicted > 0:
        parts.append(f"{evicted} evicted")
    text = "Rent: " + ", ".join(parts)
    return {"text": text, "category": "economy"}


def _tax_summary(d: dict) -> dict:
    total = _fmt_amount(d.get("total_collected", 0))
    rate = d.get("tax_rate", 0)
    text = f"Tax collection: {total} collected at {rate:.0%} rate"
    return {"text": text, "category": "politics"}


def _audit_summary(d: dict) -> dict:
    violations = d.get("violations_found", 0)
    jailed = d.get("jailed", 0)
    fines = _fmt_amount(d.get("total_fines", 0))
    if violations > 0:
        text = f"Audits: {violations} violation(s) found, {fines} in fines"
        if jailed > 0:
            text += f", {jailed} jailed"
    else:
        audited = d.get("agents_audited", 0)
        text = f"Audits: {audited} agents checked — all clean"
    return {"text": text, "category": "crime"}


def _bankruptcy_summary(d: dict) -> dict:
    names = d.get("bankrupted", [])
    count = d.get("count", len(names))
    if count == 0:
        text = "Bankruptcy check: no agents below threshold"
    elif count == 1:
        text = f"{names[0]} went bankrupt — all assets liquidated"
    else:
        text = f"{count} agents went bankrupt: {', '.join(names[:3])}"
        if count > 3:
            text += f" and {count - 3} more"
    return {"text": text, "category": "economy"}


def _default_handler(d: dict) -> dict:
    return {"text": str(d.get("message", "Something happened")), "category": "economy"}


# --- Helpers ---


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


_HANDLERS: dict[str, callable] = {
    "bankruptcy": _bankruptcy,
    "eviction": _eviction,
    "audit_violation": _audit_violation,
    "audit_clean": _audit_clean,
    "election": _election,
    "marketplace_fill": _marketplace_fill,
    "business_registered": _business_registered,
    "business_closed": _business_closed,
    "loan_disbursed": _loan_disbursed,
    "loan_default": _loan_default,
    "survival_costs": _survival_costs,
    "rent_summary": _rent_summary,
    "tax_summary": _tax_summary,
    "audit_summary": _audit_summary,
    "bankruptcy_summary": _bankruptcy_summary,
}
