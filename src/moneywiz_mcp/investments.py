"""D05 investment trades and holdings.

Shares, prices and commissions are primitive C doubles in these business
methods, so they travel as {"d": ...}, not as NSNumber.
"""
from __future__ import annotations

from .accounts import Accounts
from .native import Native, MoneyWizError, s, d, b, i, ref, arr, date, opt_s, NIL, currency_code
from .portfolio import Portfolio, HOLDING_FIELDS, holding_view
from .transactions import Transactions, given, images_arg
from .values import amount, day_start, parse_when, show_date

BUY = ("buyInvestmentsShares:transactionGID:desc:assetClass:holdingType:cusip:date:sharesSymbol:numberOfShares:"
       "pricePerShare:commission:imagesPaths:notes:")
SELL = ("sellInvestmentsShares:transactionGID:desc:assetClass:holdingType:cusip:date:sharesSymbol:numberOfShares:"
        "pricePerShare:commission:imagesPaths:notes:")
EDIT_BUY = ("editInvestmentsBuyTransaction:account:desc:assetClass:holdingType:cusip:date:sharesSymbol:"
            "numberOfShares:pricePerShare:commission:imagesPaths:notes:isDirectUserAction:")
EDIT_SELL = ("editInvestmentsSellTransaction:account:desc:assetClass:holdingType:cusip:date:sharesSymbol:"
             "numberOfShares:pricePerShare:commission:imagesPaths:notes:isDirectUserAction:")
# A currency exchange inside a forex account, as MoneyWiz's exchange editor
# (InvestmentExchangeTransactionVC) records one: from one currency position to
# another, with a fee in a currency of its own. MoneyWiz values the exchange in
# the account's currency itself and records nothing (returns nil) when it has
# no exchange rate for it; its editor then asks for a manual rate.
EXCHANGE = ("exchangeInvestmentsShares:transactionGID:desc:assetClass:holdingType:cusip:date:fromSharesSymbol:"
            "toSharesSymbol:fromNumberOfShares:toNumberOfShares:commission:commissionCurrency:imagesPaths:notes:")
EDIT_EXCHANGE = ("editInvestmentsExchangeTransaction:account:desc:assetClass:holdingType:cusip:date:fromSharesSymbol:"
                 "toSharesSymbol:fromNumberOfShares:toNumberOfShares:commission:commissionCurrency:imagesPaths:notes:"
                 "isDirectUserAction:")
NO_RATE = ("MoneyWiz has no exchange rate to value this exchange, so it recorded nothing (its editor says the same); "
           "set a manual rate with moneywiz_set_exchange_rate, or retry once MoneyWiz has fetched rates")
CREATE_HOLDING = ("createInvestmentHoldingForAccount:investmentObjectType:dataSourceServiceId:assetClass:holdingType:"
                  "desc:cusip:symbol:symbolAlias:openningNumberOfShares:pricePerShare:")

