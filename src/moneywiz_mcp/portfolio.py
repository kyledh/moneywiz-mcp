"""Reads for D05 investments, D06 loans, D07 budgets and D08 scheduled transactions,
plus the writes that have a single, well-defined business entry point.
"""
from __future__ import annotations

from .accounts import Accounts
from .native import Native, MoneyWizError, s, n, nb, ref, date, NIL
from .values import amount, money, parse_when, show_date, clean

HOLDING_FIELDS = ["symbol", "symbolAlias", "desc", "assetClass", "holdingType", "cusip", "pricePerShare",
                  "openningNumberOfShares", "costBasisOfMissingOBShares", "investmentObjectType",
                  "isPricePerShareAvailableOnline", "isFromOnlineBanking",
                  "investmentAccount.GID", "investmentAccount.name", "investmentAccount.currencyName",
                  "numberOfShares", "currentValue", "costBasis", "profitLoss"]
PLAN_FIELDS = ["apr", "durationMonths", "firstDueDate", "startDate", "planType", "principalAmount",
               "accountPayments.GID", "accountPayments.name", "categoryPrincipal.name", "categoryInterest.name",
               "payee.name", "loanAccount.GID", "loanAccount.name"]
PLAN_ITEM_FIELDS = ["date", "paymentAmount", "principalAmount", "interestAmount", "lastP", "paid", "currencyName"]
BUDGET_FIELDS = ["name", "balance", "carriedBalance", "openingBalance", "currencyName", "startDate", "endDate",
                 "duration", "durationUnits", "isRepeatable", "isPeriodMoneyTransferEnabled", "type",
                 "displayOrder", "groupID", "iconFileName", "tagsPredicateToMonitor",
                 "accountsLinksToMonitor.account.GID", "accountsLinksToMonitor.account.name",
                 "categoriesAssigmentsToMonitor.category.GID", "categoriesAssigmentsToMonitor.category.name"]
SCHEDULED_FIELDS = ["desc", "notes", "amount", "currencyName", "executeDate", "firstExecuteDate", "lastExecuteDate",
                    "isRepeatable", "duration", "durationUnits", "autoPay", "disableExecution", "executesCount",
                    "waitingExecutesCount", "weekendsHandler", "fee",
                    "account.GID", "account.name", "payee.name", "tags.name",
                    "categoriesAssigments.category.name", "categoriesAssigments.amount",
                    "recipientAccount.GID", "recipientAccount.name"]

# MoneyWiz's durationUnits enum is not sequential. 8=month and 4=year were
# confirmed against the app's own -nextExecuteDateWithNum: sequence; other
# values are reported raw rather than guessed.
DURATION_UNITS = {4: "year", 8: "month"}


def period(duration, units) -> str | None:
    if not duration:
        return None
    name = DURATION_UNITS.get(units)
    if name is None:
        return f"{duration} x unit {units}"
    return f"every {name}" if duration == 1 else f"every {duration} {name}s"
SCHEDULED_KINDS = {"ScheduledWithdrawTransactionHandler": "expense",
                   "ScheduledDepositTransactionHandler": "income",
                   "ScheduledTransferTransactionHandler": "transfer"}


def holding_view(row: dict) -> dict:
    return clean({
        "id": row["gid"], "symbol": row.get("symbol"), "alias": row.get("symbolAlias"),
        "name": row.get("desc"), "asset_class": row.get("assetClass"), "type": row.get("holdingType"),
        "cusip": row.get("cusip"),
        "account_id": row.get("investmentAccount.GID"), "account": row.get("investmentAccount.name"),
        "currency": row.get("investmentAccount.currencyName"),
        "shares": money(row.get("numberOfShares")),
        "opening_shares": money(row.get("openningNumberOfShares")),
        "price_per_share": money(row.get("pricePerShare")),
        "current_value": money(row.get("currentValue")),
        "cost_basis": money(row.get("costBasis")),
        "profit_loss": money(row.get("profitLoss")),
        "online_price": bool(row.get("isPricePerShareAvailableOnline")) or None,
    })


