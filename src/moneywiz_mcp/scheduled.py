"""D08 scheduled transactions (bills): read, create, edit, delete, skip and execute.

The values below come from MoneyWiz's own repeat editor (TransactionRepeatField,
build 466). Unlike the plain transaction methods, the scheduled-transaction
methods store the amount as given, so expenses and transfers are passed
negative, exactly as MoneyWiz's editors pass them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .accounts import Accounts
from .native import Native, MoneyWizError, s, b, i, d, n, ref, arr, date, NIL
from .transactions import Transactions, TRANSFER_NOTICE
from .values import amount, parse_when, show_date, clean, stored

KINDS = {"ScheduledWithdrawTransactionHandler": "expense",
         "ScheduledDepositTransactionHandler": "income",
         "ScheduledTransferTransactionHandler": "transfer"}
# durationUnits are NSCalendarUnit values; the editor offers these four. The
# manager stores a week as 7 days (unit day, duration x 7).
UNITS = {"day": 16, "week": 8192, "month": 8, "year": 4}
UNIT_NAMES = {v: k for k, v in UNITS.items()}
# Weekend handling (LBL_NO_CHANGE, LBL_BEFORE, LBL_AFTER, BTN_SKIP_SCHEDULED_TRANSACTION).
WEEKENDS = {"no_change": 0, "before": 1, "after": 2, "skip": 3}
# "On the <week_of_month> <day_of_week>" rules, e.g. the last weekday of each month.
WEEK_OF_MONTH = {"none": 0, "first": 1, "second": 2, "third": 3, "fourth": 4,
                 "last": 1001, "second_to_last": 1002, "third_to_last": 1003}
DAY_OF_WEEK = {"none": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6,
               "sunday": 7, "weekday": 8, "weekend_day": 9, "calendar_day": 10}
NAMES = {"weekends": {v: k for k, v in WEEKENDS.items()},
         "week_of_month": {v: k for k, v in WEEK_OF_MONTH.items()},
         "day_of_week": {v: k for k, v in DAY_OF_WEEK.items()}}

FIELDS = ["desc", "notes", "amount", "fee", "currencyName", "executeDate", "firstWaitingExecuteDate",
          "firstExecuteDate", "lastExecuteDate", "installments", "isRepeatable", "duration", "durationUnits",
          "weekendsHandler", "weekdayNumber", "weekdayName", "autoPay", "disableExecution", "executesCount",
          "waitingExecutesCount", "account.GID", "account.name", "account.currencyName", "payee.name", "tags.name",
          "categoriesAssigments.category.GID", "categoriesAssigments.category.name", "categoriesAssigments.amount",
          "recipientAccount.GID", "recipientAccount.name", "paymentPlan.GID", "loanAccount.GID", "loanAccount.name"]

_TAIL = ("isRepeatable:executeDate:duration:durationUnits:weekendsHandler:weekdayNumber:weekdayName:installments:"
        "desc:payeeName:categories:categoriesMoney:amount:")
CREATE = {"expense": "createScheduledWithdrawTransactionFor:" + _TAIL + "fee:currencyName:autoPay:notes:tagsNames:",
          "income": "createScheduledDepositTransactionFor:" + _TAIL + "fee:currencyName:autoPay:notes:tagsNames:",
          "transfer": "createScheduledTransferTransactionForAccount:recipientAccount:" + _TAIL +
                      "currencyName:fee:autoPay:notes:tagsNames:"}
UPDATE = {"expense": "updateScheduledWithdrawTransaction:account:" + _TAIL + "fee:currencyName:autoPay:notes:tagsNames:",
          "income": "updateScheduledDepositTransaction:account:" + _TAIL + "fee:currencyName:autoPay:notes:tagsNames:",
          "transfer": "updateScheduledTransferTransaction:account:recipientAccount:" + _TAIL +
                      "currencyName:fee:autoPay:notes:tagsNames:"}
EXECUTE = "executeScheduledTransaction:overrideAmount:checkTransactionsDuplicates:"
# When a schedule completes (a one-off has run, a series has recorded its last
# occurrence), this observer of MoneyWiz's main window puts up a "delete or
# keep this scheduled transaction?" alert a moment later, and does nothing
# else. It runs inside the business call, so it is skipped for the server's
# own calls; the schedule is kept and reported as finished.
SCHEDULE_NOTICE = (("iphoneMoneyWizViewController", "onScheduledWaitToExecuteCountChanged:"),)
SKIP = "skipScheduleTransaction:"
DELETE = "deleteScheduledTransactionHandler:"


def _iso(value) -> str | None:
    """Raw agent date ({"$date": seconds}) to the local display form."""
    if not value:
        return None
    return show_date(datetime.fromtimestamp(value["$date"], timezone.utc).isoformat())


def view(row: dict, finished: bool = False) -> dict:
    """Map one raw row (tagged dates) to tool output. finished is MoneyWiz's
    own verdict (ScheduledTransactionDataManager isTransactionCompleted:)."""
    kind = KINDS.get(row["$ref"], row["$ref"])
    repeats = bool(row.get("isRepeatable"))
    categories = list(zip(row.get("categoriesAssigments.category.GID") or [],
                          row.get("categoriesAssigments.category.name") or [],
                          row.get("categoriesAssigments.amount") or [], strict=True))
    week, day = row.get("weekdayNumber"), row.get("weekdayName")
    out = {
        "id": row["gid"], "kind": kind,
        "description": row.get("desc"), "memo": row.get("notes") or None,
        "amount": stored(row.get("amount")), "currency": row.get("currencyName"),
        "account_id": row.get("account.GID"), "account": row.get("account.name"),
        "transfer_to_id": row.get("recipientAccount.GID"), "transfer_to": row.get("recipientAccount.name"),
        # The next occurrence not yet recorded or skipped; executeDate runs ahead of it when one is overdue.
        "next_due": None if finished else _iso(row.get("firstWaitingExecuteDate") or row.get("executeDate")),
        "due_now": row.get("waitingExecutesCount") or None,
        "first_date": _iso(row.get("firstExecuteDate")),
        "repeats": repeats,
        "repeat": {"every": row.get("duration"), "unit": UNIT_NAMES.get(row.get("durationUnits"), row.get("durationUnits"))}
        if repeats else None,
        "on": {"week_of_month": NAMES["week_of_month"].get(week, week),
               "day_of_week": NAMES["day_of_week"].get(day, day)} if repeats and (week or day) else None,
        "weekends": NAMES["weekends"].get(row.get("weekendsHandler"), row.get("weekendsHandler"))
        if row.get("weekendsHandler") else None,
        "occurrences": row.get("installments") or None,
        "end_date": _iso(row.get("lastExecuteDate")),
        "auto_pay": bool(row.get("autoPay")) or None,
        "finished": finished or None,
        "executions": row.get("executesCount"),
        "payee": row.get("payee.name"), "tags": row.get("tags.name") or None,
        "categories": [{"id": cid, "name": name, "amount": stored(value)} for cid, name, value in categories] or None,
        "loan": row.get("loanAccount.name"),
    }
    return clean(out) | {"repeats": repeats}


def _choice(table: dict, value, field: str) -> int:
    if value not in table:
        raise MoneyWizError(f"{field} must be one of {', '.join(table)}")
    return table[value]


class Scheduled:
    def __init__(self, native: Native):
        self.n = native
        self.accounts = Accounts(native)
        self.tx = Transactions(native)

    # ------------------------------------------------------------ reads

    def _rows(self, where=None) -> list[dict]:
        return self.n.query("ScheduledTransactionHandler", where=where or [], fields=FIELDS,
                            sort=[["executeDate", True]], raw=True)["items"]

    def _row(self, scheduled_id: str) -> dict:
        rows = self._rows([["GID", "eq", s(scheduled_id)]])
        if not rows:
            raise MoneyWizError(f"Scheduled transaction not found: {scheduled_id}")
        return rows[0]

    def upcoming(self, scheduled_id: str, count: int = 3, end: float | None = None) -> list[str]:
        """Ask MoneyWiz itself for the next execution dates (it does not stop at
        the series' end, so dates after `end` are dropped)."""
        dates = []
        for index in range(1, count + 1):
            value = self.n.invoke(ref("ScheduledTransactionHandler", scheduled_id), "nextExecuteDateWithNum:",
                                  [{"i": index}], encoding="@24@0:8q16", save=False)["value"]
            if not value or (end is not None and datetime.fromisoformat(value).timestamp() > end):
                break
            dates.append(show_date(value))
        return dates

    def _finished(self, scheduled_id: str) -> bool:
        """MoneyWiz's own test for a one-off that has run or a series past its
        last occurrence. Its execute method does not check this and would
        record the last occurrence again."""
        return bool(self.n.invoke({"cls": "ScheduledTransactionDataManager"}, "isTransactionCompleted:",
                                  [ref("ScheduledTransactionHandler", scheduled_id)], encoding="B24@0:8@16",
                                  save=False)["value"])

    def _view(self, row: dict) -> dict:
        out = view(row, self._finished(row["gid"]))
        if out["repeats"] and not out.get("finished"):
            end = row.get("lastExecuteDate")
            out["upcoming_dates"] = self.upcoming(row["gid"], end=end["$date"] if end else None)
        return out

    def list(self, *, account: str | None = None) -> list[dict]:
        where = [["account.GID", "eq", s(self.accounts.resolve(account)["id"])]] if account else []
        return [self._view(row) for row in self._rows(where)]

    def get(self, scheduled_id: str) -> dict:
        return self._view(self._row(scheduled_id))

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _schedule(repeat: str, every, occurrences, weekends: str, week_of_month, day_of_week) -> list[dict]:
        """isRepeatable … installments, as the editor passes them."""
        if repeat == "none":
            repeats, units, every, occurrences = False, UNITS["month"], 1, 0
        else:
            repeats, units = True, _choice(UNITS, repeat, "repeat")
            # MoneyWiz steps through dates by this interval; zero or less would never advance.
            if not isinstance(every, int) or every < 1:
                raise MoneyWizError("every must be a whole number of at least 1")
            if occurrences is None:
                occurrences = 0  # no end
            elif not isinstance(occurrences, int) or occurrences < 1:
                raise MoneyWizError("occurrences must be a whole number of at least 1, or left out for no end")
        return [b(repeats), every, units, _choice(WEEKENDS, weekends, "weekends"),
                0 if week_of_month is None else _choice(WEEK_OF_MONTH, week_of_month, "week_of_month"),
                0 if day_of_week is None else _choice(DAY_OF_WEEK, day_of_week, "day_of_week"), occurrences]

    def _categories(self, categories, value: Decimal, sign: int):
        if not categories:
            return arr([]), arr([])
        _, _, resolved = self.tx._splits(categories, value)
        return (arr(ref("Category", c["id"]) for c in resolved),
                arr(n(sign * float(c["amount"])) for c in resolved))

    def _tags(self, tags) -> dict:
        value = self.tx._tags(tags)
        return arr([]) if value is NIL else value

    def _args(self, kind: str, head: list, when: dict, schedule: list, description: str, payee, cats, monies,
              signed: float, fee: float, currency: str, auto_pay: bool, memo: str, tags) -> list:
        repeats, every, units, weekends, week, day, occurrences = schedule
        middle = [repeats, when, i(every), i(units), i(weekends), i(week), i(day), i(occurrences),
                  s(description or ""), self.tx._payee(payee or None), cats, monies, d(signed)]
        # The transfer method takes currencyName before fee; the others after.
        money = [s(currency), d(fee)] if kind == "transfer" else [d(fee), s(currency)]
        return head + middle + money + [b(bool(auto_pay)), s(memo or ""), self._tags(tags)]

    def _created(self, result) -> dict:
        value = result.get("value")
        if not value or not value.get("gid"):
            raise MoneyWizError("MoneyWiz did not create the scheduled transaction")
        return self.get(value["gid"])

    @staticmethod
    def _loan_guard(row: dict, action: str):
        # A loan's payment schedule is generated from its payment plan; MoneyWiz
        # rebuilds it from the loan, and changing it alone would leave the plan
        # out of step with its payments.
        if row.get("paymentPlan.GID") or row.get("loanAccount.GID"):
            raise MoneyWizError(f"This is the payment schedule of the loan {row.get('loanAccount.name')!r}; "
                                f"MoneyWiz maintains it from the loan, so it cannot be {action} here")

    # ------------------------------------------------------------ writes

    def create(self, *, kind: str, account: str, amount_, date_: str, repeat: str = "month", every: int = 1,
               occurrences: int | None = None, weekends: str = "no_change", week_of_month: str | None = None,
               day_of_week: str | None = None, description: str = "", memo: str = "", payee: str | None = None,
               categories=None, tags=None, auto_pay: bool = False, transfer_to: str | None = None) -> dict:
        if kind not in CREATE:
            raise MoneyWizError("kind must be expense, income or transfer")
        source = self.accounts.resolve(account)
        value = amount(amount_, field="amount")
        when = date(parse_when(date_))
        schedule = self._schedule(repeat, every, occurrences, weekends, week_of_month, day_of_week)
        sign = 1 if kind == "income" else -1
        head = [ref("Account", source["id"])]
        if kind == "transfer":
            if not transfer_to:
                raise MoneyWizError("A scheduled transfer needs transfer_to: the destination account")
            target = self.accounts.resolve(transfer_to)
            if target["id"] == source["id"]:
                raise MoneyWizError("Transfer source and destination must differ")
            head.append(ref("Account", target["id"]))
            if categories:
                raise MoneyWizError("Categories on scheduled transfers are not supported here")
        cats, monies = self._categories(categories, value, sign)
        args = self._args(kind, head, when, schedule, description, payee, cats, monies, sign * float(value), 0.0,
                          source["currency"], auto_pay, memo, tags)
        return self._created(self.n.invoke("manager", CREATE[kind], args, quiet=SCHEDULE_NOTICE))

    def update(self, scheduled_id: str, **changes) -> dict:
        row = self._row(scheduled_id)
        self._loan_guard(row, "edited")
        kind = KINDS.get(row["$ref"])
        if kind is None:
            raise MoneyWizError(f"Unsupported scheduled transaction type {row['$ref']}")
        allowed = {"account", "amount", "date", "repeat", "every", "occurrences", "weekends", "week_of_month",
                   "day_of_week", "description", "memo", "payee", "categories", "tags", "auto_pay", "transfer_to"}
        unknown = set(changes) - allowed
        if unknown:
            raise MoneyWizError(f"Unknown scheduled transaction fields: {sorted(unknown)}")
        current = view(row)
        account = self.accounts.resolve(changes["account"]) if changes.get("account") else \
            self.accounts.resolve(row["account.GID"])
        sign = 1 if kind == "income" else -1

        # Unchanged values go back exactly as stored: MoneyWiz restarts the
        # schedule's history when the rule, or the date compared with the
        # next due date, differs at all.
        if "amount" in changes:
            value = amount(changes["amount"], field="amount")
            signed = sign * float(value)
        else:
            signed = float(row.get("amount") or 0)
            value = abs(Decimal(repr(signed)))
        if "date" in changes:
            when = date(parse_when(changes["date"]))
        else:
            when = {"date": (row.get("firstWaitingExecuteDate") or row["executeDate"])["$date"]}
        rule = {"repeat", "every", "occurrences", "weekends", "week_of_month", "day_of_week"}
        if rule & set(changes):
            repeats = bool(row.get("isRepeatable"))
            unit = UNIT_NAMES.get(row.get("durationUnits"))
            merged = {
                "repeat": (unit if repeats else "none") if unit or not repeats else None,
                "every": row.get("duration") or 1, "occurrences": row.get("installments") or None,
                "weekends": NAMES["weekends"].get(row.get("weekendsHandler") or 0),
                "week_of_month": NAMES["week_of_month"].get(row.get("weekdayNumber")),
                "day_of_week": NAMES["day_of_week"].get(row.get("weekdayName")),
            } | {k: changes[k] for k in rule & set(changes)}
            if merged["repeat"] is None:
                raise MoneyWizError(f"Unknown stored repeat unit {row.get('durationUnits')}; give repeat explicitly")
            schedule = self._schedule(merged["repeat"], merged["every"], merged["occurrences"], merged["weekends"],
                                      merged["week_of_month"], merged["day_of_week"])
        else:
            schedule = [b(bool(row.get("isRepeatable"))), row.get("duration") or 0, row.get("durationUnits") or 0,
                        row.get("weekendsHandler") or 0, row.get("weekdayNumber") or 0, row.get("weekdayName") or 0,
                        row.get("installments") or 0]

        if "categories" in changes:
            if kind == "transfer" and changes["categories"]:
                raise MoneyWizError("Categories on scheduled transfers are not supported here")
            cats, monies = self._categories(changes["categories"], value, sign)
        else:
            ids = row.get("categoriesAssigments.category.GID") or []
            stored_amounts = row.get("categoriesAssigments.amount") or []
            if len(ids) == 1 and "amount" in changes:
                stored_amounts = [signed]  # a single category follows the amount, as in the app
            cats = arr(ref("Category", cid) for cid in ids)
            monies = arr(n(float(x)) for x in stored_amounts)

        head = [ref("ScheduledTransactionHandler", scheduled_id), ref("Account", account["id"])]
        if kind == "transfer":
            target_id = self.accounts.resolve(changes["transfer_to"])["id"] if changes.get("transfer_to") \
                else row.get("recipientAccount.GID")
            if not target_id:
                raise MoneyWizError("This scheduled transfer has no destination account")
            if target_id == account["id"]:
                raise MoneyWizError("Transfer source and destination must differ")
            head.append(ref("Account", target_id))
        currency = account["currency"] if changes.get("account") else (row.get("currencyName") or account["currency"])
        args = self._args(kind, head, when, schedule, changes.get("description", current.get("description") or ""),
                          changes.get("payee", current.get("payee")), cats, monies, signed,
                          float(row.get("fee") or 0), currency,
                          changes.get("auto_pay", bool(row.get("autoPay"))),
                          changes.get("memo", current.get("memo") or ""), changes.get("tags", current.get("tags")))
        self.n.invoke("manager", UPDATE[kind], args, quiet=SCHEDULE_NOTICE)
        return self.get(scheduled_id)

    def delete(self, scheduled_id: str) -> dict:
        row = self._row(scheduled_id)
        self._loan_guard(row, "deleted")
        current = view(row)
        self.n.invoke("manager", DELETE, [ref("ScheduledTransactionHandler", scheduled_id)], quiet=SCHEDULE_NOTICE)
        if self.n.get("ScheduledTransactionHandler", scheduled_id):
            raise MoneyWizError("The scheduled transaction still exists after delete")
        return {"deleted": scheduled_id, "kind": current["kind"], "description": current.get("description"),
                "amount": current.get("amount"), "account": current.get("account")}

    def _open(self, scheduled_id: str, action: str) -> dict:
        row = self._row(scheduled_id)
        if self._finished(scheduled_id):
            raise MoneyWizError(f"This scheduled transaction is complete; MoneyWiz has nothing left to {action}")
        return row

    def skip(self, scheduled_id: str) -> dict:
        """Skip the next due occurrence without recording a transaction."""
        before = view(self._open(scheduled_id, "skip"))
        self.n.invoke("manager", SKIP, [ref("ScheduledTransactionHandler", scheduled_id)], quiet=SCHEDULE_NOTICE)
        return {"skipped": before.get("next_due"), "scheduled": self.get(scheduled_id)}

    def execute(self, scheduled_id: str, amount_=None) -> dict:
        """Record the next due occurrence now, as MoneyWiz does when it pays one."""
        row = self._open(scheduled_id, "execute")
        before = view(row)
        override = NIL
        if amount_ is not None:
            # Same direction as the stored amount (negative for expenses and transfers).
            sign = -1 if float(row.get("amount") or 0) < 0 else 1
            override = n(sign * float(amount(amount_, field="amount")))
        # Executing a scheduled transfer creates a transfer, which raises the transfer notice too.
        quiet = SCHEDULE_NOTICE + (TRANSFER_NOTICE if before["kind"] == "transfer" else ())
        result = self.n.invoke("manager", EXECUTE, [ref("ScheduledTransactionHandler", scheduled_id), override, b(True)],
                               quiet=quiet)
        value = result.get("value")
        if not value or not value.get("gid"):
            raise MoneyWizError("MoneyWiz did not record a transaction (it may already exist for this date)")
        return {"transaction": self.tx.get(value["gid"]), "occurrence": before.get("next_due"),
                "scheduled": self.get(scheduled_id) if self.n.get("ScheduledTransactionHandler", scheduled_id) else None}
