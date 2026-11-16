"""D05 investment trades and holdings.

Shares, prices and commissions are primitive C doubles in these business
methods, so they travel as {"d": ...}, not as NSNumber.
"""
from __future__ import annotations

from .accounts import Accounts
from .native import Native, MoneyWizError, s, d, b, i, ref, date, opt_s, NIL
from .portfolio import Portfolio
from .transactions import Transactions
from .values import amount, parse_when

BUY = ("buyInvestmentsShares:transactionGID:desc:assetClass:holdingType:cusip:date:sharesSymbol:numberOfShares:"
       "pricePerShare:commission:imagesPaths:notes:")
SELL = ("sellInvestmentsShares:transactionGID:desc:assetClass:holdingType:cusip:date:sharesSymbol:numberOfShares:"
        "pricePerShare:commission:imagesPaths:notes:")
EDIT_BUY = ("editInvestmentsBuyTransaction:account:desc:assetClass:holdingType:cusip:date:sharesSymbol:"
            "numberOfShares:pricePerShare:commission:imagesPaths:notes:isDirectUserAction:")
EDIT_SELL = ("editInvestmentsSellTransaction:account:desc:assetClass:holdingType:cusip:date:sharesSymbol:"
             "numberOfShares:pricePerShare:commission:imagesPaths:notes:isDirectUserAction:")
# Not shipped: exchangeInvestmentsShares:... needs a preparedValuation object
# from investmentExchangeValuationForAccount:..., and that call returns nil for
# manually priced holdings. See STATUS.md.
CREATE_HOLDING = ("createInvestmentHoldingForAccount:investmentObjectType:dataSourceServiceId:assetClass:holdingType:"
                  "desc:cusip:symbol:symbolAlias:openningNumberOfShares:pricePerShare:")

SECURITY, CURRENCY = 0, 1  # investmentObjectType values present in the ledger
INVESTMENT_KINDS = ("investment", "forex")
TRADE_KINDS = {"investment_buy": EDIT_BUY, "investment_sell": EDIT_SELL}


def _symbol(value: str) -> str:
    if not isinstance(value, str):
        raise MoneyWizError("symbol: text required, such as AAPL")
    return value


