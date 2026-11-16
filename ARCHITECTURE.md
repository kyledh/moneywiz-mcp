# Design

## Rules

1. **Resolve by name, never by address.** Classes, selectors, properties and
   entities are looked up by name in the running app. There are no ivar
   offsets, fixed addresses or binary hash checks, so an app update only breaks
   what actually changed.
2. **Check the type encoding before every call.**
   `src/moneywiz_mcp/signatures.json` holds the recorded build-466 encodings of
   the `MoneyWizManager` instance methods. `native.invoke` attaches the expected
   encoding and the agent compares it with the live method, then checks each
   argument's tag against the encoded parameter type. A changed signature fails
   loudly instead of pushing misaligned arguments onto the stack.
3. **Validate what a call needs before touching the app.** An uncaught
   Objective-C exception inside MoneyWiz terminates it, and the injection layer
   cannot catch one. So key paths are walked through the Core Data model,
   selectors are checked with `respondsToSelector:`, references are resolved to
   real objects of the right kind, and amounts and dates are parsed before
   anything is called.
4. **Leave value rules to MoneyWiz.** The server adds no rules of its own on
   top of what MoneyWiz accepts: no sign, zero, precision, split-total,
   category-kind or duplicate-name checks. Where MoneyWiz has a rule, the
   server asks MoneyWiz (`DataValidatorHelper` for account and category names,
   `currenciesArray` for currencies). The few guards that remain prevent states
   that would break the app or lose data: a category cycle, merging an object
   into itself, a zero amount we would divide by, a transfer to the same
   account, deleting an account without
   confirming its transaction count (MoneyWiz asks for that too).
5. **Never scan the heap.** `ObjC.choose` walks malloc zones; doing that on the
   main thread while other threads allocate wedged MoneyWiz during
   development. Use documented state instead: `+[MoneyWizManager isInitialized]`
   before `+sharedManager`, which would otherwise create and configure a
   manager when none exists.
6. **One queue.** The user's managed object context is
   `NSMainQueueConcurrencyType`, so all work is scheduled on the main queue.
7. **Call business methods, not setters.** `createWithdrawTransactionForAccount:…`
   recalculates balances, updates budget links, creates missing payees and
   saves. Writing `amount` directly would leave the ledger inconsistent.
8. **Read back after writing.** Every write returns the object re-read from the
   live graph, so the caller sees what MoneyWiz stored: the sign it chose, the
   allocation it adjusted, the id it assigned.
9. **Refuse to write while the app has unsaved changes.** A business method
   saves the whole context, so it would commit whatever else is pending.
   Inserted or deleted objects, or updated objects with real `changedValues`,
   count; MoneyWiz touches investment holdings without changing values at
   launch, so those do not. Some pending objects are the app's own and
   short-lived: after data changes (an iCloud import or one of our writes),
   `MWPortfolioStoryModel` inserts a `CustomReport` to compute the portfolio
   overview and deletes it about a second later. The agent refuses before
   calling anything, with a named error, and `native.py` waits up to 5 s for
   the context to clear; an editor left open is still reported.
10. **Silence only the dialogs our own calls raise.** A call may name an app
   observer that does nothing but present UI (the "transfer succeeded" alert).
   The agent replaces that method once with a pass-through and skips it only
   while that call runs on the main queue. The app's own calls are untouched,
   and Frida restores the method when the agent unloads.
11. **Ship only what works.** A business call that cannot be completed reliably
   (for example share exchanges, see STATUS.md) is left out rather than exposed
   with caveats or kept as unused machinery.

## Layers

| Layer | Responsibility |
| --- | --- |
| `agent/src/agent.js` | Domain-free: fetch requests, predicates, key-path reads, typed invocation, context state, save. |
| `native.py` | One Frida session per process, lazy attach, re-attach after an app restart, typed-argument helpers, `$date`/`$dec`/`$int` decoding, signature lookup. |
| `values.py` | Amount, date and output formatting shared by all domains. |
| `accounts.py`, `transactions.py`, `catalog.py`, `settings.py`, `investments.py`, `portfolio.py` | Business rules per domain: which selector, in which order, with which guards. |
| `server.py` | MCP tool surface, argument descriptions, read/write annotations. |

The agent knows nothing about accounts or transactions; the domain modules know
nothing about Frida. Adding a domain means adding one Python module.

## Value handling

* Money is stored as IEEE doubles. Reads emit the shortest exact decimal, so
  float noise such as `4.547473508864641e-13` in a cleared balance prints as
  `0`, and `1234.5600000000002` as `1234.56`. Nothing else is rounded.
* Typed arguments are explicit: `{"s": …}` NSString, `{"n": …}` NSNumber
  double, `{"ni": …}` / `{"nbig": "…"}` NSNumber int64 (group ids exceed
  JavaScript's safe integers, so they travel as text), `{"dec": "…"}`
  NSDecimalNumber, `{"date": …}` NSDate, `{"ref": [entity, gid]}` a managed
  object, `{"arr": […]}`, `{"nil": true}`, `{"user": true}`, and
  `{"b"/"i"/"d": …}` for primitive parameters. Trade shares, prices and
  commissions are primitive doubles in MoneyWiz's signatures.
* A bare `YYYY-MM-DD` becomes local noon, which keeps the calendar day stable
  across timezones. Range ends are exclusive.
* `durationUnits` is not a sequential enum. `8 = month` and `4 = year` were
  confirmed by comparing MoneyWiz's own `-nextExecuteDateWithNum:` output;
  unverified values are reported raw, and scheduled transactions also report
  the app-computed upcoming dates.
* Descriptions often contain non-breaking spaces (`Coffee Shop`), so text
  search matches both space variants.
* Parallel to-many key paths (category ids, names and amounts of a split) are
  read from the same set within one main-queue turn, so their element order
  matches; `zip(strict=True)` guards against length drift.

## Testing

* Offline tests cover parsing, formatting and the row → tool-output mapping on
  rows shaped like the agent's.
* `tests/test_live.py` runs full lifecycles against the real app and asserts
  that the ledger's object counts end where they started. It is opt-in
  (`MONEYWIZ_LIVE_TESTS=1`).
