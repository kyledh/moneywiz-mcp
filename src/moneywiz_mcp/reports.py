"""Saved reports in MoneyWiz's Reports section, and their groups.

MoneyWiz builds a report with one of its typed creators (createXReportForUser:...),
which leaves it unsaved while it is shown, and keeps it with
saveCustomReport:withName:groupID:. The server does both in one main-queue turn
(Native.batch), so no unsaved report is left behind. The enums were read from
MoneyWiz itself (build 466): report-type names from
+[CustomReport reportNameForPredefinedReportTypeUnlocalized:...], periods from
+[DateHelper reportTimePeriodString:], chart types and breakdown periods from
the report editor's labels (CreateCustomReportViewController).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .accounts import Accounts
from .budgets import Budgets
from .catalog import Catalog
from .native import Native, MoneyWizError, s, i, b, ni, ref, arr, date, NIL, USER, prev, recorded_encoding
from .values import clean, local_tz, money, parse_when, show_date

# Report kinds the server creates, by MoneyWiz's typed creator.
_TIME = "reportTimePeriod:fromDate:toDate:includeScheduledTransactions:"
KINDS = {
    "balance_over_time": "createAccountBalanceReportForUser:accounts:" + _TIME + "includeArchivedAccounts:",
    "forecast": "createForecastReportForUser:accounts:" + _TIME + "includeArchivedAccounts:",
    "net_worth": "createNetworthReportForUser:" + _TIME + "includeArchivedAccounts:",
    "cashflow": "createTrendsReportForUser:accounts:breakDownPeriod:" + _TIME + "includeArchivedAccounts:",
    "statistics": "createStatisticsReportForUser:accounts:" + _TIME + "includeArchivedAccounts:",
    "budget_balance": "createBudgetBalanceReportForUser:budget:" + _TIME,
    "compare_budgets": "createCompareBudgetsReportForUser:budgets:" + _TIME,
    "categories": "createCompareCategoriesReportForUser:isExpenses:categories:additionalFilters:chartType:" + _TIME
                  + "includeArchivedAccounts:",
    "payees": "createComparePayeesReportForUser:isExpenses:payees:additionalFilters:chartType:" + _TIME
              + "includeArchivedAccounts:",
    "tags": "createCompareTagsReportForUser:isExpenses:tags:additionalFilters:chartType:" + _TIME
            + "includeArchivedAccounts:",
    "categories_drilldown": "createCategoriesDrilldownReportForUser:isExpenses:categories:additionalFilters:" + _TIME
                            + "includeArchivedAccounts:",
    "categories_over_time": "createCategoriesOverTimeReportForUser:isExpenses:categories:additionalFilters:"
                            "breakDownPeriod:" + _TIME + "includeArchivedAccounts:",
    "payees_over_time": "createPayeesOverTimeReportForUser:isExpenses:payees:additionalFilters:breakDownPeriod:" + _TIME
                        + "includeArchivedAccounts:",
    "tags_over_time": "createTagsOverTimeReportForUser:isExpenses:tags:additionalFilters:breakDownPeriod:" + _TIME
                      + "includeArchivedAccounts:",
    "profit_and_loss": "createProfitAndLossReportForUser:expenseCategories:incomeCategories:additionalFilters:"
                       "breakdownPeriod:" + _TIME + "includeArchivedAccounts:",
    "summary_breakdown": "createSummaryBreakdownReportForUser:accounts:asOfDate:includeScheduledTransactions:"
                         "includeArchivedAccounts:",
    "summary_breakdown_over_time": "createSummaryBreakdownOverTimeSpreadSheetReportForUser:accounts:breakDownPeriod:"
                                   + _TIME + "includeArchivedAccounts:",
    "categories_over_time_table": "createCategoriesOverTimeSpreadsheetReportForUser:isExpenses:categories:"
                                  "additionalFilters:breakDownPeriod:" + _TIME + "includeArchivedAccounts:",
    "payees_over_time_table": "createPayeesOverTimeSpreadsheetReportForUser:isExpenses:payees:additionalFilters:"
                              "breakDownPeriod:" + _TIME + "includeArchivedAccounts:",
    "tags_over_time_table": "createTagsOverTimeSpreadsheetReportForUser:isExpenses:tags:additionalFilters:"
                            "breakDownPeriod:" + _TIME + "includeArchivedAccounts:",
    "payees_by_category_table": "createPayeesOverCategoriesSpreadsheetReportForUser:isExpenses:payees:categories:"
                                "additionalFilters:" + _TIME + "includeArchivedAccounts:",
    "categories_by_tag_table": "createCategoriesOverTagsSpreadsheetReportForUser:isExpenses:categories:tags:"
                               "additionalFilters:" + _TIME + "includeArchivedAccounts:",
    "payees_by_tag_table": "createPayeesOverTagsSpreadsheetReportForUser:isExpenses:payees:tags:additionalFilters:"
                           + _TIME + "includeArchivedAccounts:",
}
# Not offered: createSpreadSheetReportForUser:accounts:categories:tagsNames:... returns nil on build 466
# whatever it is given.
SAVE = "saveCustomReport:withName:groupID:"
DELETE = "deleteCustomReport:"
REORDER = "updateObject:withDisplayOrderIndex:siblings:notificationName:user:"

# +[DateHelper reportTimePeriodString:] for 0..11.
PERIODS = {"this_month": 0, "last_month": 1, "last_3_months": 2, "last_6_months": 3, "last_12_months": 4,
           "this_year": 5, "custom": 6, "all_time": 7, "next_month": 8, "next_3_months": 9, "next_6_months": 10,
           "next_12_months": 11}
PERIOD_NAMES = {v: k for k, v in PERIODS.items()} | {14: "as_of_date"}  # 14: what a summary breakdown stores
# The report editor's breakdown periods (NSCalendarUnit values, as for budgets).
BREAKDOWNS = {"day": 16, "week": 8192, "month": 8, "quarter": 2048, "year": 4}
BREAKDOWN_NAMES = {v: k for k, v in BREAKDOWNS.items()}
# The report editor's chart types (3 is unused).
CHARTS = {"none": 0, "line": 1, "bar": 2, "pie": 4}
CHART_NAMES = {v: k for k, v in CHARTS.items()}
# predefinedReportType as each creator stores it (sandbox-verified), and MoneyWiz's own name for it
# (+[CustomReport reportNameForPredefinedReportTypeUnlocalized:...]; it has none worth showing for 16..21).
TYPE_KINDS = {1: "balance_over_time", 2: "forecast", 3: "net_worth", 4: "cashflow", 5: "budget_balance",
              6: "compare_budgets", 7: "categories", 8: "payees", 9: "statistics", 10: "categories_drilldown",
              11: "categories_over_time", 12: "payees_over_time", 13: "profit_and_loss", 14: "tags",
              15: "tags_over_time", 16: "categories_over_time_table", 17: "payees_over_time_table",
              18: "tags_over_time_table", 19: "payees_by_category_table", 20: "categories_by_tag_table",
              21: "payees_by_tag_table", 22: "summary_breakdown", 23: "summary_breakdown_over_time"}
TYPE_NAMES = {1: "Balance over time", 2: "Forecast", 3: "Networth", 4: "Cashflow", 5: "Budget balance",
              6: "Compare budgets", 7: "Categories", 8: "Payees", 9: "Statistics", 10: "Categories Drill-down",
              11: "Categories over time", 12: "Payees over time", 13: "Profit & loss", 14: "Tags", 15: "Tags over time",
              22: "Summary Breakdown", 23: "Summary Breakdown Over Time"}

# Figures. MoneyWiz computes a report it shows with
# -[MoneyWizManager calculateDataForReportAsync:delegate:]: a temporary copy built by COMPUTE in a child
# context forked from the main one, saving and notifications off, the context reset afterwards. The agent
# repeats that sequence (Native.report_data); these are the methods it calls, checked before it starts.
COMPUTE = ("createReportForUser:predefinedReportType:mainDataType:filters:chartType:chartOptions:chartBreakPeriod:"
           "predefinedDatesPeriod:fromDate:toDate:calculateData:oldRootGroup:includeScheduledTransactions:"
           "includeArchivedAccounts:delegate:")
AGENT_CALLS = {
    "MoneyWizManager": ["- forkToChildManagedObjectContext", "- dataAccessor", "- disableExternalNotifications",
                        "- disableDataSavingFromSource:", "- " + COMPUTE],
    "DataAccessor": ["- managedObjectContext"],
    "CustomReport": ["- setReportSortingType:"],
    "TransactionsFilter": ["- setUser:"],
}
# What is read from the results: a tree of TransactionsGroup rows, the chart's series and, for the
# statistics report, the StatisticsReportDTO its root row carries.
ROW_FIELDS = ["groupName", "groupObject", "groupObject.GID", "groupAmount", "groupAmountPercent",
              "groupValuesNamesArray", "groupValuesArray"]
TRANSACTION_FIELDS = ["GID", "date", "accountName", "accountGID", "transferAccountName", "amount",
                      "accountCurrencyName", "reportCurrencyAmount", "desc", "payeeName", "mainCategory.fullName",
                      "tagsNames"]
SERIES_FIELDS = ["seriesName", "currencyName", "seriesXValue", "seriesYValue"]
STATISTICS_FIELDS = [
    "expensesDailyAvarage", "expensesMonthlyAvarage", "expensesYearlyAvarage", "expensesAllTimeTotal",
    "expensesCurrencyName", "incomesDailyAvarage", "incomesMonthlyAvarage", "incomesYearlyAvarage",
    "incomesAllTimeTotal", "incomesCurrencyName",
    "topExpenseCategoriesNamesArray", "topExpenseCategoriesAmountsArray", "topExpenseCategoriesPercentsArray",
    "topExpenseCategoriesCurrencyName", "topExpensePayeesNamesArray", "topExpensePayeesAmountsArray",
    "topExpensePayeesPercentsArray", "topExpensePayeesCurrencyName", "topIncomeCategoriesNamesArray",
    "topIncomeCategoriesAmountsArray", "topIncomeCategoriesPercentsArray", "topIncomeCategoriesCurrencyName",
    "topIncomePayeeNamesArray", "topIncomePayeeAmountsArray", "topIncomePayeePercentsArray",
    "topIncomePayeeCurrencyName", "currentNetWorthAmount", "currentNetWorthDate", "higestNetWorthAmount",
    "higestNetWorthDate", "lowestNetWorthAmount", "lowestNetWorthDate", "netWorthCurrencyName", "millionaireDate",
    "totalExpenseTransactionsCount", "totalIncomeTransactionsCount", "totalTransferTransactionsCount",
    "totalReconcileTransactionsCount", "totalRefundTransactionsCount"]
# Column names MoneyWiz puts in groupValuesNamesArray; "/" and "%" are display separators, and a
# CURRENCY_NAME column names the currency of the figure before it.
COLUMNS = {"AMOUNT": "amount", "AMOUNT_PERCENTS": "percent", "EXPENSES": "expenses", "INCOMES": "incomes",
           "TRANSFERS_LOOSES": "transfer_losses", "BUDGET_BALANCE": "budget_balance", "ACCOUNT_BALANCE": "balance"}
DTO_ENTITIES = {"MWAccountDTO": "Account"}
MAX_TRANSACTIONS = 2000

FIELDS = ["name", "isReportSaved", "predefinedReportType", "mainDataType", "chartType", "breakdownPeriod",
          "predefinedDatesPeriod", "fromDate", "toDate", "groupID", "displayOrder", "includeArchivedAccounts",
          "includeSheduledTransactions", "creationDate", "filtersArray"]
GROUP_FIELDS = ["name", "groupId", "displayOrder", "colorNum", "groupCurrency", "groupObjectsType", "isPredefined"]


def view(row: dict) -> dict:
    kind = row.get("predefinedReportType")
    filters = sorted({f.get("$object", "").removeprefix("TransactionsFilterBy") for f in row.get("filtersArray") or []
                      if isinstance(f, dict) and f.get("$object")})
    return clean({
        "id": row["gid"], "name": row.get("name"), "kind": TYPE_KINDS.get(kind, kind), "type": TYPE_NAMES.get(kind),
        "period": PERIOD_NAMES.get(row.get("predefinedDatesPeriod"), row.get("predefinedDatesPeriod")),
        "from_date": show_date(row.get("fromDate")) if row.get("predefinedDatesPeriod") == PERIODS["custom"] else None,
        "to_date": show_date(row.get("toDate")) if row.get("predefinedDatesPeriod") == PERIODS["custom"] else None,
        "breakdown": BREAKDOWN_NAMES.get(row.get("breakdownPeriod")) if row.get("breakdownPeriod") else None,
        "chart": CHART_NAMES.get(row.get("chartType")) if row.get("chartType") else None,
        "include_scheduled": bool(row.get("includeSheduledTransactions")) or None,
        "include_archived": bool(row.get("includeArchivedAccounts")) or None,
        "filters": filters or None,
        "group_id": row.get("groupID") if row.get("groupID") not in (None, 0, -1) else None,
        "display_order": row.get("displayOrder"),
    })


def _value(v):
    """One result value: dates in local time, amounts formatted like the other tools."""
    if isinstance(v, dict) and "$date" in v:
        return show_date(datetime.fromtimestamp(v["$date"], timezone.utc).isoformat())
    if isinstance(v, dict) and "$dec" in v:
        return money(float(v["$dec"]))
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return money(v)
    return v


def _figures(row: dict) -> dict:
    names, values = row.get("groupValuesNamesArray") or [], row.get("groupValuesArray") or []
    figures, currencies, last = {}, {}, None
    for name, value in zip(names, values, strict=False):  # MoneyWiz may leave a trailing column empty
        if name in ("/", "%"):
            continue
        if name == "CURRENCY_NAME":
            if last:
                currencies[last] = value
            continue
        last = COLUMNS.get(name, str(name).lower())
        figures[last] = _value(value)
    if not names:
        # Rows without columns carry their figure as the group amount; a percent of 0 there is one
        # MoneyWiz did not work out. With columns, the group amount is a display leftover (often 0).
        if row.get("groupAmount") is not None:
            figures["amount"] = _value(row["groupAmount"])
        if row.get("groupAmountPercent") is not None and _value(row["groupAmountPercent"]) != "0":
            figures["percent"] = _value(row["groupAmountPercent"])
    out = {"figures": figures or None}
    if len(set(currencies.values())) == 1:
        out["currency"] = next(iter(currencies.values()))
    elif currencies:
        out["currencies"] = currencies
    return out


def _transaction(t: dict) -> dict:
    return clean({"id": t.get("GID"), "date": _value(t.get("date")), "account": t.get("accountName"),
                  "account_id": t.get("accountGID"), "transfer_account": t.get("transferAccountName"),
                  "amount": _value(t.get("amount")), "currency": t.get("accountCurrencyName"),
                  "report_amount": _value(t.get("reportCurrencyAmount")), "description": t.get("desc"),
                  "payee": t.get("payeeName"), "category": t.get("mainCategory.fullName"),
                  "tags": t.get("tagsNames") or None})


def _row(g: dict) -> dict:
    obj, out = g.get("groupObject"), {"name": g.get("groupName")}
    if isinstance(obj, dict) and obj.get("$ref"):
        out["object"] = {"type": obj["$ref"], "id": obj.get("gid")}
    elif isinstance(obj, dict) and obj.get("$object") in DTO_ENTITIES and g.get("groupObject.GID"):
        out["object"] = {"type": DTO_ENTITIES[obj["$object"]], "id": g["groupObject.GID"]}
    elif isinstance(obj, dict) and "$date" in obj:
        out["date"] = _value(obj)
    out.update(_figures(g))
    if g.get("transactionsArray#"):
        out["transaction_count"] = g["transactionsArray#"]
        if g.get("transactionsArray"):
            out["transactions"] = [_transaction(t) for t in g["transactionsArray"]]
    if g.get("subGroupesArray"):
        out["rows"] = _rows(g["subGroupesArray"])
    return clean(out)


def _rows(groups: list[dict]) -> list[dict]:
    rows = [_row(g) for g in groups]
    return [r for r in rows if set(r) - {"name"} or (r.get("name") or "").strip()]  # drop blank spacer rows


def _series(s: dict) -> dict:
    x = s.get("seriesXValue") or []
    out = {"name": s.get("seriesName"), "currency": s.get("currencyName"),
           "values": [_value(v) for v in s.get("seriesYValue") or []]}
    if x and all(isinstance(v, dict) and "$date" in v for v in x):
        out["dates"] = [_value(v) for v in x]
    elif x and not all(isinstance(v, (int, float)) for v in x):  # plain numbers are slice angles and bar offsets
        out["labels"] = x
    return clean(out)


def _top(stats: dict, prefix: str, names: str = "NamesArray") -> list[dict] | None:
    labels = stats.get(prefix + names) or []
    amounts = stats.get(prefix + "AmountsArray") or []
    percents = stats.get(prefix + "PercentsArray") or []
    return [clean({"name": n, "amount": _value(a), "percent": _value(p)})
            for n, a, p in zip(labels, amounts, percents, strict=False)] or None


def _statistics(st: dict) -> dict:
    def flow(name):
        return clean({"daily_average": _value(st.get(name + "DailyAvarage")),
                      "monthly_average": _value(st.get(name + "MonthlyAvarage")),
                      "yearly_average": _value(st.get(name + "YearlyAvarage")),
                      "total": _value(st.get(name + "AllTimeTotal")), "currency": st.get(name + "CurrencyName")})

    def point(name):
        return clean({"amount": _value(st.get(name + "Amount")), "date": _value(st.get(name + "Date"))}) or None

    return clean({
        "expenses": flow("expenses"), "incomes": flow("incomes"),
        "top_expense_categories": _top(st, "topExpenseCategories"),
        "top_expense_payees": _top(st, "topExpensePayees"),
        "top_income_categories": _top(st, "topIncomeCategories"),
        "top_income_payees": _top(st, "topIncomePayee"),
        "net_worth": clean({"current": point("currentNetWorth"), "highest": point("higestNetWorth"),
                            "lowest": point("lowestNetWorth"), "currency": st.get("netWorthCurrencyName")}) or None,
        "millionaire_date": _value(st.get("millionaireDate")),
        "transaction_counts": clean({"expense": st.get("totalExpenseTransactionsCount"),
                                     "income": st.get("totalIncomeTransactionsCount"),
                                     "transfer": st.get("totalTransferTransactionsCount"),
                                     "reconcile": st.get("totalReconcileTransactionsCount"),
                                     "refund": st.get("totalRefundTransactionsCount")}) or None,
    })


class Reports:
    def __init__(self, native: Native):
        self.n = native
        self.accounts = Accounts(native)
        self.catalog = Catalog(native)
        self.budgets = Budgets(native)

    # ------------------------------------------------------------ reads

    def _rows(self, where=None) -> list[dict]:
        return self.n.query("CustomReport", where=[["isReportSaved", "eq", {"nb": True}]] + (where or []),
                            fields=FIELDS, sort=[["displayOrder", True]])["items"]

    def list(self) -> list[dict]:
        return [view(r) for r in self._rows()]

    def resolve(self, value: str) -> dict:
        rows = self._rows([["GID", "eq", s(value)]]) or self._rows([["name", "eq", s(value)]])
        if len(rows) > 1:
            raise MoneyWizError(f"Several saved reports are named {value!r}; use the report id")
        if not rows:
            raise MoneyWizError(f"Saved report not found: {value!r}")
        return rows[0]

    def get(self, report_id: str) -> dict:
        return view(self.resolve(report_id))

    def data(self, report_ref: str, *, transactions: int = 0) -> dict:
        """A saved report's figures, computed by MoneyWiz as when the report is opened.

        transactions: how many of each row's transactions to list (0: only their count).
        """
        if not isinstance(transactions, int) or isinstance(transactions, bool) or \
                not 0 <= transactions <= MAX_TRANSACTIONS:
            raise MoneyWizError(f"transactions: a count from 0 to {MAX_TRANSACTIONS}")
        row = self.resolve(report_ref)
        kind = TYPE_KINDS.get(row.get("predefinedReportType"))
        return {"report": view(row)} | self._figures_of({"gid": row["gid"]}, kind, row.get("predefinedDatesPeriod"),
                                                        transactions)

    def compute(self, *, transactions: int = 0, **settings) -> dict:
        """Figures of a report that is not saved: MoneyWiz builds it as its report editor
        does, the agent copies it and discards it at once, and only the copy is
        calculated (see the agent's reportData). Nothing is saved or left behind."""
        if not isinstance(transactions, int) or isinstance(transactions, bool) or \
                not 0 <= transactions <= MAX_TRANSACTIONS:
            raise MoneyWizError(f"transactions: a count from 0 to {MAX_TRANSACTIONS}")
        selector, args, period_value = self._creator(**settings)
        figures = self._figures_of({"create": {"selector": selector, "args": args,
                                               "encoding": recorded_encoding("manager", selector)}},
                                   settings["kind"], period_value, transactions)
        asked = clean({k: v for k, v in settings.items() if v not in (None, False, [])})
        return {"report": asked} | figures

    def _figures_of(self, source: dict, kind: str | None, period_value, transactions: int) -> dict:
        group = {"fields": ROW_FIELDS, "each": {"subGroupesArray": ["row", None],
                                                "transactionsArray": ["transaction", transactions]}}
        if kind == "statistics":
            group["each"]["groupObject"] = ["statistics", None]
        result = self.n.report_data({
            **source,
            "fields": ["fromDate", "toDate", "currencyName"],
            "root": {"transactionsRootGroup": "row", "chartViewData": "chart"},
            "shapes": {"row": group, "transaction": {"fields": TRANSACTION_FIELDS},
                       "chart": {"fields": ["stackNamesArray"], "each": {"seriesArray": ["series", None]}},
                       "series": {"fields": SERIES_FIELDS}, "statistics": {"fields": STATISTICS_FIELDS}},
            "max_nodes": 50000, "timeout": 120,
        }, AGENT_CALLS)
        root = result.get("transactionsRootGroup") or {}
        totals = _figures(root)
        if all(v == "0" for v in (totals.get("figures") or {}).values()):
            totals = {}  # the table reports' root: their totals are a row of their own
        chart = result.get("chartViewData") or {}
        out = {
            # MoneyWiz's dates for the period, worked out when computing; distant ones mean "all time".
            "from_date": _value(result.get("fromDate")) if period_value != PERIODS["all_time"] else None,
            "to_date": _value(result.get("toDate")) if period_value != PERIODS["all_time"] else None,
            "currency": result.get("currencyName"),
            "totals": clean(totals) or None,
            "rows": _rows(root.get("subGroupesArray") or []),
            "series": [_series(x) for x in chart.get("seriesArray") or []] or None,
            "stacks": chart.get("stackNamesArray") or None,
        }
        if root.get("transactionsArray#"):
            out["transaction_count"] = root["transactionsArray#"]
            if root.get("transactionsArray"):
                out["transactions"] = [_transaction(t) for t in root["transactionsArray"]]
        if kind == "statistics" and isinstance(root.get("groupObject"), dict):
            out["statistics"] = _statistics(root["groupObject"])
        return clean(out)

    def groups(self) -> list[dict]:
        listed = self.n.invoke("manager", "getCustomReportsGroups", [], save=False)["value"] or []
        ids = [g["gid"] for g in listed if isinstance(g, dict) and g.get("gid")]
        rows = [self.n.get("Group", gid, GROUP_FIELDS) for gid in ids]
        members = {}
        for row in self._rows():
            members.setdefault(row.get("groupID"), []).append(row["gid"])
        return [clean({"id": r["gid"], "group_id": r.get("groupId"), "name": r.get("name"), "color": r.get("colorNum"),
                       "currency": r.get("groupCurrency"), "display_order": r.get("displayOrder"),
                       "predefined": bool(r.get("isPredefined")) or None, "report_ids": members.get(r.get("groupId"))})
                for r in rows if r]

    def group(self, group_ref) -> dict:
        for group in self.groups():
            if group_ref in (group["id"], group.get("group_id"), group.get("name")):
                return group
        raise MoneyWizError(f"Report group not found: {group_ref}")

    # ------------------------------------------------------------ create

    def _choice(self, table: dict, value, field: str) -> int:
        if value not in table:
            raise MoneyWizError(f"{field} must be one of {', '.join(table)}")
        return table[value]

    def _all(self, entity: str, where=None) -> list[str]:
        return [r["gid"] for r in self.n.query(entity, where=where or [], fields=[])["items"]]

    def create(self, *, name: str, group=None, **settings) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise MoneyWizError("name: a saved report needs a name")
        selector, args, _ = self._creator(**settings)
        group_arg = ni(self.group(group)["group_id"]) if group not in (None, "") else NIL
        result = self.n.batch([
            {"target": "manager", "selector": selector, "args": args},
            {"target": "manager", "selector": SAVE, "args": [prev(0), s(name), group_arg], "save": True},
        ])
        created = result["results"][0]["value"]
        if not created or not created.get("gid"):
            raise MoneyWizError("MoneyWiz did not create the report")
        return self.get(created["gid"])

    def _creator(self, *, kind: str, period: str | None = None, from_date: str | None = None,
                 to_date: str | None = None, breakdown: str = "month", chart: str = "pie", flow: str = "expense",
                 accounts=None, categories=None, income_categories=None, payees=None, tags=None, budgets=None,
                 budget: str | None = None, as_of: str | None = None, include_scheduled: bool | None = None,
                 include_archived: bool = False):
        """MoneyWiz's typed creator for a report kind and its arguments, as its report
        editor passes them; also the period's enum value."""
        selector = KINDS.get(kind)
        if selector is None:
            raise MoneyWizError(f"kind must be one of {', '.join(KINDS)}")
        if flow not in ("expense", "income"):
            raise MoneyWizError("flow must be expense or income")
        expenses = flow == "expense"
        period = period or ("next_3_months" if kind == "forecast" else "this_month")
        period_value = self._choice(PERIODS, period, "period")
        if period == "custom":
            if not from_date or not to_date:
                raise MoneyWizError("A custom period needs from_date and to_date")
            start = datetime.combine(parse_when(from_date, field="from_date").astimezone(local_tz()).date(), time(0),
                                     tzinfo=local_tz())
            end = datetime.combine(parse_when(to_date, field="to_date").astimezone(local_tz()).date(), time(0),
                                   tzinfo=local_tz()) + timedelta(days=1)
        else:
            # MoneyWiz works the dates out from the period; these only fill the arguments.
            today = datetime.now(local_tz()).date()
            start = datetime.combine(today.replace(day=1), time(0), tzinfo=local_tz())
            end = datetime.combine(today, time(0), tzinfo=local_tz()) + timedelta(days=1)
        scheduled = (kind == "forecast") if include_scheduled is None else bool(include_scheduled)

        def account_refs():
            ids = [self.accounts.resolve(a)["id"] for a in accounts] if accounts else \
                self._all("Account", [] if include_archived else [["archived", "ne", {"nb": True}]])
            return arr(ref("Account", x) for x in ids)

        def category_refs(values, kind_name):
            ids = [self.catalog.resolve_category(c)["id"] for c in values] if values else \
                [c["id"] for c in self.catalog.categories(kind_name)]
            return arr(ref("Category", x) for x in ids)

        def payee_refs():
            names = {p["name"]: p["id"] for p in self.catalog.payees()}
            ids = [p if p in names.values() else names.get(p) for p in payees] if payees else list(names.values())
            if None in ids:
                raise MoneyWizError("Unknown payee in payees")
            return arr(ref("Payee", x) for x in ids)

        def tag_list():
            known = {t["id"]: t["name"] for t in self.catalog.tags()}
            if not tags:
                return list(known.items())
            out = []
            for t in tags:
                match = next(((k, v) for k, v in known.items() if t in (k, v)), None)
                if match is None:
                    raise MoneyWizError(f"Unknown tag: {t!r}")
                out.append(match)
            return out

        def budget_ref():
            if not budget:
                raise MoneyWizError("A budget_balance report needs budget")
            return ref("Budget", self.budgets.resolve(budget)["gid"])

        builders = {
            "isExpenses": lambda: b(expenses),
            "accounts": account_refs,
            "categories": lambda: category_refs(categories, "expense" if expenses else "income"),
            "expenseCategories": lambda: category_refs(categories, "expense"),
            "incomeCategories": lambda: category_refs(income_categories, "income"),
            "payees": payee_refs,
            "tags": lambda: arr(ref("Tag", k) for k, _ in tag_list()),
            "tagsNames": lambda: arr(s(v) for _, v in tag_list()),
            "budgets": lambda: arr(ref("Budget", self.budgets.resolve(x)["gid"]) for x in budgets) if budgets
            else arr(ref("Budget", x) for x in self._all("Budget")),
            "budget": budget_ref,
            "additionalFilters": lambda: arr([]),
            "chartType": lambda: i(self._choice(CHARTS, chart, "chart")),
            "breakDownPeriod": lambda: i(self._choice(BREAKDOWNS, breakdown, "breakdown")),
            "breakdownPeriod": lambda: i(self._choice(BREAKDOWNS, breakdown, "breakdown")),
            "reportTimePeriod": lambda: i(period_value),
            "fromDate": lambda: date(start),
            "toDate": lambda: date(end),
            "asOfDate": lambda: date(parse_when(as_of, field="as_of") if as_of else datetime.now(local_tz())),
            "includeScheduledTransactions": lambda: b(scheduled),
            "includeArchivedAccounts": lambda: b(include_archived),
        }
        keywords = selector[:-1].split(":")
        return selector, [USER] + [builders[k]() for k in keywords[1:]], period_value

    # ------------------------------------------------------------ change

    def _place(self, ordered_ids: list[str]):
        siblings = arr(ref("CustomReport", x) for x in ordered_ids)
        for index, report_id in enumerate(ordered_ids):
            self.n.invoke("manager", REORDER, [ref("CustomReport", report_id), i(index), siblings,
                                               s("Report updated"), USER])

    def _members(self, group_id) -> list[str]:
        """Saved reports of one group in display order (None: the ungrouped ones)."""
        def group_of(row):
            return None if row.get("groupID") in (None, 0, -1) else row["groupID"]
        return [r["gid"] for r in self._rows() if group_of(r) == group_id]

    def update(self, report_ref: str, *, name: str | None = None, group=...) -> dict:
        """Rename a saved report or move it to another group (or out of one)."""
        row = self.resolve(report_ref)
        old_group = row.get("groupID") if row.get("groupID") not in (None, 0, -1) else None
        new_group = old_group if group is ... else (self.group(group)["group_id"] if group not in (None, "") else None)
        before = self._members(old_group)
        # saveCustomReport:withName:groupID: renames and files a report, appended to its group.
        self.n.invoke("manager", SAVE, [ref("CustomReport", row["gid"]), s(name or row.get("name") or ""),
                                        ni(new_group) if new_group is not None else NIL])
        if new_group == old_group:
            self._place(before)  # keep its place
        return self.get(row["gid"])

    def reorder(self, ordered: list[str], group=None) -> list[dict]:
        group_id = self.group(group)["group_id"] if group not in (None, "") else None
        ids = [self.resolve(x)["gid"] for x in ordered]
        if sorted(ids) != sorted(self._members(group_id)):
            raise MoneyWizError("Give every saved report of that group exactly once (see moneywiz_list_report_groups)")
        self._place(ids)
        return [self.get(x) for x in ids]

    def delete(self, report_ref: str) -> dict:
        row = self.resolve(report_ref)
        self.n.invoke("manager", DELETE, [ref("CustomReport", row["gid"])])
        if self.n.get("CustomReport", row["gid"]):
            raise MoneyWizError("The report still exists after delete")
        return {"deleted": row["gid"], "name": row.get("name")}

    # ------------------------------------------------------------ groups

    def create_group(self, *, name: str, color: int = 1, currency: str | None = None,
                     report_ids: list[str] | None = None) -> dict:
        from .native import currency_code
        from .settings import Settings
        code = currency_code(self.n, currency or Settings(self.n).currencies().get("default_currency"))
        reports = arr(ref("CustomReport", self.resolve(x)["gid"]) for x in report_ids or [])
        created = self.n.invoke("manager", "createCustomReportsGroupForUser:name:colorNum:currency:reports:",
                                [USER, s(name), i(color), s(code), reports])["value"]
        if not created or not created.get("gid"):
            raise MoneyWizError("MoneyWiz did not create the report group")
        return self.group(created["gid"])

    def update_group(self, group_ref, *, name: str | None = None, color: int | None = None,
                     report_ids: list[str] | None = None) -> dict:
        group = self.group(group_ref)
        members = report_ids if report_ids is not None else group.get("report_ids") or []
        reports = arr(ref("CustomReport", self.resolve(x)["gid"]) for x in members)
        self.n.invoke("manager", "updateCustomReportsGroup:name:colorNum:reports:",
                      [ref("Group", group["id"]), s(name or group["name"]),
                       i(group.get("color", 1) if color is None else color), reports])
        return self.group(group["id"])

    def delete_group(self, group_ref) -> dict:
        group = self.group(group_ref)
        if group.get("predefined"):
            raise MoneyWizError("MoneyWiz's predefined report groups cannot be deleted")
        self.n.invoke("manager", "deleteCustomReportsGroup:", [ref("Group", group["id"])])
        if self.n.get("Group", group["id"]):
            raise MoneyWizError("The report group still exists after delete")
        return {"deleted": group["id"], "name": group["name"], "report_ids": group.get("report_ids")}

    def reorder_groups(self, ordered: list[str]) -> list[dict]:
        groups = self.groups()
        ids = [self.group(x)["id"] for x in ordered]
        if sorted(ids) != sorted(g["id"] for g in groups):
            raise MoneyWizError("Give every report group exactly once")
        for index, group_id in enumerate(ids):
            self.n.invoke("manager", "updateCustomReportsGroup:withDisplayOrder:", [ref("Group", group_id), ni(index)])
        return self.groups()
