"""
Banking service — re-exports from domain-specific sub-modules.

Split into:
  deposits.py  — deposit, withdraw, view_balance, process_deposit_interest
  loans.py     — take_loan, process_loan_payments, default_agent_loans,
                 close_bank_account_for_bankruptcy
  credit.py    — calculate_credit
  _helpers.py  — shared internal helpers
"""

from backend.banking.credit import calculate_credit  # noqa: F401
from backend.banking.deposits import (  # noqa: F401
    deposit,
    process_deposit_interest,
    view_balance,
    withdraw,
)
from backend.banking.loans import (  # noqa: F401
    close_bank_account_for_bankruptcy,
    default_agent_loans,
    process_loan_payments,
    take_loan,
)
