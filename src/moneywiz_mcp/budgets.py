"""D07 budgets: read, create, edit, delete, budget groups and transfers between budgets.

Values come from MoneyWiz's budget editor (CreateBudgetViewController and the
main window's delayedCreateBudget:/delayedUpdateBudget:, build 466): what it
offers, what it fills in by default and what it passes to the manager.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import unquote

from .accounts import Accounts
from .catalog import Catalog
from .native import Native, MoneyWizError, s, n, i, d, b, ni, ref, arr, cls, date, NIL, USER, currency_code
from .scheduled import UNITS, UNIT_NAMES
from .settings import Settings
from .values import amount, clean, day_start, find_one, local_time, money, next_day_start, parse_when, show_date, stored, today

KINDS = {"expense": 0, "income": 1}
KIND_NAMES = {v: k for k, v in KINDS.items()}
# Which periods a new amount applies to when a repeating budget is edited
# (SelectBudgetPeriodBalanceVC: "before X", X, "after X"). The editor's
# default is the current period only.
PERIODS = {"past": 4, "current": 2, "future": 1}
BUDGET_GROUP_TYPE = 2  # Group.groupObjectsType of budget groups
# Budgets have no description; the editor passes this constant for it.
DESC = "Desc"

FIELDS = ["name", "type", "currencyName", "startDate", "endDate", "duration", "durationUnits", "isRepeatable",
          "isPeriodMoneyTransferEnabled", "carriedBalance", "openingBalance", "balanceDisplayMode", "displayOrder",
          "groupID", "iconFileName", "tagsPredicateToMonitor", "currentPeriodNumber",
          "accountsLinksToMonitor.account.GID", "accountsLinksToMonitor.account.name",
          "categoriesAssigmentsToMonitor.category.GID", "categoriesAssigmentsToMonitor.category.name"]
GROUP_FIELDS = ["name", "groupId", "displayOrder", "colorNum", "groupCurrency"]

CREATE_ONCE = ("createBudgetForUser:type:name:desc:balance:currencyName:accountsToMonitor:categoriesToMonitor:"
               "tagsNamesBasedPredicate:startDate:endDate:groupID:iconFileName:")
CREATE_REPEATING = ("createRepeatableBudgetForUser:type:name:desc:balance:carriedBalance:currencyName:accountsToMonitor:"
                    "categoriesToMonitor:tagsNamesBasedPredicate:startDate:duration:durationUnits:transferUnusedMoney:"
                    "transferPrevPeriodsMoney:groupID:iconFileName:")
UPDATE_ONCE = ("updateBudget:type:name:desc:balance:balanceDisplayMode:currencyName:accountsToMonitor:"
               "categoriesToMonitor:tagsNamesBasedPredicate:startDate:endDate:groupID:iconFileName:")
UPDATE_REPEATING = ("updateRepeatableBudget:type:name:desc:balance:balancePeriods:carriedBalance:balanceDisplayMode:"
                    "currencyName:accountsToMonitor:categoriesToMonitor:tagsNamesBasedPredicate:startDate:duration:"
                    "durationUnits:transferUnusedMoney:transferPrevPeriodsMoney:groupID:iconFileName:")
DELETE = "deleteBudget:"
TRANSFER = "createTransferTransactionFromBudget:toBudget:description:notes:amount:exchangeRate:fee:date:imagesPaths:tagsNames:"
# Observer of MoneyWiz's main window that only acts on screen: it selects a
# newly created budget in the sidebar. Skipped for the server's own calls (the
# budget warning alert is skipped for every write, see native.ALWAYS_QUIET).
BUDGET_NOTICE = (("iphoneMoneyWizViewController", "onBudgetCreated:"),)


def decode_tags(predicate: str | None):
    """MoneyWiz stores the tag filter as percent-escaped names joined by OR or AND."""
    if not predicate:
        return None, None
    for word, match in ((" OR ", "any"), (" AND ", "all")):
        if word in predicate and (" AND " if word == " OR " else " OR ") not in predicate:
            return [unquote(part) for part in predicate.split(word)], match
    if " OR " not in predicate and " AND " not in predicate:
        return [unquote(predicate)], "any"
    return None, None  # a mixed expression; reported raw


def view(row: dict, current: dict | None = None, paths: dict[str, str] | None = None) -> dict:
    """paths: category id -> full path. Monitored accounts are named (MoneyWiz keeps account
    names unique) and categories given by path, as the tools accept them back."""
    repeats = bool(row.get("isRepeatable"))
    tags, match = decode_tags(row.get("tagsPredicateToMonitor"))
    accounts = list(zip(row.get("accountsLinksToMonitor.account.GID") or [],
                        row.get("accountsLinksToMonitor.account.name") or [], strict=True))
    categories = list(zip(row.get("categoriesAssigmentsToMonitor.category.GID") or [],
                          row.get("categoriesAssigmentsToMonitor.category.name") or [], strict=True))
    end = row.get("endDate")
    return clean({
        "id": row["gid"], "name": row.get("name"), "kind": KIND_NAMES.get(row.get("type"), row.get("type")),
        "currency": row.get("currencyName"),
        "repeats": repeats,
        "repeat": {"every": row.get("duration"), "unit": UNIT_NAMES.get(row.get("durationUnits"), row.get("durationUnits"))}
        if repeats else None,
        "start_date": show_date(row.get("startDate")),
        # MoneyWiz stores the end as the start of the following day.
        "end_date": show_date((datetime.fromisoformat(end) - timedelta(seconds=1)).isoformat()) if end else None,
        "amount": money(row.get("openingBalance")) if not repeats else None,
        "current_period": current,
        "rollover": bool(row.get("isPeriodMoneyTransferEnabled")) if repeats else None,
        "carried_balance": money(row.get("carriedBalance")) if row.get("carriedBalance") else None,
        "group_id": row.get("groupID"),
        "accounts": [nm for _, nm in accounts] or None,
        "categories": [(paths or {}).get(c, nm) for c, nm in categories] or None,
        "tags": tags, "tags_match": match,
        "tag_filter": row.get("tagsPredicateToMonitor") if row.get("tagsPredicateToMonitor") and tags is None else None,
        "icon": row.get("iconFileName"),
        "display_order": row.get("displayOrder"),
    }) | {"repeats": repeats}


class Budgets:
    def __init__(self, native: Native):
        self.n = native
        self.accounts = Accounts(native)
        self.catalog = Catalog(native)
        self.settings = Settings(native)

    # ------------------------------------------------------------ reads

    def _period(self, budget_id: str, index: int) -> dict:
        """The amounts of one period, as MoneyWiz computes them."""
        def call(selector):
            return self.n.ask(ref("Budget", budget_id), selector, [{"i": index}])
        return clean({
            "number": index,
            "budgeted": money(call("openingBalanceForPeriodIndex:")),
            "available": money(call("balanceWithTransfersForPeriodIndex:")),
            "spent": money(call("budgetExpensesForPeriodIndex:")),
            "remaining": money(call("remainingAmountForPeriodIndex:")),
        })

    def _rows(self, where=None) -> list[dict]:
        return self.n.query("Budget", where=where or [], fields=FIELDS, sort=[["displayOrder", True]])["items"]

    def _row(self, budget_id: str) -> dict:
        rows = self._rows([["GID", "eq", s(budget_id)]])
        if not rows:
            raise MoneyWizError(f"Budget not found: {budget_id}")
        return rows[0]

    def _view(self, row: dict, paths: dict[str, str]) -> dict:
        index = row.get("currentPeriodNumber")
        return view(row, self._period(row["gid"], int(index)) if isinstance(index, int) and index >= 0 else None, paths)

    def _paths(self) -> dict[str, str]:
        return {c["id"]: c["path"] for c in self.catalog.categories()}

    def list(self) -> list[dict]:
        paths = self._paths()
        return [self._view(r, paths) for r in self._rows()]

    def get(self, budget_id: str) -> dict:
        return self._view(self._row(budget_id), self._paths())

    def resolve(self, value: str) -> dict:
        rows = self._rows([["GID", "eq", s(value)]]) or self._rows([["name", "eq", s(value)]])
        if len(rows) > 1:
            raise MoneyWizError(f"Several budgets are named {value!r}; use the budget id")
        if not rows:
            raise MoneyWizError(f"Budget not found: {value!r}")
        return rows[0]

    def groups(self) -> list[dict]:
        rows = self.n.query("Group", where=[["groupObjectsType", "eq", ni(BUDGET_GROUP_TYPE)]], fields=GROUP_FIELDS,
                            sort=[["displayOrder", True]])["items"]
        members = {}
        for row in self._rows():
            members.setdefault(row.get("groupID"), []).append(row["gid"])
        return [clean({"id": r["gid"], "group_id": r.get("groupId"), "name": r.get("name"), "color": r.get("colorNum"),
                       "currency": r.get("groupCurrency"), "display_order": r.get("displayOrder"),
                       "budget_ids": members.get(r.get("groupId"))}) for r in rows]

    def group(self, group_ref) -> dict:
        return find_one(self.groups(), group_ref, "budget group")

    # ------------------------------------------------------------ helpers

    def _check_name(self, name):
        # MoneyWiz's own rule for budget names (DataValidatorHelper).
        if not isinstance(name, str):
            raise MoneyWizError("name: text required")
        ok = self.n.ask(cls("DataValidatorHelper"), "validateBudgetName:", [s(name)])
        if not ok:
            raise MoneyWizError(f"MoneyWiz rejects the budget name {name!r}")

    def _tags(self, tags) -> dict:
        if not tags:
            return NIL
        if isinstance(tags, str):
            tags = [tags]
        known = {t["id"]: t["name"] for t in self.catalog.tags()}
        names = [known.get(t, t) for t in tags]
        predicate = self.n.ask(cls("TagsPredicate"), "ORPredicateStringFromTagsNames:", [arr(s(x) for x in names)])
        return s(predicate) if predicate else NIL

    def _accounts(self, accounts) -> dict:
        if accounts is None:
            # The editor's default: every non-investment account.
            rows = self.n.ask("user", "nonInvestmentAccountsArray", []) or []
            return arr(ref("Account", r["gid"]) for r in rows)
        return arr(ref("Account", self.accounts.resolve(a)["id"]) for a in accounts)

    def _categories(self, categories, kind: str | None = None) -> dict:
        if isinstance(categories, str):
            categories = [categories]
        return arr(ref("Category", self.catalog.resolve_category(c)["id"]) for c in categories or [])

    def _group_id(self, group) -> dict:
        if group in (None, "", 0):
            return NIL
        return ni(self.group(group)["group_id"])

    @staticmethod
    def _repeat(repeat: str, every) -> tuple[int, int]:
        units = UNITS.get(repeat)
        if units is None:
            raise MoneyWizError(f"repeat must be none or one of {', '.join(UNITS)}")
        # MoneyWiz steps through periods by this interval; zero or less would never advance.
        if not isinstance(every, int) or every < 1:
            raise MoneyWizError("every must be a whole number of at least 1")
        return every, units

    def _limit_reached(self) -> bool:
        return bool(self.n.ask("manager", "isBudgetsLimitReached", []))

    # ------------------------------------------------------------ writes

    def create(self, *, name: str, amount_, categories, kind: str = "expense", accounts=None, currency: str | None = None,
               start_date: str | None = None, repeat: str = "month", every: int = 1, end_date: str | None = None,
               rollover: bool = False, tags=None, group=None, icon: str | None = None) -> dict:
        if kind not in KINDS:
            raise MoneyWizError("kind must be expense or income")
        # MoneyWiz limits how many budgets a plan may hold and its editor refuses
        # beyond that; the server keeps to the same limit.
        if self._limit_reached():
            raise MoneyWizError("MoneyWiz's budget limit is reached for this subscription; it creates no more budgets")
        self._check_name(name)
        value = amount(amount_, field="amount")
        code = currency_code(self.n, currency or self.settings.currencies().get("default_currency"))
        # The editor's default start: the first day of the current month.
        start = day_start(start_date, field="start_date") if start_date else local_time(today().replace(day=1))
        head = [USER, i(KINDS[kind]), s(name), s(DESC)]
        monitored = [self._accounts(accounts), self._categories(categories), self._tags(tags)]
        if repeat == "none":
            if not end_date:
                raise MoneyWizError("A one-time budget needs end_date")
            end = next_day_start(end_date, field="end_date")  # stored as the start of the next day
            args = head + [n(float(value)), s(code), *monitored, date(start), date(end), self._group_id(group),
                           s(icon) if icon else NIL]
            result = self.n.invoke("manager", CREATE_ONCE, args, quiet=BUDGET_NOTICE)
        else:
            every_, units = self._repeat(repeat, every)
            # transferPrevPeriodsMoney: the editor always passes YES for a new budget.
            args = head + [d(float(value)), d(0.0), s(code), *monitored, date(start), i(every_), i(units),
                           b(bool(rollover)), b(True), self._group_id(group), s(icon) if icon else NIL]
            result = self.n.invoke("manager", CREATE_REPEATING, args, quiet=BUDGET_NOTICE)
        created = result.get("value")
        if not created or not created.get("gid"):
            raise MoneyWizError("MoneyWiz did not create the budget")
        return self.get(created["gid"])

    def update(self, budget_ref: str, **changes) -> dict:
        allowed = {"name", "amount", "apply_to", "kind", "accounts", "categories", "currency", "start_date", "repeat",
                   "every", "end_date", "rollover", "tags", "group", "icon"}
        unknown = set(changes) - allowed
        if unknown:
            raise MoneyWizError(f"Unknown budget fields: {sorted(unknown)}")
        row = self._row(self.resolve(budget_ref)["gid"])
        budget_id = row["gid"]
        if "name" in changes and changes["name"] != row.get("name"):
            self._check_name(changes["name"])
        kind = changes.get("kind", KIND_NAMES.get(row.get("type"), "expense"))
        if kind not in KINDS:
            raise MoneyWizError("kind must be expense or income")
        repeats = bool(row.get("isRepeatable"))
        repeat = changes.get("repeat", UNIT_NAMES.get(row.get("durationUnits")) if repeats else "none")
        head = [ref("Budget", budget_id), i(KINDS[kind]), s(changes.get("name", row.get("name") or "")), s(DESC)]
        code = currency_code(self.n, changes["currency"]) if changes.get("currency") else row.get("currencyName")
        stored_accounts = row.get("accountsLinksToMonitor.account.GID") or []
        stored_categories = row.get("categoriesAssigmentsToMonitor.category.GID") or []
        monitored = [
            self._accounts(changes["accounts"]) if "accounts" in changes else arr(ref("Account", x) for x in stored_accounts),
            self._categories(changes["categories"]) if "categories" in changes
            else arr(ref("Category", x) for x in stored_categories),
            self._tags(changes["tags"]) if "tags" in changes
            else (s(row["tagsPredicateToMonitor"]) if row.get("tagsPredicateToMonitor") else NIL),
        ]
        start = date(day_start(changes["start_date"], field="start_date")) if changes.get("start_date") \
            else {"date": datetime.fromisoformat(row["startDate"]).timestamp()}
        group = self._group_id(changes["group"]) if "group" in changes else \
            (ni(row["groupID"]) if row.get("groupID") not in (None, 0, -1) else NIL)
        icon = s(changes["icon"]) if changes.get("icon") else (s(row["iconFileName"]) if row.get("iconFileName") else NIL)
        display_mode = i(int(row.get("balanceDisplayMode") or 0))

        if repeat == "none":
            if changes.get("end_date"):
                end = date(next_day_start(changes["end_date"], field="end_date"))
            elif row.get("endDate"):
                end = {"date": datetime.fromisoformat(row["endDate"]).timestamp()}
            else:
                raise MoneyWizError("Making a budget one-time needs end_date")
            value = amount(changes["amount"], field="amount") if "amount" in changes else row.get("openingBalance") or 0
            args = head + [n(float(value)), display_mode, s(code), *monitored, start, end, group, icon]
            self.n.invoke("manager", UPDATE_ONCE, args, quiet=BUDGET_NOTICE)
        else:
            every_, units = self._repeat(repeat, changes.get("every", row.get("duration") or 1))
            if "amount" in changes:
                value = float(amount(changes["amount"], field="amount"))
                periods = changes.get("apply_to") or ["current"]
                if isinstance(periods, str):
                    periods = [periods]
                if any(p not in PERIODS for p in periods):
                    raise MoneyWizError(f"apply_to takes {', '.join(PERIODS)}")
                mask = sum(PERIODS[p] for p in set(periods))
            else:
                # The current period's own amount, applied to it alone, changes nothing.
                index = row.get("currentPeriodNumber") or 0
                stored_amount = self.n.ask(ref("Budget", budget_id), "openingBalanceForPeriodIndex:", [{"i": index}])
                value, mask = float(stored_amount or 0), PERIODS["current"]
            rollover = bool(changes.get("rollover", row.get("isPeriodMoneyTransferEnabled")))
            # The editor passes its rollover switch for both transfer flags when editing.
            args = head + [d(value), i(mask), d(float(row.get("carriedBalance") or 0)), display_mode, s(code), *monitored,
                           start, i(every_), i(units), b(rollover), b(rollover), group, icon]
            self.n.invoke("manager", UPDATE_REPEATING, args, quiet=BUDGET_NOTICE)
        return self.get(budget_id)

    def _orders(self) -> dict:
        return {r["gid"]: (r.get("groupID"), r.get("displayOrder"))
                for r in self.n.query("Budget", fields=["groupID", "displayOrder"])["items"]}

    def _place(self, entity: str, ordered_ids: list[str], notification: str):
        """Number objects 0..n in this order with the call MoneyWiz's own reorder
        uses (updateBudget:withDisplayOrder:andGroup: and normilizeBudgetsDisplayOrders)."""
        siblings = arr(ref(entity, x) for x in ordered_ids)
        for index, object_id in enumerate(ordered_ids):
            self.n.invoke("manager", "updateObject:withDisplayOrderIndex:siblings:notificationName:user:",
                          [ref(entity, object_id), i(index), siblings, s(notification), USER])

    def reorder(self, ordered: list[str], group=None) -> list[dict]:
        """Set the order of the budgets in one group (or the ungrouped ones)."""
        group_id = self.group(group)["group_id"] if group not in (None, "") else None
        def member(row):
            stored = row.get("groupID")
            return stored == group_id if group_id is not None else stored in (None, 0, -1)
        members = [r["gid"] for r in self._rows() if member(r)]
        ids = [self.resolve(x)["gid"] for x in ordered]
        if sorted(ids) != sorted(members):
            raise MoneyWizError("Give every budget of that group exactly once (see moneywiz_list_groups, kind budget)")
        self._place("Budget", ids, "Budget display order changed")
        return [self.get(x) for x in ids]

    def reorder_groups(self, ordered: list[str]) -> list[dict]:
        ids = [self.group(x)["id"] for x in ordered]
        if sorted(ids) != sorted(g["id"] for g in self.groups()):
            raise MoneyWizError("Give every budget group exactly once")
        self._place("Group", ids, "Group changed")
        return self.groups()

    def delete(self, budget_ref: str) -> dict:
        row = self.resolve(budget_ref)
        before = self._orders()
        self.n.invoke("manager", DELETE, [ref("Budget", row["gid"])], quiet=BUDGET_NOTICE)
        if self.n.get("Budget", row["gid"]):
            raise MoneyWizError("The budget still exists after delete")
        # deleteBudget: lowers the display order of every budget above the deleted
        # one, in every group. Only its own group should close the gap; the other
        # groups are put back in their previous order with MoneyWiz's reorder call.
        after, deleted_group = self._orders(), before[row["gid"]][0]
        restored = 0
        for group_id in {g for g, _ in before.values() if g != deleted_group}:
            members = sorted(((o if o is not None else -1), gid) for gid, (g, o) in before.items()
                             if g == group_id and gid in after)
            if all(after[gid][1] == o for o, gid in members):
                continue
            self._place("Budget", [gid for _, gid in members], "Budget display order changed")
            restored += len(members)
        return clean({"deleted": row["gid"], "name": row.get("name"), "reordered_back": restored or None})

    def transfer(self, *, from_budget: str, to_budget: str, amount_, date_: str, description: str = "",
                 memo: str = "", exchange_rate=None) -> dict:
        """Move money between two budgets, as MoneyWiz's budget transfer does."""
        source, target = self.resolve(from_budget), self.resolve(to_budget)
        if source["gid"] == target["gid"]:
            raise MoneyWizError("Choose two different budgets")
        value = amount(amount_, field="amount")
        if exchange_rate is not None:
            rate = amount(exchange_rate, field="exchange_rate")
        elif source.get("currencyName") == target.get("currencyName"):
            rate = 1
        else:
            raise MoneyWizError(f"{source.get('currencyName')}→{target.get('currencyName')} needs exchange_rate")
        args = [ref("Budget", source["gid"]), ref("Budget", target["gid"]), s(description or ""), s(memo or ""),
                n(float(value)), n(float(rate)), n(0.0), date(parse_when(date_)), NIL, NIL]
        result = self.n.invoke("manager", TRANSFER, args, quiet=BUDGET_NOTICE)
        created = result.get("value")
        if not created or not created.get("gid"):
            raise MoneyWizError("MoneyWiz did not record the budget transfer")
        row = self.n.get("Transaction", created["gid"], ["amount", "date", "desc"])
        return {"transfer_id": created["gid"], "amount": stored(row.get("amount")) if row else None,
                "date": show_date(row.get("date")) if row else None,
                "from": self.get(source["gid"]), "to": self.get(target["gid"])}

    # ------------------------------------------------------------ groups

    def create_group(self, *, name: str, color: int = 1, budget_ids: list[str] | None = None) -> dict:
        budgets = arr(ref("Budget", self.resolve(x)["gid"]) for x in budget_ids or [])
        result = self.n.invoke("manager", "createBudgetsGroupForUser:name:colorNum:budgets:",
                               [USER, s(name), i(color), budgets], quiet=BUDGET_NOTICE)["value"]
        if not result or not result.get("gid"):
            raise MoneyWizError("MoneyWiz did not create the budget group")
        return self.group(result["gid"])

    def update_group(self, group_ref, *, name: str | None = None, color: int | None = None,
                     budget_ids: list[str] | None = None) -> dict:
        group = self.group(group_ref)
        members = budget_ids if budget_ids is not None else group.get("budget_ids") or []
        budgets = arr(ref("Budget", self.resolve(x)["gid"]) for x in members)
        self.n.invoke("manager", "updateBudgetsGroup:name:colorNum:budgets:",
                      [ref("Group", group["id"]), s(name or group["name"]), i(group.get("color", 1) if color is None else color),
                       budgets], quiet=BUDGET_NOTICE)
        return self.group(group["id"])

    def delete_group(self, group_ref) -> dict:
        group = self.group(group_ref)
        self.n.invoke("manager", "deleteBudgetGroup:", [ref("Group", group["id"])], quiet=BUDGET_NOTICE)
        if self.n.get("Group", group["id"]):
            raise MoneyWizError("The budget group still exists after delete")
        return {"deleted": group["id"], "name": group["name"], "budget_ids": group.get("budget_ids")}
