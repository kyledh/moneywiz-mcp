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
| Transactions | yes | expense, income, transfer, refund: create, update, delete; one purchase, income or refund across several accounts; budget transfers: update | single and split categories, foreign currency, fees, cheque numbers, tags, both transfer legs, cross-currency transfers, refund links, a split across two accounts for each kind, a budget transfer's amount |
| Attachments | listed per transaction, file written on request | add (image or PDF), remove; kept by every edit | two receipts added, one read back byte for byte, kept through an expense and a transfer edit (both legs), one removed, the rest deleted with the transaction (MoneyWiz removes its copies) |
| Transaction state | yes | cleared/pending, reconciled, void | yes |
| Balance adjustments (reconcile transactions) | balance set, difference booked | adjust to a balance on a date (cash, bank, credit card, investment cash; forex per currency), delete | end-of-day dating against a same-day expense, a second adjustment after later activity, delete restores the balance, forex position created and priced by MoneyWiz |
| Categories | yes | create, rename, re-parent, reorder, merge, delete | yes, including deleting one still in use (MoneyWiz unlinks its transactions) |
| Payees, tags | yes | create, rename, merge, delete | yes |
| Currencies | yes | default currency, manual rates, display list | manual rate set/clear; net worth and conversions computed by MoneyWiz |
| Investments | holdings, shares, price, cost basis | buy, sell, edit trade, delete trade, create/delete holding, manual price, share count, cost basis | buy → second buy → edit → sell → delete, with shares, cost basis and cash checked at each step |
| Loans | accounts, payment plan, full installment schedule | — | reads |
| Budgets | definition, current period's budgeted, available, spent and remaining, monitored accounts, categories and tags | expense and income budgets, repeating or one-time: create, update (amount for chosen periods, rollover, tags, name, period, monitored items), delete; budget groups: create, update membership, delete; transfers between budgets | monthly budget through spending past its limit, a raise for the current and future periods, rollover, tag filter added and removed, rename; one-time budget; group membership; a transfer, its edit and deletion; reordering; real budgets keep their order; MoneyWiz's "exceeded" verdict returned by the tool |
| Scheduled transactions | definition, next due date, repeat rule, app-computed upcoming dates, completion | expense, income, transfer: create, update, delete, skip, execute (with an amount override) | monthly bill with category, tags and payee: edit → execute → skip → execute a different amount → new rule; weekly series of 3 with weekend handling to completion; last-weekday-of-month rule; transfer with both legs; one-off |

## Not covered yet

* **Share exchanges** (swapping one holding for another).
  `exchangeInvestmentsShares:…` creates nothing without a `preparedValuation`
  object from `investmentExchangeValuationForAccount:…`, and that call returns
  nil for manually priced holdings. Passing such an object between calls would
  also need the agent to keep a reference to a non-managed object across calls.
* **Loan creation and payment-plan edits**: `createLoanAccountForUser:…` takes 28
  arguments including a generated payment plan.
* **Saved reports, info cards, file import.**
* Archiving is checked by hand, not by the live suite: MoneyWiz moves an
  archived account to "no group" and renumbers the user's real ungrouped
  accounts, which the suite must not touch.
* Scheduled transactions: loan payment schedules are read-only (MoneyWiz
  derives them from the loan's payment plan). MoneyWiz's scheduled transfers
  have no fee (its editor always passes 0). A schedule in another currency is
  converted at MoneyWiz's rate each time it is recorded; cross-currency
  scheduled transfers are not verified. MoneyWiz has no business method to
  pause a schedule. Its execute method would record a completed series'
  last occurrence again, so the server refuses to execute or skip a schedule
  MoneyWiz reports as completed.
* Balance adjustments are not edited in place (MoneyWiz's editor deletes and
  recreates them too); `update_transaction` says so. A budget transfer's two
  budgets are kept when it is edited. Investment buys and sells are edited
  with `update_trade`. A transaction split across accounts is not edited as a
  whole: MoneyWiz keeps no link between the parts.
* Deleting an expense deletes its refunds too (MoneyWiz's rule); the result
  lists them.
* An attachment that MoneyWiz has not downloaded from iCloud yet cannot be
  read; `get_attachment` says so.

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
  prices it without saving, once its main queue is free (seconds later while
  it is busy uploading to iCloud). The server waits up to 15 seconds for that
  change and saves it, and only when nothing else is pending.
* MoneyWiz answers each new transfer with a "transfer succeeded" dialog from
  `-[iphoneMoneyWizViewController onTransactionCreated:]`, which runs inside the
  create call and does nothing else. The server skips that observer for its own
  transfer calls only. If a MoneyWiz update renames it, the dialog comes back
  (and blocks quitting the app until dismissed); writes are unaffected.
* Several MCP clients may be attached at once (say hermes and Claude). The
  dialog suppression patches nothing between calls; 0.1.1–0.1.2 kept a code
  patch in place, and two clients unloading in the wrong order crashed
  MoneyWiz.
* `deleteBudget:` lowers the display order of every budget above the deleted
  one in every group. `delete_budget` puts the other groups back in their
  previous order with MoneyWiz's own reorder call.
* MoneyWiz limits how many budgets a subscription may hold; `create_budget`
  refuses when `isBudgetsLimitReached`, as the editor does.
* Recording a description in MoneyWiz's autocomplete history (a budget
  transfer does) schedules `-[DataAccessor purge1YearOldStringHistoryItems]`,
  which deletes year-old suggestions a moment later without saving. That
  upkeep does not hold up writes; the next save commits it, as MoneyWiz's
  own would.
* MoneyWiz's edit methods set a transaction's attachments to exactly the list
  they are given, so every edit passes the current ones. Up to 0.1.4 edits
  passed none, which would have removed them.
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
which the server skips for its own scheduled-transaction calls. Budgets add
two: `onBudgetCreated:` selects a new budget in the sidebar, and
`onBudgetsWarning:` raises "the following budgets are low / exceeded" whenever
a transaction pushes a budget over. The second one also fired for a plain
`create_transaction` in 0.1.3 and earlier; it is now skipped for every saving
call. These were found by reading the observers and confirmed by checking what
MoneyWiz presents after the live suite, without the tracer. MoneyWiz also
updates its Dock badge (`-[iosMoneyWizAppDelegate updateAppBadgeNumber]`, the
count of due bills) after scheduled-transaction changes, as it does for edits
in the app. `set_default_currency` was not exercised, to leave the ledger's
currency alone. `adjust_balance` was checked by its live test and a look at
what MoneyWiz presents afterwards, not under the full tracer (running that
tracer next to other attached clients is what crashed the app once). With
`MONEYWIZ_LAUNCH=1` the app is started hidden (`open -g -j`).
