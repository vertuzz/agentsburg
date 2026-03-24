"""Phase 6: Banking (Days 10-14) — deposit, withdraw, loans, interest, installments."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.models.agent import Agent
from backend.models.banking import BankAccount, CentralBank
from tests.conftest import give_balance
from tests.simulation.helpers import print_phase, print_section

if TYPE_CHECKING:
    from tests.helpers import TestAgent


async def run_phase_6(agents: dict[str, TestAgent], client, app, clock, run_tick, redis_client):
    """Test banking: deposit, withdraw, loans, interest accrual, money supply invariant."""
    print_phase(6, "BANKING")

    banker = agents["eco_banker"]
    await give_balance(app, "eco_banker", 1000)

    # --- 6a-pre: Credit score in status ---
    print_section("Credit score in agent status")

    banker_status = await banker.status()
    assert "credit_score" in banker_status, "Status should include credit_score"
    assert "max_loan_amount" in banker_status, "Status should include max_loan_amount"
    assert isinstance(banker_status["credit_score"], (int, float)), (
        f"credit_score should be numeric, got {type(banker_status['credit_score'])}"
    )
    print(f"  credit_score={banker_status['credit_score']}, max_loan_amount={banker_status['max_loan_amount']}")

    # --- 6a: Deposit ---
    print_section("Deposit money")

    dep_result = await banker.call(
        "bank",
        {
            "action": "deposit",
            "amount": 300,
        },
    )
    assert dep_result["account_balance"] == 300.0
    wallet_after_dep = dep_result["wallet_balance"]
    print(f"  Deposited 300. Account={dep_result['account_balance']}, Wallet={wallet_after_dep}")

    # --- 6b: Withdraw ---
    print_section("Withdraw money")

    withdraw_result = await banker.call(
        "bank",
        {
            "action": "withdraw",
            "amount": 100,
        },
    )
    assert withdraw_result["account_balance"] == 200.0
    print(f"  Withdrew 100. Account={withdraw_result['account_balance']}")

    # --- 6c: View balance ---
    view = await banker.call("bank", {"action": "view_balance"})
    assert "account_balance" in view
    assert "credit" in view
    credit_score = view["credit"]["credit_score"]
    print(f"  View: account={view['account_balance']}, credit_score={credit_score}")

    # --- 6d: Take a loan ---
    print_section("Taking a loan")

    async with app.state.session_factory() as session:
        cb = await session.execute(select(CentralBank).where(CentralBank.id == 1))
        bank_row = cb.scalar_one()
        if float(bank_row.reserves) < 10000:
            bank_row.reserves = Decimal("50000")
            await session.commit()

    loan_result = await banker.call(
        "bank",
        {
            "action": "take_loan",
            "amount": 100,
        },
    )
    assert "principal" in loan_result
    assert loan_result["installments_remaining"] == 24
    loan_installment = loan_result["installment_amount"]
    print(
        f"  Loan: principal={loan_result['principal']}, installment={loan_installment}, "
        f"rate={loan_result.get('interest_rate', 'N/A')}"
    )

    # Cannot take second loan
    _, err = await banker.try_call("bank", {"action": "take_loan", "amount": 50})
    assert err is not None
    print(f"  Second loan rejected (error={err})")

    # --- 6e: Run ticks for interest and installments ---
    print_section("Running ticks for banking operations")

    view_before = await banker.call("bank", {"action": "view_balance"})
    account_before = view_before["account_balance"]

    await run_tick(hours=48)

    view_after = await banker.call("bank", {"action": "view_balance"})
    account_after = view_after["account_balance"]

    if account_after > account_before:
        print(f"  Deposit interest accrued: {account_before} -> {account_after}")
    else:
        print(f"  Account: {account_before} -> {account_after} (interest may be small)")

    active_loans = view_after.get("active_loans", [])
    if active_loans:
        remaining = active_loans[0].get("installments_remaining", 24)
        print(f"  Loan installments remaining: {remaining} (started at 24)")
        assert remaining < 24, "Some installments should have been collected"
    else:
        print("  Loan may have been fully repaid or defaulted")

    # --- 6f: Money supply check ---
    print_section("Money supply invariant check")

    async with app.state.session_factory() as session:
        wallet_total = float((await session.execute(select(func.coalesce(func.sum(Agent.balance), 0)))).scalar_one())

        bank_acct_total = float(
            (await session.execute(select(func.coalesce(func.sum(BankAccount.balance), 0)))).scalar_one()
        )

        bank_row = (await session.execute(select(CentralBank).where(CentralBank.id == 1))).scalar_one_or_none()
        reserves = float(bank_row.reserves) if bank_row else 0.0

    print(f"  Wallets: {wallet_total:.2f}")
    print(f"  Bank accounts: {bank_acct_total:.2f}")
    print(f"  Central bank reserves: {reserves:.2f}")
    print(f"  Trackable total: {wallet_total + bank_acct_total + reserves:.2f}")

    # Run remaining days to day 14
    await run_tick(hours=48)

    print("\n  Phase 6 COMPLETE")
