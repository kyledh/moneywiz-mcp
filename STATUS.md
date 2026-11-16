# Status

Verified against MoneyWiz 2026.44.4 (build 466). "Verified" means performed on
real objects through the tools, read back from the app, and reverted, with
account, transaction, category, payee and tag counts returning to their
starting values (`tests/test_live.py`).

## Covered

| Area | Read | Write | Verified live |
| --- | --- | --- | --- |
| Accounts | yes | create, update, archive/unarchive, move, delete | cash, checking, savings, credit card, investment, forex |
| Account groups | yes | create, update (incl. membership), delete, reorder | yes |
| Transactions | yes | expense, income, transfer, refund: create, update, delete | single and split categories, foreign currency, fees, cheque numbers, tags, both transfer legs, cross-currency transfers, refund links |
| Transaction state | yes | cleared/pending, reconciled, void | yes |
| Categories | yes | create, rename, re-parent, reorder, merge, delete | yes, including the in-use delete guard |
| Payees, tags | yes | create, rename, merge, delete | yes |
| Currencies | yes | default currency, manual rates, display list | manual rate set/clear; net worth and conversions computed by MoneyWiz |
| Investments | holdings, shares, price, cost basis | buy, sell, edit trade, delete trade, create/delete holding, manual price, share count, cost basis | buy → second buy → edit → sell → delete, with shares, cost basis and cash checked at each step |
| Loans | accounts, payment plan, full installment schedule | — | reads |
| Budgets | definition, limit, spent, remaining, monitored accounts/categories | — | reads |
| Scheduled transactions | definition, repeat period, app-computed upcoming dates | — | reads |

## Not covered yet

* **Share exchanges** (swapping one holding for another).
  `exchangeInvestmentsShares:…` creates nothing without a `preparedValuation`
  object from `investmentExchangeValuationForAccount:…`, and that call returns
  nil for manually priced holdings. Passing such an object between calls would
  also need the agent to keep a reference to a non-managed object across calls.
* **Budget writes**: `createBudgetForUser:…`, `createRepeatableBudgetForUser:…`,
  `updateBudget:…`, `updateRepeatableBudget:…`, `deleteBudget:`, budget groups.
* **Loan creation and payment-plan edits**: `createLoanAccountForUser:…` takes 28
  arguments including a generated payment plan.
* **Scheduled transaction writes**: create, update, skip, execute now.
* **Splitting one transaction across accounts**:
  `createSplitedWithdrawTransactionForAccounts:…`.
* **Reconcile transactions**: `createReconcileTransactionForAccount:…`.
* **Attachments**: counts are reported; adding or reading receipt files is not.
* **Saved reports, info cards, file import.**
* Archiving is checked by hand, not by the live suite: MoneyWiz moves an
  archived account to "no group" and renumbers the user's real ungrouped
  accounts, which the suite must not touch.
* Reconcile and budget-transfer transactions are read-only; `update_transaction`
  refuses them with a clear message. Investment buys and sells are edited with
  `update_trade`.

Out of scope: online banking, tax reports, the built-in AI features, Shortcuts,
app preferences, passcode/security and subscriptions. Ledger lifecycle and
iCloud sync stay with MoneyWiz.

## Known constraints

* MoneyWiz must be running with its ledger open. The server attaches to the
  live process and reconnects if the app restarts.
* Attaching needs the one-time permission setup in the README.
* A write waits up to 5 seconds for MoneyWiz's own unsaved changes to clear
  (it briefly holds a temporary report after every data change) and is
  refused if they remain, for example while an editor is open.
* Saved locally is not synced; MoneyWiz uploads to iCloud on its own schedule.
* Several calls are not atomic: each business call saves independently.
* Only build 466's `MoneyWizManager` signatures are recorded. On another build,
  any call whose signature changed fails with an explicit error; reads and
  unchanged methods keep working.
