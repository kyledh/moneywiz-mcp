# Status

Verified against MoneyWiz 2026.44.4 (build 466). "Verified" means performed on
real objects through the tools, read back from the app, and reverted, with
account, transaction, category, payee and tag counts returning to their
starting values (`tests/test_live.py`).

## Covered

| Area | Read | Write | Verified live |
| --- | --- | --- | --- |
| Accounts | yes | create, update, archive/unarchive, move, delete (optionally converting its transfers, as MoneyWiz's delete dialog offers) | cash, checking, savings, credit card, investment, forex; deleting an account with transfers both ways, converted (the other account's balance unchanged, a pending leg still pending, an attachment kept) and not converted (both legs gone); a rename of each kind keeps every other stored setting (online-banking switches, display modes, cash date, limits) and the budgets that monitor it, including a budget that monitors only that account |
| Account groups | yes | create, update (incl. membership), delete, reorder | yes |
| Transactions | yes | expense, income, transfer, refund: create, update, delete; one purchase, income or refund across several accounts; budget transfers: update; an income turned into a refund of an expense; many at once: status, delete | single and split categories, foreign currency, fees, cheque numbers, tags, both transfer legs, cross-currency transfers, refund links, a split across two accounts for each kind, a budget transfer's amount, an income with an attachment, tags and reconciled state turned into a refund (categories in proportion), status and delete for a selection with a transfer and a refund; memo-only edits of a negative income, expense and refund, an expense and an income with a fee, a transfer with a fee in one currency and across two, a foreign-currency expense, an expense booked against a forex position and one timed to a fraction of a second leave every stored value of both legs as it was; a new amount on a foreign-currency expense keeps the foreign amount and gives the rate |
| Attachments | listed per transaction, file written on request | add (image or PDF), remove; kept by every edit | two receipts added, one read back byte for byte, kept through an expense and a transfer edit (both legs), one removed, the rest deleted with the transaction (MoneyWiz removes its copies) |
| Transaction state | yes | cleared/pending, reconciled, void, for one transaction or a list | yes |
| Balance adjustments (reconcile transactions) | balance set, difference booked | adjust to a balance on a date (cash, bank, credit card, investment cash; forex per currency), delete | end-of-day dating against a same-day expense, a second adjustment after later activity, delete restores the balance, forex position created and priced by MoneyWiz |
| Categories | yes | create, rename, re-parent, reorder, merge, delete | yes, including deleting one still in use (MoneyWiz unlinks its transactions) |
| Payees, tags | yes | create, rename, merge, delete | yes |
| Currencies | yes | default currency, manual rates, display list | manual rate set/clear; net worth and conversions computed by MoneyWiz |
| Investments | holdings, shares, price, cost basis, stock-split trades, currency exchanges (amounts as entered and the positions' changes) | buy, sell, edit trade, move a trade to another account, delete trade, stock split (and reverse split), currency exchange in a forex account: create, edit, delete; create/delete holding, manual price, share count, cost basis | buy → second buy → edit → sell → delete, with shares, cost basis and cash checked at each step; a 4-for-1 split across two accounts and a 1-for-10 reverse split (shares, unchanged cost basis and cash, the adjusted manual price, nothing realized); a trade with an attachment moved to another account and renamed, the file read back byte for byte; USD → EUR with a fee, valued by MoneyWiz from its own rates, edited with its attachment kept, deleted with the USD position restored |
| Loans | accounts, payment plan, full installment schedule | — | reads |
| Reports | name, kind, period, breakdown, chart, filters, group; figures as MoneyWiz computes them (rows, totals, series, statistics, transactions per row), for a saved report or for one computed without saving | 22 report kinds: create (saved), rename, move, reorder, delete; report groups: create, update membership, reorder, delete | every kind created and saved with nothing left unsaved, a custom period, group membership, a rename that keeps its place, a group deleted with its reports kept; figures of every kind computed with nothing left pending; category, cashflow, statistics, balance and table figures checked against sandbox data; a forecast and a net worth over the whole ledger leave every schedule and loan installment as it was; the same figures computed without saving, with the number of reports unchanged and nothing left unsaved |
| Budgets | definition, current period's budgeted, available, spent and remaining, monitored accounts, categories and tags | expense and income budgets, repeating or one-time: create, update (amount for chosen periods, rollover, tags, name, period, monitored items), delete; budget groups: create, update membership, delete; transfers between budgets | monthly budget through spending past its limit, a raise for the current and future periods, rollover, tag filter added and removed, rename; one-time budget; group membership; a transfer, its edit and deletion; reordering; real budgets keep their order; MoneyWiz's "exceeded" verdict returned by the tool |
| Backups | — | a backup now, as MoneyWiz's automatic backups make one, into its backups folder | the zip held the iCloud store (integrity check passed; transaction and account counts equal to the live ledger); nothing in the ledger changed |
| Scheduled transactions | definition, next due date, repeat rule, app-computed upcoming dates, completion | expense, income, transfer: create, update, delete, skip, execute (with an amount override) | monthly bill with category, tags and payee: edit → execute → skip → execute a different amount → new rule; weekly series of 3 with weekend handling to completion; last-weekday-of-month rule; transfer with both legs; one-off |

## Not covered yet

* **Share-for-share exchanges.** MoneyWiz's exchange transaction is the forex
  account's currency exchange (its editor offers currencies only), which
  `exchange_currency` records. Swapping one security for another has no
  editor of its own in MoneyWiz; record it as a sell and a buy.
* **Loan creation and payment-plan edits.** The path is known but not built:
  MoneyWiz's account wizard generates the installments with
  `+[LoanAccount generatePaymentPlan:andInterest:andNoMonths:andStartDate:andFirstDueDate:currencyName:inContext:]`
  (inserted, unsaved), and passes them, a payments schedule and the plan's
  options (from `PaymentPlanFieldsViewController`) to the 29-argument
  `createLoanAccountForUser:…registerPastTransactions:`, which can also record
  past payments. Loans stay read-only until that is built and verified.
* **File import (CSV, OFX, QIF, CAMT, MT940).** MoneyWiz's importers
  (`CSVImporter`, `OFXImporter`, … `initWithURL:`) feed an interactive wizard
  (account matching, CSV column matchups, duplicate checks) before
  `importDataFromImporter:`. Recording parsed transactions with
  `create_transaction` covers the same ground with more control.
* **Several schedules' overdue occurrences at once.**
  `executeAllWaitingScheduledTransactions:` and `skipAllWaitingScheduledTransactions:`
  are MoneyWiz's auto-pay catch-up for one schedule with auto-pay on, run from
  its own timer; `execute_scheduled` and `skip_scheduled` handle one
  occurrence of any schedule. `moveTransactions:toAccount:` is not called
  anywhere in MoneyWiz; moving goes through the edit methods it would loop
  over (`update_transaction` and `update_trade` with another account).
* **Info cards.** The summary cards of investment accounts and the dashboard
  are display settings. `createSpreadSheetReportForUser:…` (the transactions
  table report) returns nothing on build 466 and is not offered.
* **Restoring a backup** stays with MoneyWiz (Settings > Backup): it replaces
  the whole ledger.
* Archiving is checked by hand, not by the live suite: MoneyWiz moves an
  archived account to "no group" and renumbers the user's real ungrouped
  accounts, which the suite must not touch. `set_default_currency` has not
  been run against the real ledger, to leave its currency alone.
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
  with `update_trade`, currency exchanges with `update_exchange`. A
  transaction split across accounts is not edited as a whole: MoneyWiz keeps
  no link between the parts.

Out of scope: online banking, tax reports, the built-in AI features, Shortcuts,
app preferences, passcode/security and subscriptions. Ledger lifecycle and
iCloud sync stay with MoneyWiz.

## Known constraints

* Deleting an expense deletes its refunds too (MoneyWiz's rule); the result
  lists them.
* A trade moved to another account or given another symbol is deleted and
  recorded again by MoneyWiz's edit method, so it gets a new id; a split's
  flag is kept. That method deletes the trade's stored attachment files
  before recording it again, so the server hands it copies (written to
  MoneyWiz's temporary directory and removed afterwards) and refuses when an
  attachment is not downloaded from iCloud yet.
* A stock split goes into every investment account holding the symbol or
  alias (currency positions excepted), as in MoneyWiz; there is no
  per-account split. It is dated at the start of the day, as MoneyWiz's
  split editor dates it.
* A currency exchange is valued in the account's currency from MoneyWiz's
  exchange rates (fetched online, or manual); without one MoneyWiz records
  nothing, and the tool says so. The fee is part of the position it is paid
  in (100 USD with a 1 USD fee takes 101 USD out of the USD position).
* Converting an account's transfers when deleting it names the deleted
  account as the payee of each income or expense MoneyWiz makes from them.
* An attachment that MoneyWiz has not downloaded from iCloud yet cannot be
  read; `get_attachment` says so.
* A report computed without saving is built in the main context by
  MoneyWiz's own creator (which does not calculate), copied and deleted
  within one main-queue turn; only the copy is calculated, in a child context.
* A backup zips the database while MoneyWiz runs, as its automatic backups
  do. Receipts are not part of an iCloud ledger's backup (MoneyWiz zips the
  store's SQLite files only). Older backups in the folder are kept; MoneyWiz's
  own automatic run would prune to its configured number.

* The server works through a running MoneyWiz. It starts one hidden when none
  is running, keeps it out of the Dock while nobody looks at it, and quits it
  after 10 idle minutes or when the last server using it exits (README,
  "MoneyWiz in the background"). Verified on build 466: launched hidden, the
  first call answered in about 2 s, out of the Dock a second later; quit after
  the idle time and on both ways a client ends a session (input closed,
  SIGTERM); shown without being activated, it was back in the Dock within 3 s
  and was not quit afterwards; no crash reports. While a Frida session is
  attached, MoneyWiz takes about 30 s longer to exit, so the server detaches
  as soon as MoneyWiz agrees to quit. Another client's session (an older
  server) can still hold up the exit that long. Servers before 0.1.6 do not
  take part: they neither keep a MoneyWiz alive nor quit one.
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
* The same holds for everything else an edit method takes (ARCHITECTURE,
  rule 13), so edits hand every value they are not given back as stored.
  Up to 0.1.7 they did not, all verified on sandbox copies before the fix:
  renaming an account through `update_account` took it out of every budget
  that monitored it (`setBudgets:toAccount:` with nil; a budget left
  monitoring nothing was deleted), turned its four online-banking switches
  off and moved an investment account's cash date to the time of the edit;
  a memo-only `update_transaction` flipped a negative income, expense or
  refund, added a transfer's fee to its amount once more (both legs moved),
  unlinked an expense or income from its forex position (the position's
  balance changed) and cut its time to the second; changing a foreign-currency
  expense's amount dropped its foreign amount; trade edits rounded shares,
  price and commission to 15 digits. New credit cards take the second display
  mode MoneyWiz's own cards have (0, not 3).
* A report's figures are computed in a child context that is discarded,
  as MoneyWiz does when it shows a report (see ARCHITECTURE). For a report
  that counts scheduled transactions (a forecast does by default), MoneyWiz
  records their future occurrences for every account, whichever accounts the
  report covers; on a large ledger that takes a few seconds. The net worth
  report has no account filter in MoneyWiz and always covers every account.
* Saved locally is not synced; MoneyWiz uploads to iCloud on its own schedule.
* Several calls are not atomic: each business call saves independently.
* A call whose connection is lost while MoneyWiz handles it (MoneyWiz quit or
  crashed) is reported and not sent again; up to 0.1.7 it was sent again
  after reconnecting (and relaunching MoneyWiz), which could record a write
  twice.
* Signatures are recorded from build 466. On another build, any call whose
  signature changed fails with an explicit error; reads and unchanged methods
  keep working. `scripts/check_build.py` lists, before anything fails, every
  method, muted observer and field the server uses that the running build has
  changed (see the README).

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
what MoneyWiz presents afterwards, not under the full tracer; `split_holding`,
`convert_to_refund`, `set_transactions_status`, `delete_transactions` and
deleting an account with converted transfers, `compute_report`,
`exchange_currency` and `backup` were checked by reading the observers of what
they post (the main window's bulk-delete and the saved-report list's
"report created" observers are empty, and none of them creates a transfer),
not under the tracer either (running that
tracer next to other attached clients is what crashed the app once). The
app is started hidden (`open -g -j`) and moved out of the Dock with the
standard activation policy; it is quit through `-[NSApplication terminate:]`,
the path Cmd-Q takes.
