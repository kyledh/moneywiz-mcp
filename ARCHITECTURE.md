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
13. **An edit hands back what it was not given, as stored.** MoneyWiz's edit
   methods set everything they take: `setBudgets:toAccount:` makes exactly
   the budgets given monitor the account (nil takes it out of every budget,
   and a budget left monitoring nothing is deleted), the online-banking
   switches and the cash date are set from the arguments, and the
   symbol and share count relink an expense or income to a forex position or
   holding. So unchanged values are never rebuilt from what a tool shows
   (rounded, signs folded, settings it does not show); they are read from
   the stored row and given back the way MoneyWiz's editors give them: an
   expense's amount and shares negated, an income's or refund's as stored
   (a negative income stays negative), a transfer's amount sent from its
   original amount (its stored amount includes the fee). Each kind's edit has
   a live test that a memo-only edit leaves every stored value as it was.
14. **Send a call once.** A connection that ended between calls is replaced
   before the next one is sent. One lost while MoneyWiz handles a call (it
   quit or crashed) is reported as such: MoneyWiz may have carried the call
   out and saved it, so sending it again could record it twice or crash the
   app a second time. Inside the agent, what a write left unsaved is saved
   before its result is read, so a failure reading the result can neither
   lose the write nor leave it pending.
15. **Reads are questions, writes are calls.** `Native.ask` calls a method
   that only answers (a validator, a computed figure): it runs while the app
   holds unsaved changes, saves nothing and mutes nothing, and the agent
   refuses the answer if the method left anything unsaved, rolling that back
   when nothing else was pending. `Native.invoke` is for writes only.
16. **One tool per kind of change, never a read and a write in one.** Tools
   that do the same change to different objects (groups of accounts, budgets
   and reports; display order; one transaction or many) are one tool with a
   kind argument, so a client loads fewer definitions; a read and a write are
   never merged, because clients ask before writes and warn before deletions
   from the tools' read-only and destructive markers (an offline test checks
   them against each tool's verb). A result goes out once, as compact JSON
   text: FastMCP would send a list as one indented block per item and a
   structured copy as well, about twice the size. List rows carry what tells
   their entries apart and how much each is used, so a question takes one
   call (0.1.x needed one call per category to count its transactions).

## Layers

| Layer | Responsibility |
| --- | --- |
| `agent/src/agent.js` | Domain-free: fetch requests, predicates, key-path reads, typed invocation, context state, save. |
| `native.py` | One Frida session per process, lazy attach, re-attach on the next call after an app restart (a call is sent once), writes (`invoke`) and questions (`ask`), typed-argument helpers, `$date`/`$dec`/`$int` decoding, signature lookup. |
| `values.py` | Amount, date and output formatting shared by all domains. |
| `accounts.py`, `transactions.py`, `catalog.py`, `settings.py`, `investments.py`, `portfolio.py`, `scheduled.py`, `budgets.py`, `reports.py` | Business rules per domain: which selector, in which order, with which guards. |
| `lifecycle.py` | A MoneyWiz the server launched: kept out of the Dock, handed to the user when seen in use, quit when idle or when the last server exits; the lease shared by several servers. |
| `server.py` | MCP tool surface, argument descriptions, read/write annotations, compact JSON results, list rows. |

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
  across timezones. Range ends are exclusive. Every local time is converted
  with the system zone's offset for that moment, never one fixed offset: an
  offset taken today is an hour off on the other side of a daylight-saving
  change, which put day starts on the previous day (0.1.7 and earlier).
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
  A report that is not saved (`compute_report`) is first built in the main
  context by MoneyWiz's typed creator, as its report editor builds one; the
  creator does not calculate (`calculateData:NO`), and the agent copies its
  settings and deletes it again within the same main-queue turn, checking
  that nothing is left unsaved, before calculating the copy as above.
* A backup is made as `MWBackupsManager manageBackups` makes one, without its
  schedule check and pruning: the manager's creator (the iCloud store's
  variant for an iCloud ledger, as `shouldUseICloudBackup` decides) zips the
  database on a background queue and calls a block on the main queue, which
  moves the zip into MoneyWiz's backups folder. What the block uses stays
  retained until it has run, even after a timeout answered the caller.
* Attachments travel as bytes: MoneyWiz is sandboxed and cannot open the
  caller's files, so the agent writes them into MoneyWiz's own temporary
  directory for `addImagesAtPaths:toTransaction:` and removes them after. It
  reads an attachment from MoneyWiz's stored file (`imageAbsolutePath`);
  `imageData` is a decoded UIImage. An edit MoneyWiz performs by deleting
  and recording the transaction again (a trade moved to another account or
  symbol) deletes the stored files first, so the server stages copies of them
  the same way and passes those paths instead.
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
