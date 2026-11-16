# Design

## Rules

1. **Resolve by name, never by address.** Classes, selectors, properties and
   entities are looked up by name in the running app. There are no ivar
   offsets, fixed addresses or binary hash checks, so an app update only breaks
   what actually changed.
2. **Check the type encoding before every call.**
   `src/moneywiz_mcp/signatures.json` holds the encodings recorded from one
   build: every `MoneyWizManager` instance method, and under `classes` every
   other method the server calls and every observer it mutes, keyed as the
   code names the receiver (an entity for `ref()`, a class for `cls()`).
   `native.invoke` looks the expected encoding up for every call (none is
   written in the domain code) and the agent compares it with the live method,
   then checks each argument's tag against the encoded parameter type. A
   changed signature fails loudly instead of pushing misaligned arguments onto
   the stack. `calls.py` finds every call in the source; an offline test fails
   if one is not in the table, and `scripts/check_build.py` compares the table,
   and the fields the server reads, with a running build.
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
   The one exception is a report's calculation, which runs where MoneyWiz runs
   it: on a child context's private queue (below).
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
   the context to clear; an editor left open is still reported. Merging an
   iCloud import calls `delete:` on the main context's copies of the rows it
   removed, so they sit in `deletedObjects` although the store no longer has
   them (checked with a fetch that ignores pending changes), and related
   objects show those members missing from loaded relationships. Neither
   counts: saving them changes nothing. When MoneyWiz follows one of our calls
   with an unsaved change of its own (pricing a new forex position), `settle`
   saves it only if every object a save would write is one the caller names.
   MoneyWiz's purge of year-old autocomplete history (scheduled whenever a
   history item is created, unsaved) is upkeep, not an edit, and does not
   count either.
10. **Silence only the dialogs our own calls raise.** A call may name an app
   observer that does nothing but present UI (the "transfer succeeded" alert,
   or the "delete or keep this completed scheduled transaction?" prompt).
   `native.ALWAYS_QUIET` adds the budget warning alert to every saving call.
   The stand-in records what the observer was told (the notification's
   object, such as MoneyWiz's lists of low and exceeded budgets), and write
   tools return that as `budget_warnings`.
   For that call only, the agent swaps the method's implementation for a
   no-op and puts the original back before the main-queue turn ends; the
   observers run inside the business call, so they are skipped, and the
   app's own calls are untouched. Nothing stays patched between calls: a
   persistent code patch (as in 0.1.1–0.1.2) crashed MoneyWiz when two
   clients were attached and unloaded in the wrong order.
11. **Look after only the MoneyWiz the server started.** `lifecycle.py` hides
   (accessory activation policy) and later quits a MoneyWiz only when the
   lease file says a server launched that very process and nobody has taken it
   over. Being seen in use (a window shown or miniaturized, the app active or
   unhidden) hands it to the user for good. Quitting goes through the app's
   own `terminate:` and is refused while anything is unsaved or MoneyWiz
   reports CloudKit work. The check runs on a thread every 3 s, never launches
   MoneyWiz and does not count as use. The agent schedules `terminate:` with a
   run-loop timer, not a JavaScript callback, so the server can detach before
   it fires: nothing of the agent's runs once it is gone.
12. **Ship only what works.** A business call that cannot be completed reliably
   (for example share exchanges, see STATUS.md) is left out rather than exposed
   with caveats or kept as unused machinery.

## Layers

| Layer | Responsibility |
| --- | --- |
| `agent/src/agent.js` | Domain-free: fetch requests, predicates, key-path reads, typed invocation, context state, save. |
| `native.py` | One Frida session per process, lazy attach, re-attach after an app restart, typed-argument helpers, `$date`/`$dec`/`$int` decoding, signature lookup. |
| `values.py` | Amount, date and output formatting shared by all domains. |
| `accounts.py`, `transactions.py`, `catalog.py`, `settings.py`, `investments.py`, `portfolio.py`, `scheduled.py`, `budgets.py`, `reports.py` | Business rules per domain: which selector, in which order, with which guards. |
| `lifecycle.py` | A MoneyWiz the server launched: kept out of the Dock, handed to the user when seen in use, quit when idle or when the last server exits; the lease shared by several servers. |
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
* Scheduled transactions use NSCalendarUnit values for `durationUnits`, as
  MoneyWiz's repeat editor offers them: `16 = day`, `8192 = week`, `8 = month`,
  `4 = year`; the manager stores a week as 7 days. Weekend handling, the
  "last weekday of the month" ordinals and day names were read from the same
  editor's labels. Unlike the plain transaction methods, the scheduled ones
  store the amount as given, so expenses and transfers are passed negative,
  as the editors pass them.
* MoneyWiz compares an edited schedule's rule and date with the stored ones
  and restarts its history when they differ at all, so unchanged values are
  read raw (`Native.query(raw=True)`, tagged dates as exact seconds) and
  handed back bit for bit.
* Budgets use the same four units (the editor offers day, week, month, year;
  weeks are stored as days and 12 months as a year). `type` 0 is an expense
  budget, 1 an income budget. A one-time budget's end is stored as the start
  of the day after its last day. The tag filter is MoneyWiz's own string
  (`+[TagsPredicate ORPredicateStringFromTagsNames:]`), percent-escaped names
  joined by OR. A repeating budget's amount for an edit applies to a bitmask
  of periods: 4 past, 2 current, 1 future. Scheduled transactions also report
  the upcoming dates MoneyWiz computes (`-nextExecuteDateWithNum:`).
* Some business objects take two calls: a report is created unsaved by a
  typed creator, then kept with `saveCustomReport:withName:groupID:`.
  `Native.batch` runs both in one main-queue turn, the second naming the
  first's result as `prev(0)`, and if a step fails it deletes what the batch
  inserted, so nothing half-made is left for MoneyWiz's next save. Report
  enums were read from MoneyWiz's own label methods and tables, not guessed:
  calling such a helper with a value past its table crashed MoneyWiz once.
* Report figures are computed the way MoneyWiz computes a report it shows
  (`-[MoneyWizManager calculateDataForReportAsync:delegate:]`): the agent
  forks a child of the main context (`forkToChildManagedObjectContext`),
  turns off saving, sync tracking and notifications on that forked manager,
  builds a temporary copy of the saved report there with the 15-argument
  `createReportForUser:…calculateData:YES…`, reads the results on the child's
  queue and resets the child context. The calculation writes scratch state
  into whatever context its report lives in: every account's balance display
  mode and, for a forecast over accounts with fees, investments or
  cross-currency transfers, the future occurrences of every scheduled
  transaction (inserted transactions, advanced schedules, loan installments
  marked paid, category links removed). Run on a report in the main context
  it would leave all of that pending for the next save, so it never runs
  there. The objects a block uses on the other queue are retained explicitly
  (Frida's wrappers do not retain; a freed forked manager crashed MoneyWiz
  once), and the methods the agent calls itself are listed in the module's
  `AGENT_CALLS` and checked against the recorded signatures before it starts.
  Results are read by field lists the Python side names, as for queries.
* Attachments travel as bytes: MoneyWiz is sandboxed and cannot open the
  caller's files, so the agent writes them into MoneyWiz's own temporary
  directory for `addImagesAtPaths:toTransaction:` and removes them after. It
  reads an attachment from MoneyWiz's stored file (`imageAbsolutePath`);
  `imageData` is a decoded UIImage.
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
