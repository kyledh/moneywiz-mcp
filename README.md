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
open database and ledger totals. If MoneyWiz is not running you get a clear
error; open the app and retry. The server reconnects by itself after MoneyWiz
restarts.

To let the server open MoneyWiz when it is not running, set
`MONEYWIZ_LAUNCH=1` (`claude mcp add --scope user -e MONEYWIZ_LAUNCH=1 moneywiz
-- moneywiz-mcp`, or `"env": {"MONEYWIZ_LAUNCH": "1"}` in a JSON config).
MoneyWiz is then launched hidden: no window appears and focus stays where it
is (click its Dock icon to show it). The first call waits for the ledger and
returns in a few seconds. A MoneyWiz you already have open is left as it is,
and a ledger behind a passcode still has to be unlocked by hand.

## Tools

57 tools, all prefixed `moneywiz_`:

| Area | Tools |
| --- | --- |
| Status | `status` |
| Accounts | `list_accounts`, `get_account`, `create_account`, `update_account`, `archive_account`, `delete_account`, `move_account` |
| Account groups | `list_account_groups`, `create_account_group`, `update_account_group`, `delete_account_group` |
| Transactions | `list_transactions`, `get_transaction`, `create_transaction`, `update_transaction`, `set_transaction_status`, `delete_transaction`, `adjust_balance` |
| Categories, payees, tags | `list_categories`, `list_payees`, `list_tags`, `create_category`, `update_category`, `reorder_categories`, `create_payee`, `create_tag`, `rename_payee`, `rename_tag`, `delete_catalog_object`, `merge_catalog_objects`, `catalog_usage` |
| Currencies | `currency_settings`, `net_worth`, `set_default_currency`, `get_exchange_rate`, `set_exchange_rate`, `clear_exchange_rate`, `set_display_currencies` |
| Investments | `list_holdings`, `trade`, `update_trade`, `delete_trade`, `create_holding`, `delete_holding`, `set_holding_price`, `set_holding_shares`, `set_holding_cost_basis` |
| Loans | `list_loans`, `loan_schedule` |
| Scheduled transactions | `list_scheduled`, `create_scheduled`, `update_scheduled`, `delete_scheduled`, `skip_scheduled`, `execute_scheduled` |
| Budgets | `list_budgets` |

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
  `exchange_rate`.
* **Transfers** create, edit and delete both legs together. Across currencies,
  give `transfer_amount` (what arrives) or `transfer_exchange_rate`. MoneyWiz's
  "transfer between A and B succeeded" dialog is skipped for transfers made
  through the server; transfers entered in the app still show it.
* **Trades** buy or sell by symbol; a first buy creates the holding, and
  MoneyWiz recalculates shares, cost basis and the account's cash. Selling a
  symbol you do not hold opens a short position (negative shares), and a later
  buy covers it. A negative commission is a rebate: a buy then costs
  `shares x price - rebate`.
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
  "following budgets are low / exceeded" warning is skipped for every write,
  so check `list_budgets` after recording spending.
* Writes wait while MoneyWiz has unsaved changes of its own and are refused if
  they persist for 5 seconds (an open editor), so a half-finished entry is
  never committed for you. Deletions that arrived from another device through
  iCloud, which MoneyWiz holds unsaved although they are already stored, do
  not count.
* Deleting an account deletes its transactions, so the tool refuses unless you
  pass the exact transaction count it reports. Archiving is usually better.
* Deleting a category also deletes its subcategories; transactions that used
  it, or a deleted payee or tag, stay and lose that link. The result lists what
  referenced it. Deleting a holding keeps its trades unless `delete_trades` is
  set. Merging is usually the better choice.
* Each call saves on its own, like an edit in the app; several calls are not
  one transaction.
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
