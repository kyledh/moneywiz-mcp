# moneywiz-mcp

An [MCP](https://modelcontextprotocol.io) server that reads and edits your
[MoneyWiz](https://wiz.money) ledger on macOS by calling MoneyWiz's own business
methods inside the running app. Balances, budgets, category allocations,
transfer legs and iCloud sync stay the app's job: this server supplies
validated inputs and reads the results back from the live object graph.

> **Unofficial.** Not affiliated with or endorsed by SilverWiz, the makers of
> MoneyWiz. It is unrelated to the read-only connector built into MoneyWiz.
> It writes to your real financial data and attaches a debugger-level hook to
> the app, so read [Requirements](#requirements) and [Safety](#safety) first.

Verified against **MoneyWiz 2026.44.4 (build 466)** on macOS (Apple silicon).

## Requirements

* macOS with MoneyWiz installed from the Mac App Store, running with its
  ledger open.
* [`uv`](https://docs.astral.sh/uv/) (or any Python 3.11+ installer).
* **Permission to attach to MoneyWiz.** MoneyWiz is a hardened-runtime build
  without `get-task-allow`. This server has been tested with System Integrity
  Protection disabled; macOS normally refuses debugger attachment to such apps
  while SIP is on. The debug authorization must also allow it, once per machine:

  ```sh
  # keep a copy of the current rule so you can restore it later
  security authorizationdb read system.privilege.taskport > ~/taskport-right-backup.plist

  sudo DevToolsSecurity -enable
  sudo security authorizationdb write system.privilege.taskport is-developer
  ```

  The last command lets members of the `_developer` group attach without a
  password prompt, which is what makes this work for MCP clients started from a
  terminal or over SSH. Check membership with `id`; join with
  `sudo dseditgroup -o edit -a "$USER" -t user _developer`. The trade-off: any
  process running as you can then attach to your other apps. To undo it:

  ```sh
  sudo security authorizationdb write system.privilege.taskport < ~/taskport-right-backup.plist
  ```

  Without this, every call fails with *"macOS denied task_for_pid"*.

## Install

```sh
uv tool install moneywiz-mcp      # installs the `moneywiz-mcp` command
```

or run it on demand with `uvx moneywiz-mcp`. The first start downloads the
Frida runtime, so an installed tool starts faster under MCP clients with short
start-up timeouts.

## Connect a client

The server speaks MCP over stdio. Use `moneywiz-mcp` as the command (or
`uvx` with argument `moneywiz-mcp`).

**Claude Code**

```sh
claude mcp add --scope user moneywiz -- moneywiz-mcp
```

**Codex**

```sh
codex mcp add moneywiz -- moneywiz-mcp
```

**Claude Desktop and other JSON-configured clients** — add to `mcpServers`
(Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "moneywiz": { "command": "uvx", "args": ["moneywiz-mcp"] }
  }
}
```

GUI apps may not see your shell's `PATH`; if the command is not found, use the
absolute path from `which moneywiz-mcp` or `which uvx`.

Then ask the client to call `moneywiz_status`: it reports the app version, the
open database and ledger totals. The server reconnects by itself on the next
call after MoneyWiz restarts. A call that loses the connection while MoneyWiz
handles it (MoneyWiz quit or crashed) is reported and never sent again: it may
have been carried out, so check the ledger before repeating it.

### MoneyWiz in the background

You do not need to open MoneyWiz first. When it is not running, the server
starts it hidden: no window appears, focus stays where it is, and once the
ledger is open (a few seconds; the first call waits for it) MoneyWiz is kept
out of the Dock and Cmd-Tab. The server then looks after that MoneyWiz:

* It quits it, as Cmd-Q would, after 10 minutes in which no MCP client used it,
  or when the last server using it exits (the client session ends).
* It quits only when nothing holds MoneyWiz: no unsaved changes and no iCloud
  sync in progress (MoneyWiz's own record of its CloudKit work).
* Opening MoneyWiz yourself (from the Dock, Finder or Spotlight) shows the same
  app; within a few seconds it is back in the Dock and it is yours: the server
  never hides or quits it again. A MoneyWiz you started yourself is never
  hidden or quit.

Several clients (say hermes and Claude) share this through a small file,
`~/Library/Caches/moneywiz-mcp/lifecycle.json`. Settings, as environment
variables of the server (`claude mcp add --scope user -e
MONEYWIZ_IDLE_MINUTES=30 moneywiz -- moneywiz-mcp`, or `"env": {...}` in a JSON
config): `MONEYWIZ_IDLE_MINUTES` (default 10; `0` never quits) and
`MONEYWIZ_LAUNCH=0` (never start MoneyWiz; calls then fail until you open it).
A ledger behind a passcode still has to be unlocked by hand.

## Tools

90 tools, all prefixed `moneywiz_`:

| Area | Tools |
| --- | --- |
| Status, backup | `status`, `backup` |
| Accounts | `list_accounts`, `get_account`, `create_account`, `update_account`, `archive_account`, `delete_account`, `move_account` |
| Account groups | `list_account_groups`, `create_account_group`, `update_account_group`, `delete_account_group` |
| Transactions | `list_transactions`, `get_transaction`, `create_transaction`, `update_transaction`, `set_transaction_status`, `set_transactions_status`, `delete_transaction`, `delete_transactions`, `convert_to_refund`, `adjust_balance`, `create_split_transaction`, `add_attachment`, `get_attachment`, `remove_attachment` |
| Categories, payees, tags | `list_categories`, `list_payees`, `list_tags`, `create_category`, `update_category`, `reorder_categories`, `create_payee`, `create_tag`, `rename_payee`, `rename_tag`, `delete_catalog_object`, `merge_catalog_objects`, `catalog_usage` |
| Currencies | `currency_settings`, `net_worth`, `set_default_currency`, `get_exchange_rate`, `set_exchange_rate`, `clear_exchange_rate`, `set_display_currencies` |
| Investments | `list_holdings`, `trade`, `update_trade`, `delete_trade`, `split_holding`, `exchange_currency`, `update_exchange`, `create_holding`, `delete_holding`, `set_holding_price`, `set_holding_shares`, `set_holding_cost_basis` |
| Loans | `list_loans`, `loan_schedule` |
| Scheduled transactions | `list_scheduled`, `create_scheduled`, `update_scheduled`, `delete_scheduled`, `skip_scheduled`, `execute_scheduled` |
| Reports | `list_reports`, `get_report_data`, `compute_report`, `create_report`, `update_report`, `reorder_reports`, `delete_report`, `list_report_groups`, `create_report_group`, `update_report_group`, `delete_report_group`, `reorder_report_groups` |
| Budgets | `list_budgets`, `create_budget`, `update_budget`, `delete_budget`, `transfer_between_budgets`, `list_budget_groups`, `create_budget_group`, `update_budget_group`, `delete_budget_group`, `reorder_budgets`, `reorder_budget_groups` |

What is and is not covered yet is listed in [STATUS.md](STATUS.md).

### Conventions

* **Amounts** are decimal strings (`"12.50"`). The kind (`expense`, `income`,
  `transfer`, `refund`, buy/sell) decides the direction, so pass positive
  amounts: MoneyWiz stores an expense of `5` as `-5`, and a negative amount
  reverses that, exactly as MoneyWiz would. Values are handed to MoneyWiz as
  given (it stores doubles, 15 significant digits); a transaction's fields read
  back at that precision (`-26.6`, not `-26.600000000000001`), while balances
  and totals are rounded to 8 decimals to drop the noise of summing doubles.
* **Dates** accept `YYYY-MM-DD` (local noon, so the day never shifts) or a full
  ISO 8601 timestamp. Output is local time. List ranges are `[start, end)`.
  Each date takes its own day's offset, so days on the other side of a
  daylight-saving change stay whole.
* **Accounts** by id or exact name; **categories** by id or full path
  (`"Food/Coffee"`).
* **Category splits** are a list of categories and amounts:
  `[{"category": "Food/Coffee", "amount": "10.00"}, {"category": "Tips", "amount": "2.50"}]`.
  MoneyWiz does not require them to add up to the amount, so check the totals
  you send. Changing the amount of a single-category transaction moves the
  allocation with it, as in the app; a split keeps its amounts unless you pass
  a new list. Refunds normally use the expense's category, as in MoneyWiz.
* **Foreign currency**: give `original_amount` + `original_currency` alongside
  the amount in the account's currency; the rate is derived, or pass
  `exchange_rate`. Changing only the amount keeps the foreign amount and
  derives the new rate.
* **Edits change only what you pass.** Everything else goes back to MoneyWiz
  exactly as stored, because its edit methods set all they are given: an
  account keeps the budgets that monitor it, its online-banking switches,
  display modes and cash date; a transaction keeps a negative amount, a
  transfer's fee, the forex position or holding it is booked against, and its
  time to the microsecond; a trade keeps its share count, price and commission
  at full precision.
* **Transfers** create, edit and delete both legs together. Across currencies,
  give `transfer_amount` (what arrives) or `transfer_exchange_rate`. MoneyWiz's
  "transfer between A and B succeeded" dialog is skipped for transfers made
  through the server; transfers entered in the app still show it.
* **Trades** buy or sell by symbol; a first buy creates the holding, and
  MoneyWiz recalculates shares, cost basis and the account's cash. Selling a
  symbol you do not hold opens a short position (negative shares), and a later
  buy covers it. A negative commission is a rebate: a buy then costs
  `shares x price - rebate`. `update_trade` can move a trade to another
  investment account; MoneyWiz does that (and a change of symbol) by
  recording the trade again, so it comes back with a new id (`replaced`
  names the old one), its attachments carried over.
* **Stock splits** (`split_holding`) are MoneyWiz's own: give the shares
  before and after (`1` → `4` for a 4-for-1 split, `10` → `1` for a reverse
  split) and the day. In every investment account holding the symbol,
  MoneyWiz records the difference as a buy (or a sell) at price 0, marked
  `stock_split`, so cost basis and gains stay as they were; a manually priced
  holding gets the previous day's price divided by the ratio.
* **Refund from income** (`convert_to_refund`): an income that was really
  money back for an expense becomes a refund of it, as MoneyWiz's "refund
  from income" does: same account, amount, date, payee, notes, attachments
  and state, the expense's categories in proportion, both sets of tags. The
  income is deleted.
* **Several transactions at once**: `set_transactions_status` and
  `delete_transactions` work on a list of ids, as a selection in MoneyWiz's
  transaction list does (reconciling and deleting are one MoneyWiz call each).
* **Several accounts** (`create_split_transaction`): one purchase, income or
  refund paid from several accounts becomes one ordinary transaction per
  account, as in MoneyWiz's editor with several accounts selected; the
  category split follows each part. MoneyWiz does not link the parts, so
  each is edited or deleted on its own.
* **Attachments** (receipts): `add_attachment` takes a file on this Mac (up to
  25 MB); MoneyWiz keeps its own copy, sized per its receipt setting, and
  syncs it through iCloud. `get_transaction` lists them and `get_attachment`
  writes one to a file. Edits keep them, and deleting the transaction deletes
  them.
* **Saved reports** (`create_report`) are reports in MoneyWiz's Reports
  section, made as its report editor makes them: balance over time, forecast,
  net worth, cashflow, statistics, budget balance and comparison, categories,
  payees and tags (totals, over time, drill-down), profit and loss, summary
  breakdowns and the tables of categories, payees and tags over time or by
  one another. MoneyWiz computes and shows them; a period such as
  `this_month` stays relative. `get_report_data` returns a saved report's
  figures as MoneyWiz computes them when the report is opened: its rows
  (with nested rows for tables and drill-downs, and MoneyWiz's own total and
  average rows), totals, chart series over time, statistics, and optionally
  each row's transactions. MoneyWiz computes on a temporary copy that it
  discards, so reading a report writes nothing. `compute_report` gives the
  same figures for any report kind and settings without saving a report
  (spending by category last month, cashflow by month this year).
* **Currency exchanges** (`exchange_currency`) are MoneyWiz's exchange inside
  a forex account: an amount of one currency for an amount of another, with a
  fee in either. MoneyWiz values it in the account's currency from its own
  exchange rates and records nothing without one (set a manual rate first).
  The fee comes out of the position it is paid in.
* **Balance adjustments** (`adjust_balance`) are MoneyWiz's "adjust balance":
  give the balance a statement shows on a date and MoneyWiz books the
  difference from its own running balance as a reconcile transaction. A bare
  date means the end of that day. Forex accounts adjust one currency at a
  time. To change an adjustment, delete it and adjust again, as MoneyWiz's
  editor does.
* **Scheduled transactions** (bills, recurring income and transfers) repeat
  every N days, weeks, months or years, optionally on a rule such as the
  last weekday of the month, with weekend handling and an optional number of
  occurrences. Nothing is recorded until one is executed (or auto-paid by
  MoneyWiz); executing records the next due occurrence on its due date, and
  skipping passes over it. As in MoneyWiz, changing the date or repeat rule,
  or turning on auto-pay, restarts the schedule's history; other edits keep
  it. A completed schedule is reported as `finished` and kept. Loan payment
  schedules are read-only here, as MoneyWiz maintains them from the loan.
* **Budgets** are expense or income budgets over categories and accounts,
  optionally limited to transactions with given tags. They repeat every N
  days, weeks, months or years from a start date (default: the 1st of this
  month), or run once between two dates. `list_budgets` reports the current
  period as MoneyWiz computes it: `budgeted`, `available` (with rollover and
  budget transfers), `spent` and `remaining` (negative when over). A new
  amount on a repeating budget applies to the current period unless
  `apply_to` names others (`past`, `current`, `future`), as in MoneyWiz's
  editor. MoneyWiz's limit on the number of budgets for the subscription is
  kept.
* Unknown payee and tag names are created, as in the app (an existing name is
  reused). Unknown categories are rejected.
* Read balances from accounts. Never sum transaction rows: they include
  pending and void entries and both legs of each transfer.

## Safety

* The server checks only what it needs to build a valid call (a parseable
  number or date, an existing account or category, the right object types),
  because an exception raised inside the app would terminate it. Value rules
  are MoneyWiz's own: account names go through MoneyWiz's name validator and
  currencies through its currency list; sign, zero, precision, split totals
  and duplicate payee or tag names are left to MoneyWiz, as in its editors.
* Writes are silent: no dialogs, notifications, sounds, progress overlays or
  window activation (traced across every write tool on build 466). The two
  dialogs MoneyWiz raises in reaction, "transfer succeeded" and "all payments
  of this scheduled transaction are done: delete or keep it?", are skipped.
  MoneyWiz does refresh its Dock badge (the number of due bills). The
  "following budgets are low / exceeded" warning is skipped for every write;
  its verdict comes back as `budget_warnings` (`low`, `exceeded`) in the
  tool's result instead.
* Writes wait while MoneyWiz has unsaved changes of its own and are refused if
  they persist for 5 seconds (an open editor), so a half-finished entry is
  never committed for you. Reads work all the while. Deletions that arrived from another device through
  iCloud, which MoneyWiz holds unsaved although they are already stored, do
  not count.
* Deleting an account deletes its transactions, so the tool refuses unless you
  pass the exact transaction count it reports. Archiving is usually better.
  Its transfers go too, both legs, which changes the other accounts'
  balances; `convert_transfers` keeps the other side of each as an ordinary
  income or expense (MoneyWiz names the deleted account as its payee), as
  MoneyWiz's delete dialog offers.
* Deleting several transactions also deletes what goes with each (a
  transfer's other leg, an expense's refunds); the result lists them.
* Deleting a category also deletes its subcategories; transactions that used
  it, or a deleted payee or tag, stay and lose that link. The result lists what
  referenced it. Deleting a holding keeps its trades unless `delete_trades` is
  set. Merging is usually the better choice.
* Each call saves on its own, like an edit in the app; several calls are not
  one transaction. Before many changes at once, `backup` makes a backup as
  MoneyWiz's automatic backups do, into MoneyWiz's backups folder; restoring
  one is done in MoneyWiz (Settings > Backup). An iCloud ledger's backup holds
  its database, not the receipt files.
* Saved locally is not synced: MoneyWiz uploads to iCloud on its own schedule.
* Avoid editing the same records in MoneyWiz while a tool call runs.

## How it works

```
MCP client ──stdio──▶ moneywiz-mcp (Python)
                        │ domain modules: accounts, transactions, catalog, …
                        ▼
                      Frida session ──▶ agent inside MoneyWiz (main queue)
                                           ├─ NSFetchRequest over the live context
                                           └─ MoneyWizManager business methods
```

One small, domain-free agent resolves classes, selectors and properties by
name, checks each method's type encoding against a recorded signature table
before calling it, and runs everything on the app's main queue. All business
rules live in Python. See [ARCHITECTURE.md](ARCHITECTURE.md).

## Development

```sh
git clone https://github.com/kyledh/moneywiz-mcp && cd moneywiz-mcp
uv sync                                   # .venv with the package (editable) and pytest
uv run pytest -q                          # offline tests
MONEYWIZ_LIVE_TESTS=1 uv run pytest -q tests/test_live.py
```

The live suite writes to the real ledger: it creates everything under an
`MCP-TEST-` prefix, removes it again (also after failures), and asserts that
account, transaction, category, payee and tag counts, and the positions of your
accounts, groups and categories, end where they started.

### After a MoneyWiz update

The server records the type encoding of every MoneyWiz method it calls, for
one build, and refuses a call whose signature has changed. With the new build
running:

```sh
uv run scripts/check_build.py             # what changed for the server, if anything
uv run scripts/check_build.py --record    # nothing changed: record the new build
MONEYWIZ_LIVE_TESTS=1 uv run pytest -q tests/test_live.py
```

The report lists every method, muted observer and Core Data field the server
uses that the new build changed or removed. `--record` refuses while there are
such changes. Adapt the calls first, then pass `--accept-changed`.

The compiled agent is checked in at `src/moneywiz_mcp/agent.js`. After editing
`agent/src/agent.js`, rebuild it:

```sh
(cd agent && npm ci)                      # once: frida-objc-bridge
uv run scripts/build_agent.py
```

## License

MIT, see [LICENSE](LICENSE). The bundled agent includes
[frida-objc-bridge](https://github.com/frida/frida-objc-bridge); see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