class Investments:
    def __init__(self, native: Native):
        self.n = native
        self.accounts = Accounts(native)
        self.portfolio = Portfolio(native)
        self.transactions = Transactions(native)

    def _account(self, account: str) -> dict:
        resolved = self.accounts.resolve(account)
        if resolved["kind"] not in INVESTMENT_KINDS:
            raise MoneyWizError(f"{resolved['name']!r} is a {resolved['kind']} account; "
                                "trades need an investment or forex account")
        return resolved

    def _holding_of(self, account_id: str, symbol: str) -> dict | None:
        for holding in self.portfolio.holdings(account_id):
            if symbol in (holding.get("symbol"), holding.get("alias")):
                return holding
        return None

    # ------------------------------------------------------------ holdings

    def create_holding(self, *, account: str, symbol: str, name: str = "", holding_type: str = "",
                       shares="0", price="0", alias: str | None = None, cusip: str = "",
                       currency_position: bool = False) -> dict:
        resolved = self._account(account)
        _symbol(symbol)
        opening = amount(shares, field="shares")
        value = amount(price, field="price")
        result = self.n.invoke("manager", CREATE_HOLDING,
                               [ref("Account", resolved["id"]), i(CURRENCY if currency_position else SECURITY),
                                NIL, s(""), s(holding_type or ""), s(name or symbol), s(cusip or ""),
                                s(symbol), opt_s(alias), d(float(opening)), d(float(value))])["value"]
        if not result:
            raise MoneyWizError("MoneyWiz did not create the holding")
        return self.portfolio.holding(result["gid"])

    def delete_holding(self, holding_id: str, *, delete_trades: bool = False) -> dict:
        holding = self.portfolio.holding(holding_id)
        trades = self.n.count("Transaction", [["investmentHolding.GID", "eq", s(holding_id)]])
        result = self.n.invoke("manager", "deleteInvestmentHolding:deleteTrades:",
                               [ref("InvestmentHolding", holding_id), b(delete_trades)])
        if result["value"] is False:
            raise MoneyWizError("MoneyWiz refused to delete the holding")
        if self.n.get("InvestmentHolding", holding_id):
            raise MoneyWizError("Holding still exists after delete")
        return {"deleted": holding_id, "symbol": holding.get("symbol"), "deleted_trades": trades if delete_trades else 0}

    # ------------------------------------------------------------ trades

    def trade(self, *, account: str, side: str, symbol: str, shares, price, date_: str,
              commission="0", description: str = "", memo: str = "", holding_type: str = "",
              cusip: str = "") -> dict:
        if side not in ("buy", "sell"):
            raise MoneyWizError("side must be buy or sell")
        resolved = self._account(account)
        _symbol(symbol)
        quantity = amount(shares, field="shares")
        unit = amount(price, field="price")
        # A negative commission is a rebate; MoneyWiz stores and nets it as given.
        fee = amount(commission, field="commission")
        # Selling a symbol that is not held opens a short position: MoneyWiz's
        # sell method creates the holding itself, as a buy does.
        existing = self._holding_of(resolved["id"], symbol)
        if existing and existing.get("name") and not description:
            description = existing["name"]
        before = set(self._trade_ids(resolved["id"]))
        result = self.n.invoke("manager", BUY if side == "buy" else SELL,
                               [ref("Account", resolved["id"]), NIL, s(description or symbol), s(""),
                                s(holding_type or (existing or {}).get("type") or ""), s(cusip or ""),
                                date(parse_when(date_)), s(symbol), d(float(quantity)), d(float(unit)),
                                d(float(fee)), NIL, s(memo or "")])
        created = self._created_trade(result, resolved["id"], before)
        return {"transaction": created, "holding": self._holding_of(resolved["id"], symbol),
                "account_balance": self.accounts.get(resolved["id"])["balance"]}

    def _trade_ids(self, account_id: str) -> list[str]:
        rows = self.n.query("Transaction", where=[["account.GID", "eq", s(account_id)]], fields=[])["items"]
        return [r["gid"] for r in rows]

    def _created_trade(self, result, account_id: str, before: set[str]) -> dict:
        value = result.get("value")
        if isinstance(value, dict) and value.get("gid") and self.n.get("Transaction", value["gid"]):
            return self.transactions.get(value["gid"])
        # Some trade methods return the holding or an array; fall back to the new row.
        fresh = [x for x in self._trade_ids(account_id) if x not in before]
        if len(fresh) != 1:
            raise MoneyWizError(f"MoneyWiz reported {len(fresh)} new transactions for this trade; check the app")
        return self.transactions.get(fresh[0])

    def update_trade(self, transaction_id: str, **changes) -> dict:
        current = self.transactions.get(transaction_id)
        selector = TRADE_KINDS.get(current["kind"])
        if selector is None:
            raise MoneyWizError(f"{current['kind']} transactions are not investment trades")
        unknown = set(changes) - {"shares", "price", "commission", "date", "description", "memo", "symbol"}
        if unknown:
            raise MoneyWizError(f"Unknown trade fields: {sorted(unknown)}")
        account = self.accounts.resolve(current["account_id"])
        symbol = _symbol(changes.get("symbol", current.get("symbol")))
        quantity = amount(changes.get("shares", current.get("shares")), field="shares")
        unit = amount(changes.get("price", current.get("price_per_share")), field="price")
        fee = amount(changes.get("commission", current.get("fee") or 0), field="commission")
        when = parse_when(changes["date"]) if changes.get("date") else parse_when(current["date"])
        holding = self._holding_of(account["id"], symbol) or {}
        self.n.invoke("manager", selector,
                      [ref("Transaction", transaction_id), ref("Account", account["id"]),
                       s(changes.get("description", current.get("description") or symbol)), s(""),
                       s(holding.get("type") or ""), s(""), date(when), s(symbol),
                       d(float(quantity)), d(float(unit)), d(float(fee)), NIL,
                       s(changes.get("memo", current.get("memo") or "")), b(True)])
        return {"transaction": self.transactions.get(transaction_id),
                "holding": self._holding_of(account["id"], symbol),
                "account_balance": self.accounts.get(account["id"])["balance"]}

    def delete_trade(self, transaction_id: str) -> dict:
        current = self.transactions.get(transaction_id)
        if current["kind"] not in ("investment_buy", "investment_sell", "investment_exchange"):
            raise MoneyWizError(f"{current['kind']} is not an investment trade; use moneywiz_delete_transaction")
        return self.transactions.delete(transaction_id)