# MoneyWiz's stock split: in every investment account holding the symbol (or
# alias), it records the share difference on that day as a buy (or, for a
# reverse split, a sell) at price 0, flags it as a split (Transaction.flags,
# STOCK_SPLIT_FLAG, which its gain calculation reads), and for manually priced
# holdings divides the previous day's price by the ratio. Read from
# -[MoneyWizManager splitStocksForSymbolOrAlias:splitRatio:splitDate:] and its
# editor, InvestmentHoldingStocksSplitVC (ratio = new shares / old shares, the
# day's start as the date).
SPLIT = "splitStocksForSymbolOrAlias:splitRatio:splitDate:"
SPLIT_SKIPS_TYPE = "Currency"  # the holdingType the split passes over

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
        unknown = set(changes) - {"shares", "price", "commission", "date", "description", "memo", "symbol", "account"}
        if unknown:
            raise MoneyWizError(f"Unknown trade fields: {sorted(unknown)}")
        source = self.accounts.resolve(current["account_id"])
        # MoneyWiz's edit methods take the account, so a trade can move to another one.
        account = self._account(changes["account"]) if changes.get("account") else source
        if account["kind"] != source["kind"]:
            # MoneyWiz moves buys and sells only between accounts of their kind (moveTransactions:toAccount:).
            raise MoneyWizError(f"A trade in a {source['kind']} account can only move to another {source['kind']} account")
        symbol = _symbol(changes.get("symbol", current.get("symbol")))
        row = self.transactions._row(transaction_id)
        # Unchanged figures go back as stored (MoneyWiz keeps a trade's shares, price and
        # commission as given), not as the rounded figures the view shows.
        quantity = amount(changes["shares"], field="shares") if "shares" in changes else given(row.get("numberOfShares"), 1)
        unit = amount(changes["price"], field="price") if "price" in changes else given(row.get("pricePerShare"), 1)
        fee = amount(changes["commission"], field="commission") if "commission" in changes else given(row.get("fee"), 1)
        when = parse_when(changes["date"]) if changes.get("date") else parse_when(row["date"])
        # A trade moving to an account that does not hold the symbol yet keeps its holding's type.
        holding = self._holding_of(account["id"], symbol) or self._holding_of(source["id"], symbol) or {}
        # With another account or symbol, MoneyWiz deletes the trade (and its stored
        # attachment files) and records it again, keeping a split's flag; the edit
        # returns the trade as it now is. Its attachments travel as copies.
        recreated = account["id"] != source["id"] or \
            symbol.strip().upper() != (current.get("symbol") or "").strip().upper()
        staged = self.transactions.staged_copies(transaction_id, row) if recreated else []
        try:
            result = self.n.invoke("manager", selector,
                                   [ref("Transaction", transaction_id), ref("Account", account["id"]),
                                    s(changes.get("description", current.get("description") or symbol)), s(""),
                                    s(holding.get("type") or ""), s(""), date(when), s(symbol),
                                    d(float(quantity)), d(float(unit)), d(float(fee)),
                                    arr(s(x) for x in staged) if staged else images_arg(row),
                                    s(changes.get("memo", current.get("memo") or "")), b(True)])
        finally:
            for path in staged:
                self.n.unstage_file(path)
        value = result.get("value")
        edited = value["gid"] if isinstance(value, dict) and value.get("gid") else transaction_id
        if not self.n.get("Transaction", edited):
            raise MoneyWizError("MoneyWiz did not return the edited trade; check the app")
        trade = self.transactions.get(edited)
        kept, had = len(trade.get("attachments") or []), len(current.get("attachments") or [])
        if kept != had:
            raise MoneyWizError(f"The trade was changed but kept {kept} of its {had} attachments; check it in MoneyWiz")
        out = {"transaction": trade,
               "holding": self._holding_of(account["id"], symbol),
               "account_balance": self.accounts.get(account["id"])["balance"]}
        if edited != transaction_id:
            out["replaced"] = transaction_id
        if account["id"] != source["id"]:
            out["previous_account"] = {"id": source["id"], "name": source["name"],
                                       "holding": self._holding_of(source["id"], symbol),
                                       "balance": self.accounts.get(source["id"])["balance"]}
        return out

    # ------------------------------------------------------------ stock split

    def split(self, *, symbol: str, old_shares, new_shares, date_: str) -> dict:
        _symbol(symbol)
        before_count = amount(old_shares, field="old_shares")
        after_count = amount(new_shares, field="new_shares")
        # MoneyWiz's split editor accepts only share counts above zero (validateFields);
        # the ratio divides the price, so a zero would put an infinite price on record.
        if before_count <= 0 or after_count <= 0:
            raise MoneyWizError("old_shares and new_shares must both be above zero, as in MoneyWiz's split editor")
        day = day_start(date_)
        rows = self.n.query("InvestmentHolding", fields=HOLDING_FIELDS,
                            where=[{"or": [["symbol", "eq", s(symbol)], ["symbolAlias", "eq", s(symbol)]]}])["items"]
        targets = [r for r in rows if (r.get("investmentObjectType") or 0) == SECURITY
                   and r.get("holdingType") != SPLIT_SKIPS_TYPE]
        if not targets:
            raise MoneyWizError(f"No investment holding has the symbol or alias {symbol!r}")
        ids = [r["gid"] for r in targets]
        before = set(self._holding_trade_ids(ids))
        self.n.invoke("manager", SPLIT, [s(symbol), d(float(after_count) / float(before_count)), date(day)])
        created = [x for x in self._holding_trade_ids(ids) if x not in before]
        after = {r["gid"]: holding_view(r) for r in self.n.query(
            "InvestmentHolding", where=[["GID", "in", arr(s(x) for x in ids)]], fields=HOLDING_FIELDS)["items"]}
        return {"symbol": symbol, "old_shares": str(before_count), "new_shares": str(after_count),
                "date": show_date(day.isoformat()),
                "transactions": [self.transactions.get(x) for x in created],
                "holdings": [after[x] for x in ids if x in after]}

    def _holding_trade_ids(self, holding_ids: list[str]) -> list[str]:
        rows = self.n.query("Transaction", where=[["investmentHolding.GID", "in", arr(s(x) for x in holding_ids)]],
                            fields=[])["items"]
        return [r["gid"] for r in rows]

    # ------------------------------------------------------------ currency exchange

    def exchange(self, *, account: str, from_currency: str, from_amount, to_currency: str, to_amount,
                 date_: str, fee="0", fee_currency: str | None = None, description: str = "", memo: str = "") -> dict:
        resolved = self.accounts.resolve(account)
        if resolved["kind"] != "forex":
            raise MoneyWizError(f"{resolved['name']!r} is a {resolved['kind']} account; "
                                "currency exchanges are recorded in forex accounts")
        source, target = currency_code(self.n, from_currency), currency_code(self.n, to_currency)
        if source == target:
            raise MoneyWizError("from_currency and to_currency are the same")  # no exchange to value
        given = amount(from_amount, field="from_amount")
        received = amount(to_amount, field="to_amount")
        cost = amount(fee, field="fee")
        fee_code = currency_code(self.n, fee_currency) if fee_currency else source
        result = self.n.invoke("manager", EXCHANGE,
                               [ref("Account", resolved["id"]), NIL, s(description), NIL, NIL, NIL,
                                date(parse_when(date_)), s(source), s(target), d(float(given)), d(float(received)),
                                d(float(cost)), s(fee_code), NIL, s(memo or "")])["value"]
        if not isinstance(result, dict) or not result.get("gid") or not self.n.get("Transaction", result["gid"]):
            raise MoneyWizError(NO_RATE)
        return {"transaction": self.transactions.get(result["gid"]),
                "holdings": self.portfolio.holdings(resolved["id"]),
                "account_balance": self.accounts.get(resolved["id"])["balance"]}

    def update_exchange(self, transaction_id: str, **changes) -> dict:
        current = self.transactions.get(transaction_id)
        if current["kind"] != "investment_exchange":
            raise MoneyWizError(f"{current['kind']} is not a currency exchange")
        unknown = set(changes) - {"from_currency", "from_amount", "to_currency", "to_amount", "date", "fee",
                                  "fee_currency", "description", "memo"}
        if unknown:
            raise MoneyWizError(f"Unknown exchange fields: {sorted(unknown)}")
        was = current["exchange"]
        source = currency_code(self.n, changes.get("from_currency", was["from_currency"]))
        target = currency_code(self.n, changes.get("to_currency", was["to_currency"]))
        if source == target:
            raise MoneyWizError("from_currency and to_currency are the same")
        fee_code = currency_code(self.n, changes.get("fee_currency", was.get("fee_currency") or source))
        row = self.transactions._row(transaction_id)
        result = self.n.invoke("manager", EDIT_EXCHANGE,
                               [ref("Transaction", transaction_id), ref("Account", current["account_id"]),
                                s(changes.get("description", current.get("description") or "")), NIL, NIL, NIL,
                                date(parse_when(changes.get("date") or row["date"])), s(source), s(target),
                                d(float(amount(changes.get("from_amount", was["from_amount"]), field="from_amount"))),
                                d(float(amount(changes.get("to_amount", was["to_amount"]), field="to_amount"))),
                                d(float(amount(changes.get("fee", was.get("fee") or "0"), field="fee"))), s(fee_code),
                                images_arg(row), s(changes.get("memo", current.get("memo") or "")), b(True)])["value"]
        edited = result["gid"] if isinstance(result, dict) and result.get("gid") else transaction_id
        if not self.n.get("Transaction", edited):
            raise MoneyWizError(NO_RATE)
        out = {"transaction": self.transactions.get(edited), "holdings": self.portfolio.holdings(current["account_id"]),
               "account_balance": self.accounts.get(current["account_id"])["balance"]}
        if edited != transaction_id:
            out["replaced"] = transaction_id
        return out

    def delete_trade(self, transaction_id: str) -> dict:
        current = self.transactions.get(transaction_id)
        if current["kind"] not in ("investment_buy", "investment_sell", "investment_exchange"):
            raise MoneyWizError(f"{current['kind']} is not an investment trade; use moneywiz_delete_transactions")
        return self.transactions.delete(transaction_id)