class Portfolio:
    def __init__(self, native: Native):
        self.n = native
        self.accounts = Accounts(native)

    # ------------------------------------------------------------ D05 investments

    def holdings(self, account: str | None = None, *, symbol: str | None = None) -> list[dict]:
        where = []
        if account:
            where.append(["investmentAccount.GID", "eq", s(self.accounts.resolve(account)["id"])])
        if symbol:
            where.append({"or": [["symbol", "eq", s(symbol)], ["symbolAlias", "eq", s(symbol)]]})
        rows = self.n.query("InvestmentHolding", where=where, fields=HOLDING_FIELDS, sort=[["symbol", True]])["items"]
        return [holding_view(r) for r in rows]

    def holding(self, holding_id: str) -> dict:
        row = self.n.get("InvestmentHolding", holding_id, HOLDING_FIELDS)
        if row is None:
            raise MoneyWizError(f"Holding not found: {holding_id}")
        return holding_view(row)

    def set_holding_price(self, holding_id: str, price, when: str | None = None) -> dict:
        """Record a manual price for one holding on one date."""
        current = self.holding(holding_id)
        value = amount(price, field="price")
        moment = parse_when(when) if when else None
        self.n.invoke("manager", "updateInvestmentHolding:addManualPricePerShare:forDate:",
                      [ref("InvestmentHolding", holding_id), n(float(value)),
                       date(moment) if moment else NIL])
        after = self.holding(holding_id)
        return {"holding": after, "requested_price": str(value), "date": show_date(moment.isoformat()) if moment else None,
                "previous_price": current.get("price_per_share")}

    def set_holding_shares(self, holding_id: str, shares) -> dict:
        self.holding(holding_id)
        value = amount(shares, field="shares", allow_zero=True)
        self.n.invoke("manager", "updateInvestmentHolding:setNumberOfShares:",
                      [ref("InvestmentHolding", holding_id), n(float(value))])
        return self.holding(holding_id)

    def set_holding_cost_basis(self, holding_id: str, cost_basis) -> dict:
        self.holding(holding_id)
        value = amount(cost_basis, field="cost_basis", allow_zero=True)
        self.n.invoke("manager", "updateInvestmentHolding:setCostBasis:",
                      [ref("InvestmentHolding", holding_id), n(float(value))])
        return self.holding(holding_id)

    # ------------------------------------------------------------ D06 loans

    def loans(self) -> list[dict]:
        rows = self.n.query("LoanAccount", fields=["name", "currencyName", "archived", "creditLimit",
                                                   "paymentPlan.GID", "balanceDictionary"] +
                            ["paymentPlan." + f for f in ("apr", "durationMonths", "startDate", "firstDueDate",
                                                          "planType", "principalAmount")],
                            sort=[["displayOrder", True]])["items"]
        out = []
        for row in rows:
            balance = row.get("balanceDictionary") or {}
            out.append(clean({
                "id": row["gid"], "name": row.get("name"), "currency": row.get("currencyName"),
                "archived": bool(row.get("archived")),
                "balance": money(balance.get("BalanceNumber")),
                "principal": money(row.get("paymentPlan.principalAmount") or row.get("creditLimit")),
                "apr": money(row.get("paymentPlan.apr")),
                "months": row.get("paymentPlan.durationMonths"),
                "start_date": show_date(row.get("paymentPlan.startDate")),
                "first_due_date": show_date(row.get("paymentPlan.firstDueDate")),
                "payment_plan_id": row.get("paymentPlan.GID"),
            }))
        return out

    def payment_plan(self, loan: str) -> dict:
        account = self.accounts.resolve(loan)
        row = self.n.get("Account", account["id"], ["paymentPlan.GID"])
        plan_id = row.get("paymentPlan.GID") if row else None
        if not plan_id:
            raise MoneyWizError(f"{account['name']!r} has no payment plan")
        plan = self.n.get("PaymentPlan", plan_id, PLAN_FIELDS)
        items = self.n.query("PaymentPlanItem", where=[["paymentPlan.GID", "eq", s(plan_id)]],
                             fields=PLAN_ITEM_FIELDS, sort=[["date", True]])["items"]
        paid = [x for x in items if x.get("paid")]
        return clean({
            "loan_id": account["id"], "loan": account["name"], "payment_plan_id": plan_id,
            "apr": money(plan.get("apr")), "months": plan.get("durationMonths"),
            "principal": money(plan.get("principalAmount")),
            "start_date": show_date(plan.get("startDate")), "first_due_date": show_date(plan.get("firstDueDate")),
            "payment_account": plan.get("accountPayments.name"),
            "principal_category": plan.get("categoryPrincipal.name"),
            "interest_category": plan.get("categoryInterest.name"),
            "payee": plan.get("payee.name"),
            "installments": len(items), "paid_installments": len(paid),
            "schedule": [clean({"date": show_date(x.get("date")), "payment": money(x.get("paymentAmount")),
                                "principal": money(x.get("principalAmount")), "interest": money(x.get("interestAmount")),
                                "remaining": money(x.get("lastP")), "paid": bool(x.get("paid"))}) for x in items],
        })

    # ------------------------------------------------------------ D07 budgets

    def budgets(self, *, include_past: bool = False) -> list[dict]:
        rows = self.n.query("Budget", fields=BUDGET_FIELDS, sort=[["displayOrder", True]])["items"]
        out = []
        for row in rows:
            spent = self.n.invoke("manager", "budgetsExpenses:convertToCurrency:",
                                  [{"arr": [ref("Budget", row["gid"])]}, s(row.get("currencyName") or "CNY")],
                                  save=False)["value"]
            remaining = self.n.invoke("manager", "budgetsRemaining:convertToCurrency:",
                                      [{"arr": [ref("Budget", row["gid"])]}, s(row.get("currencyName") or "CNY")],
                                      save=False)["value"]
            out.append(clean({
                "id": row["gid"], "name": row.get("name"), "currency": row.get("currencyName"),
                "limit": money(row.get("balance")),
                "spent": money(spent), "remaining": money(remaining),
                "carried": money(row.get("carriedBalance")),
                "start_date": show_date(row.get("startDate")), "end_date": show_date(row.get("endDate")),
                "repeats": bool(row.get("isRepeatable")),
                "period": period(row.get("duration"), row.get("durationUnits")) if row.get("isRepeatable") else None,
                "rollover": bool(row.get("isPeriodMoneyTransferEnabled")) or None,
                "accounts": row.get("accountsLinksToMonitor.account.name") or None,
                "categories": row.get("categoriesAssigmentsToMonitor.category.name") or None,
                "tag_filter": row.get("tagsPredicateToMonitor") or None,
            }))
        return out

    def budget(self, budget_id: str) -> dict:
        found = [x for x in self.budgets() if x["id"] == budget_id]
        if not found:
            raise MoneyWizError(f"Budget not found: {budget_id}")
        return found[0]

    # ------------------------------------------------------------ D08 scheduled

    def _upcoming(self, handler_id: str, count: int = 3) -> list[str]:
        """Ask MoneyWiz itself for the next execution dates."""
        dates = []
        for index in range(1, count + 1):
            value = self.n.invoke(ref("ScheduledTransactionHandler", handler_id), "nextExecuteDateWithNum:",
                                  [{"i": index}], encoding="@24@0:8q16", save=False)["value"]
            if value:
                dates.append(show_date(value))
        return dates

    def scheduled(self, *, account: str | None = None, include_disabled: bool = True) -> list[dict]:
        where = []
        if account:
            where.append(["account.GID", "eq", s(self.accounts.resolve(account)["id"])])
        if not include_disabled:
            where.append(["disableExecution", "ne", nb(True)])
        rows = self.n.query("ScheduledTransactionHandler", where=where, fields=SCHEDULED_FIELDS,
                            sort=[["executeDate", True]])["items"]
        out = []
        for row in rows:
            categories = list(zip(row.get("categoriesAssigments.category.name") or [],
                                  row.get("categoriesAssigments.amount") or [], strict=True))
            out.append(clean({
                "id": row["gid"], "kind": SCHEDULED_KINDS.get(row["$ref"], row["$ref"]),
                "description": row.get("desc"), "memo": row.get("notes") or None,
                "amount": money(row.get("amount")), "currency": row.get("currencyName"),
                "account_id": row.get("account.GID"), "account": row.get("account.name"),
                "transfer_to_id": row.get("recipientAccount.GID"), "transfer_to": row.get("recipientAccount.name"),
                "next_date": show_date(row.get("executeDate")),
                "first_date": show_date(row.get("firstExecuteDate")),
                "last_run": show_date(row.get("lastExecuteDate")),
                "repeats": bool(row.get("isRepeatable")),
                "period": period(row.get("duration"), row.get("durationUnits")) if row.get("isRepeatable") else None,
                "upcoming_dates": self._upcoming(row["gid"]) if row.get("isRepeatable") else None,
                "auto_pay": bool(row.get("autoPay")) or None,
                "paused": bool(row.get("disableExecution")) or None,
                "executions": row.get("executesCount"),
                "due_now": row.get("waitingExecutesCount") or None,
                "payee": row.get("payee.name"), "tags": row.get("tags.name") or None,
                "categories": [{"name": name, "amount": money(value)} for name, value in categories] or None,
            }))
        return out
