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

51 tools, all prefixed `moneywiz_`:

| Area | Tools |
| --- | --- |
| Status | `status` |
| Accounts | `list_accounts`, `get_account`, `create_account`, `update_account`, `archive_account`, `delete_account`, `move_account` |
| Account groups | `list_account_groups`, `create_account_group`, `update_account_group`, `delete_account_group` |
| Transactions | `list_transactions`, `get_transaction`, `create_transaction`, `update_transaction`, `set_transaction_status`, `delete_transaction` |
| Categories, payees, tags | `list_categories`, `list_payees`, `list_tags`, `create_category`, `update_category`, `reorder_categories`, `create_payee`, `create_tag`, `rename_payee`, `rename_tag`, `delete_catalog_object`, `merge_catalog_objects`, `catalog_usage` |
| Currencies | `currency_settings`, `net_worth`, `set_default_currency`, `get_exchange_rate`, `set_exchange_rate`, `clear_exchange_rate`, `set_display_currencies` |
| Investments | `list_holdings`, `trade`, `update_trade`, `delete_trade`, `create_holding`, `delete_holding`, `set_holding_price`, `set_holding_shares`, `set_holding_cost_basis` |
| Loans | `list_loans`, `loan_schedule` |
| Budgets, scheduled | `list_budgets`, `list_scheduled` |

What is and is not covered yet is listed in [STATUS.md](STATUS.md).

### Conventions

* **Amounts** are decimal strings (`"12.50"`). Pass positive amounts; the kind
  (`expense`, `income`, `transfer`, `refund`, buy/sell) decides the direction.
  MoneyWiz stores doubles, which hold 15 significant digits: amounts up to that
  precision (`"0.351623471"`) are stored and read back as written, and longer
  ones are refused rather than rounded silently. A transaction's fields read
  back at that precision (`-26.6`, not `-26.600000000000001`); balances and
  totals are rounded to 8 decimals to drop the noise of summing doubles.
* **Dates** accept `YYYY-MM-DD` (local noon, so the day never shifts) or a full
  ISO 8601 timestamp. Output is local time. List ranges are `[start, end)`.
* **Accounts** by id or exact name; **categories** by id or full path
  (`"Food/Coffee"`).
* **Category splits** are a list that must add up to the amount:
  `[{"category": "Food/Coffee", "amount": "10.00"}, {"category": "Tips", "amount": "2.50"}]`.
  Changing the amount of a single-category transaction moves the allocation
  with it; a split transaction needs a new list. Refunds take expense
  categories, as in MoneyWiz: the money goes back to what was spent on.
* **Foreign currency**: give `original_amount` + `original_currency` alongside
  the amount in the account's currency; the rate is derived, or pass
  `exchange_rate`.
* **Transfers** create, edit and delete both legs together. Across currencies,
  give `transfer_amount` (what arrives) or `transfer_exchange_rate`. MoneyWiz's
  "transfer between A and B succeeded" dialog is skipped for transfers made
  through the server; transfers entered in the app still show it.
* **Trades** buy or sell by symbol; a first buy creates the holding, and
  MoneyWiz recalculates shares, cost basis and the account's cash. A negative
  commission is a rebate: a buy then costs `shares x price - rebate`.
* Unknown payee and tag names are created, as in the app. Unknown categories
  are rejected.
* Read balances from accounts. Never sum transaction rows: they include
  pending and void entries and both legs of each transfer.

## Safety

* Every input is validated before MoneyWiz is touched, because an exception
  raised inside the app would terminate it.
* Writes are silent: no dialogs, notifications, sounds, progress overlays or
  window activation (traced across every write tool on build 466). The one
  dialog MoneyWiz raises in reaction, "transfer succeeded", is skipped.
* Writes wait while MoneyWiz has unsaved changes of its own and are refused if
  they persist for 5 seconds (an open editor), so a half-finished entry is
  never committed for you.
* Deleting an account deletes its transactions, so the tool refuses unless you
  pass the exact transaction count it reports. Archiving is usually better.
* Categories, payees, tags and holdings that are still referenced cannot be
  deleted; merge them instead.
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
