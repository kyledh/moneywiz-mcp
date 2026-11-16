"""Reads for D05 investments and D06 loans, plus the holding writes that have a
single, well-defined business entry point.
"""
from __future__ import annotations

from .accounts import Accounts
from .native import Native, MoneyWizError, s, n, ref, date, NIL
from .values import amount, money, parse_when, show_date, clean, stored

HOLDING_FIELDS = ["symbol", "symbolAlias", "desc", "assetClass", "holdingType", "cusip", "pricePerShare",
                  "openningNumberOfShares", "costBasisOfMissingOBShares", "investmentObjectType",
                  "isPricePerShareAvailableOnline", "isFromOnlineBanking",
                  "investmentAccount.GID", "investmentAccount.name", "investmentAccount.currencyName",
                  "numberOfShares", "totalPrice", "costBasis", "unrealizedGain", "realizedGain", "totalReturn",
                  "totalReturnPercents"]
PLAN_FIELDS = ["apr", "durationMonths", "firstDueDate", "startDate", "planType", "principalAmount",
               "accountPayments.GID", "accountPayments.name", "categoryPrincipal.name", "categoryInterest.name",
               "payee.name", "loanAccount.GID", "loanAccount.name"]
PLAN_ITEM_FIELDS = ["date", "paymentAmount", "principalAmount", "interestAmount", "lastP", "paid", "currencyName"]
def holding_view(row: dict) -> dict:
    return clean({
        "id": row["gid"], "symbol": row.get("symbol"), "alias": row.get("symbolAlias"),
        "name": row.get("desc"), "asset_class": row.get("assetClass"), "type": row.get("holdingType"),
        "cusip": row.get("cusip"),
        "account_id": row.get("investmentAccount.GID"), "account": row.get("investmentAccount.name"),
        "currency": row.get("investmentAccount.currencyName"),
        "shares": money(row.get("numberOfShares")),
        "opening_shares": money(row.get("openningNumberOfShares")),
        "price_per_share": stored(row.get("pricePerShare")),
        # MoneyWiz's own figures: market value (totalPrice) and gains.
        "market_value": money(row.get("totalPrice")),
        "cost_basis": money(row.get("costBasis")),
        "unrealized_gain": money(row.get("unrealizedGain")),
        "realized_gain": money(row.get("realizedGain")),
        "total_return": money(row.get("totalReturn")),
        "total_return_percent": money(row["totalReturnPercents"] * 100) if row.get("totalReturnPercents") is not None else None,
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

    def update_holding(self, holding_id: str, *, shares=None, cost_basis=None, price=None, when: str | None = None) -> dict:
        """Set a holding's share count, cost basis and/or manual price (in that order, each with
        MoneyWiz's own call); returns the holding and what the changed figures were before."""
        if shares is None and cost_basis is None and price is None:
            raise MoneyWizError("Give shares, cost_basis or price")
        if when is not None and price is None:
            raise MoneyWizError("price_date goes with a price")
        before = self.holding(holding_id)
        if shares is not None:
            self.set_holding_shares(holding_id, shares)
        if cost_basis is not None:
            self.set_holding_cost_basis(holding_id, cost_basis)
        if price is not None:
            self.set_holding_price(holding_id, price, when)
        changed = [k for k, v in (("shares", shares), ("cost_basis", cost_basis), ("price_per_share", price))
                   if v is not None]
        return {"holding": self.holding(holding_id), "previous": {k: before.get(k) for k in changed}}

    def set_holding_shares(self, holding_id: str, shares) -> dict:
        self.holding(holding_id)
        value = amount(shares, field="shares")
        self.n.invoke("manager", "updateInvestmentHolding:setNumberOfShares:",
                      [ref("InvestmentHolding", holding_id), n(float(value))])
        return self.holding(holding_id)

    def set_holding_cost_basis(self, holding_id: str, cost_basis) -> dict:
        self.holding(holding_id)
        value = amount(cost_basis, field="cost_basis")
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
