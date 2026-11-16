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
| Balance adjustments (reconcile transactions) | balance set, difference booked | adjust to a balance on a date (cash, bank, credit card, investment cash; forex per currency), delete | end-of-day dating against a same-day expense, a second adjustment after later activity, delete restores the balance, forex position created and priced by MoneyWiz |
| Categories | yes | create, rename, re-parent, reorder, merge, delete | yes, including deleting one still in use (MoneyWiz unlinks its transactions) |
| Payees, tags | yes | create, rename, merge, delete | yes |
| Currencies | yes | default currency, manual rates, display list | manual rate set/clear; net worth and conversions computed by MoneyWiz |
| Investments | holdings, shares, price, cost basis | buy, sell, edit trade, delete trade, create/delete holding, manual price, share count, cost basis | buy → second buy → edit → sell → delete, with shares, cost basis and cash checked at each step |
| Loans | accounts, payment plan, full installment schedule | — | reads |
| Budgets | definition, limit, spent, remaining, monitored accounts/categories | — | reads |
| Scheduled transactions | definition, next due date, repeat rule, app-computed upcoming dates, completion | expense, income, transfer: create, update, delete, skip, execute (with an amount override) | monthly bill with category, tags and payee: edit → execute → skip → execute a different amount → new rule; weekly series of 3 with weekend handling to completion; last-weekday-of-month rule; transfer with both legs; one-off |

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
* **Splitting one transaction across accounts**:
  `createSplitedWithdrawTransactionForAccounts:…`.
* **Attachments**: counts are reported; adding or reading receipt files is not.
* **Saved reports, info cards, file import.**
* Archiving is checked by hand, not by the live suite: MoneyWiz moves an
  archived account to "no group" and renumbers the user's real ungrouped
  accounts, which the suite must not touch.
* Scheduled transactions: loan payment schedules are read-only (MoneyWiz
  derives them from the loan's payment plan). Fees and categories on
  scheduled transfers, foreign-currency schedules and cross-currency
  scheduled transfers are not offered. MoneyWiz has no business method to
  pause a schedule. Its execute method would record a completed series'
  last occurrence again, so the server refuses to execute or skip a schedule
  MoneyWiz reports as completed.
* Balance adjustments are not edited in place (MoneyWiz's editor deletes and
  recreates them too); `update_transaction` says so. Budget-transfer
  transactions are read-only. Investment buys and sells are edited with
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
  refused if they remain, for example while an editor is open. Deletions
  merged in from an iCloud import (already stored, but held unsaved by the
  main context, together with the related objects' relationships they
  shortened) do not count; this was verified with a sandbox probe that deletes
  through a second context and merges the change the way imports do.
* After a forex balance adjustment creates a currency position, MoneyWiz
  prices it a moment later without saving. The server saves that change, and
  only when nothing else is pending.
* MoneyWiz answers each new transfer with a "transfer succeeded" dialog from
  `-[iphoneMoneyWizViewController onTransactionCreated:]`, which runs inside the
  create call and does nothing else. The server skips that observer for its own
  transfer calls only. If a MoneyWiz update renames it, the dialog comes back
  (and blocks quitting the app until dismissed); writes are unaffected.
* Several MCP clients may be attached at once (say hermes and Claude). The
  dialog suppression patches nothing between calls; 0.1.1–0.1.2 kept a code
  patch in place, and two clients unloading in the wrong order crashed
  MoneyWiz.
* Saved locally is not synced; MoneyWiz uploads to iCloud on its own schedule.
* Several calls are not atomic: each business call saves independently.
* Only build 466's `MoneyWizManager` signatures are recorded. On another build,
  any call whose signature changed fails with an explicit error; reads and
  unchanged methods keep working.

## Silence

On build 466 every write tool was run under a tracer hooked to alert
presentation (`presentViewController:`, `MWAlertViewHelper`), `MBProgressHUD`,
user notifications, sounds, window ordering and activation, and URL opening.
The only reactions were the "transfer succeeded" alert, which the server skips
for its own transfers, and the "all payments are completed: delete or keep this
scheduled transaction?" alert that
`-[iphoneMoneyWizViewController onScheduledWaitToExecuteCountChanged:]`
schedules when a one-off has run or a series has recorded its last occurrence,
which the server skips for its own scheduled-transaction calls. MoneyWiz also
updates its Dock badge (`-[iosMoneyWizAppDelegate updateAppBadgeNumber]`, the
count of due bills) after scheduled-transaction changes, as it does for edits
in the app. `set_default_currency` was not exercised, to leave the ledger's
currency alone. `adjust_balance` was checked by its live test and a look at
what MoneyWiz presents afterwards, not under the full tracer (running that
tracer next to other attached clients is what crashed the app once). With
`MONEYWIZ_LAUNCH=1` the app is started hidden (`open -g -j`).
