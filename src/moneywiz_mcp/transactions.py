"""D02 transactions: expenses, income, transfers, refunds and their state."""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from .accounts import Accounts
from .catalog import Catalog
from .native import Native, MoneyWizError, s, n, ni, nb, b, ref, arr, date, opt_s, NIL, currency_code
from .values import amount, parse_when, parse_bound, show_date, clean, stored

# Transaction.status: MoneyWiz marks a record pending or cleared; "reconciled"
# is a separate flag, and cheque voiding a third.
PENDING, CLEARED = 1, 2
STATUS_NAMES = {PENDING: "pending", CLEARED: "cleared"}

KIND_ENTITY = {
    "expense": "WithdrawTransaction",
    "income": "DepositTransaction",
    "refund": "RefundTransaction",
    "transfer_out": "TransferWithdrawTransaction",
    "transfer_in": "TransferDepositTransaction",
    "investment_buy": "InvestmentBuyTransaction",
    "investment_sell": "InvestmentSellTransaction",
    "investment_exchange": "InvestmentExchangeTransaction",
    "reconcile": "ReconcileTransaction",
    "budget_transfer": "TransferBudgetTransaction",
}
ENTITY_KIND = {v: k for k, v in KIND_ENTITY.items()}
EDITABLE = {"expense", "income", "refund", "transfer_out", "transfer_in"}

FIELDS = ["date", "amount", "desc", "notes", "status", "reconciled", "voidCheque", "checkbookNumber",
          "originalAmount", "originalCurrency", "originalExchangeRate", "fee", "originalFee", "originalFeeCurrency",
          "numberOfShares", "pricePerShare", "symbol",
          "account.GID", "account.name", "account.currencyName", "payee.GID", "payee.name",
          "tags.GID", "tags.name", "images.GID",
          "categoriesAssigments.amount", "categoriesAssigments.category.GID", "categoriesAssigments.category.name",
          "recipientAccount.GID", "recipientAccount.name", "recipientTransaction.GID", "recipientTransaction.amount",
          "senderTransaction.GID", "senderTransaction.account.GID", "senderTransaction.account.name",
          "investmentHolding.GID", "investmentHolding.symbol",
          "withdrawTransactionsLinks.withdrawTransaction.GID", "refundTransactionsLinks.refundTransaction.GID",
          "reconcileAmount", "reconcileNumberOfShares"]

CREATE_EXPENSE = ("createWithdrawTransactionForAccount:GID:description:payeeName:originalCurrency:originalAmount:"
                  "originalExchangeRate:amount:fee:date:categories:categoriesMoney:checkbookNumber:imagesPaths:notes:"
                  "tagsNames:investmentSymbol:symbol:numberOfShares:")
CREATE_INCOME = ("createDepositTransactionForAccount:GID:description:payeeName:originalCurrency:originalExchangeRate:"
                 "originalAmount:amount:fee:date:categories:categoriesMoney:checkbookNumber:imagesPaths:notes:tagsNames:"
                 "investmentSymbol:symbol:numberOfShares:")
EDIT_EXPENSE = ("editWithdrawTransaction:account:description:payeeName:originalCurrency:originalExchangeRate:"
                "originalAmount:amount:amountCurrency:fee:date:categories:categoriesMoney:checkbookNumber:imagesPaths:"
                "notes:tagsNames:investmentSymbol:symbol:numberOfShares:")
EDIT_INCOME = ("editDepositTransaction:account:description:payeeName:originalCurrency:originalExchangeRate:"
               "originalAmount:amount:fee:date:categories:categoriesMoney:checkbookNumber:imagesPaths:notes:tagsNames:"
               "investmentSymbol:symbol:numberOfShares:")
CREATE_REFUND = ("createRefundTransactionForAccount:withdrawTransaction:description:payeeName:originalCurrency:"
                 "originalExchangeRate:originalAmount:amount:fee:date:categories:categoriesMoney:checkbookNumber:"
                 "imagesPaths:notes:tagsNames:investmentSymbol:symbol:numberOfShares:")
EDIT_REFUND = ("editRefundTransaction:withdrawTransaction:account:description:payeeName:originalCurrency:"
               "originalExchangeRate:originalAmount:amount:fee:date:categories:categoriesMoney:checkbookNumber:"
               "imagesPaths:notes:tagsNames:investmentSymbol:symbol:numberOfShares:")
CREATE_TRANSFER = ("createTransferTransactionFromAccount:toAccount:description:notes:fromAmount:fromAmountCurrency:"
                   "exchangeRate:toAmount:toAmountCurrency:fee:feeCurrency:date:receiveDate:categories:categoriesMoney:"
                   "payeeName:imagesPaths:checkbookNumber:tagsNames:fromSymbol:fromNumberOfShares:toSymbol:"
                   "toNumberOfShares:")
EDIT_TRANSFER = ("editTransferWithdrawTransaction:fromAccount:toAccount:description:notes:fromAmount:fromAmountCurrency:"
                 "exchangeRate:toAmount:toAmountCurrency:fee:feeCurrency:date:receiveDate:categories:categoriesMoney:"
                 "payeeName:imagesPaths:checkbookNumber:tagsNames:")
DELETE = "deleteTransactionWithBalanceRecalculation:notifyAccountsBalanceChange:"
# MoneyWiz's "adjust balance": it books the difference between the balance
# given and its own running balance on that date. Forex accounts adjust one
# currency position at a time.
RECONCILE = "createReconcileTransactionForAccount:description:notes:ballance:date:"
RECONCILE_POSITION = "createReconcileTransactionForAccount:description:notes:date:symbol:reconciledNumberOfShares:"
# MoneyWiz's main window answers each new transfer with a "transfer between A
# and B succeeded" dialog. This observer does nothing else, and it runs inside
# the create call, so it is skipped for transfers made through the server.
TRANSFER_NOTICE = (("iphoneMoneyWizViewController", "onTransactionCreated:"),)


def _ratio(numerator: Decimal, denominator: Decimal, what: str) -> Decimal:
    # We derive a rate from two amounts; with a zero amount there is none.
    if denominator == 0:
        raise MoneyWizError(f"Cannot derive {what} from a zero amount; give the rate explicitly")
    return numerator / denominator


def view(row: dict) -> dict:
    kind = ENTITY_KIND.get(row["$ref"], row["$ref"])
    # Parallel to-many paths are read from the same set in one main-queue turn,
    # so their element order matches; strict catches any length drift.
    categories = list(zip(row.get("categoriesAssigments.category.GID") or [],
                          row.get("categoriesAssigments.category.name") or [],
                          row.get("categoriesAssigments.amount") or [], strict=True))
    out = {
        "id": row["gid"],
        "kind": kind,
        "date": show_date(row.get("date")),
        "amount": stored(row.get("amount")),
        "currency": row.get("account.currencyName"),
        "description": row.get("desc"),
        "memo": row.get("notes") or None,
        "account_id": row.get("account.GID"),
        "account": row.get("account.name"),
        "status": STATUS_NAMES.get(row.get("status"), row.get("status")),
        "reconciled": bool(row.get("reconciled")),
        "payee": row.get("payee.name"),
        "payee_id": row.get("payee.GID"),
        "tags": row.get("tags.name") or None,
        "categories": [{"id": cid, "name": name, "amount": stored(value)} for cid, name, value in categories] or None,
        "fee": stored(row.get("fee")) if row.get("fee") else None,
        "cheque_number": row.get("checkbookNumber") or None,
        "attachments": len(row.get("images.GID") or []) or None,
    }
    if row.get("originalCurrency") and row.get("originalCurrency") != row.get("account.currencyName"):
        out["original"] = {"currency": row.get("originalCurrency"), "amount": stored(row.get("originalAmount")),
                           "exchange_rate": stored(row.get("originalExchangeRate"))}
    if row.get("voidCheque"):
        out["void"] = True
    if kind == "transfer_out":
        out["transfer_to_id"] = row.get("recipientAccount.GID")
        out["transfer_to"] = row.get("recipientAccount.name")
        out["other_leg_id"] = row.get("recipientTransaction.GID")
    if kind == "transfer_in":
        out["transfer_from_id"] = row.get("senderTransaction.account.GID")
        out["transfer_from"] = row.get("senderTransaction.account.name")
        out["other_leg_id"] = row.get("senderTransaction.GID")
    if kind == "refund":
        out["refund_of_ids"] = row.get("withdrawTransactionsLinks.withdrawTransaction.GID") or None
    if kind == "reconcile":
        if row.get("reconcileNumberOfShares") is not None and row.get("originalCurrency"):
            out["balance_set_to"] = {"currency": row["originalCurrency"],
                                     "amount": stored(row["reconcileNumberOfShares"])}
        else:
            out["balance_set_to"] = stored(row.get("reconcileAmount"))
    if row.get("refundTransactionsLinks.refundTransaction.GID"):
        out["refunded_by_ids"] = row["refundTransactionsLinks.refundTransaction.GID"]
    if row.get("investmentHolding.symbol") and kind != "reconcile":  # an adjustment states its position above
        out["symbol"] = row["investmentHolding.symbol"]
        out["shares"] = stored(row.get("numberOfShares"))
        out["price_per_share"] = stored(row.get("pricePerShare"))
    if kind == "reconcile":
        out.pop("original", None)
    return clean(out) | {"reconciled": out["reconciled"]}


class Transactions:
    def __init__(self, native: Native):
        self.n = native
        self.accounts = Accounts(native)
        self.catalog = Catalog(native)

    # ------------------------------------------------------------ reads

    def list(self, *, account: str | None = None, start: str | None = None, end: str | None = None,
             text: str | None = None, kind: str | None = None, category: str | None = None,
             payee: str | None = None, tag: str | None = None, status: str | None = None,
             min_amount=None, max_amount=None, limit: int = 50, offset: int = 0) -> dict:
        entity = "Transaction"
        if kind:
            entity = KIND_ENTITY.get(kind)
            if entity is None:
                raise MoneyWizError(f"kind must be one of {sorted(KIND_ENTITY)}")
        where = []
        if account:
            where.append(["account.GID", "eq", s(self.accounts.resolve(account)["id"])])
        if start:
            where.append(["date", "ge", date(parse_bound(start))])
        if end:
            where.append(["date", "lt", date(parse_bound(end, end=True))])
        if text:
            # MoneyWiz stores non-breaking spaces in many descriptions ("Coffee\xa0Shop"),
            # so a plain-space search has to match that variant too.
            variants = {text, text.replace(" ", "\u00a0"), text.replace("\u00a0", " ")}
            where.append({"or": [[field, "contains", s(variant)]
                                 for field in ("desc", "notes", "payee.name") for variant in sorted(variants)]})
        if category:
            resolved = self.catalog.resolve_category(category)
            where.append(["categoriesAssigments.category.GID", "eq", s(resolved["id"])])
        if payee:
            matches = [p for p in self.catalog.payees() if payee in (p["id"], p["name"])]
            if not matches:
                raise MoneyWizError(f"Payee not found: {payee!r}")
            where.append(["payee.GID", "in", arr([s(p["id"]) for p in matches])])
        if tag:
            matches = [t for t in self.catalog.tags() if tag in (t["id"], t["name"])]
            if not matches:
                raise MoneyWizError(f"Tag not found: {tag!r}")
            where.append(["tags.GID", "in", arr([s(t["id"]) for t in matches])])
        if status:
            if status == "reconciled":
                where.append(["reconciled", "eq", nb(True)])
            elif status in ("pending", "cleared"):
                where.append(["status", "eq", ni(PENDING if status == "pending" else CLEARED)])
            else:
                raise MoneyWizError("status must be pending, cleared or reconciled")
        if min_amount is not None:
            where.append(["amount", "ge", n(float(amount(min_amount, field="min_amount")))])
        if max_amount is not None:
            where.append(["amount", "le", n(float(amount(max_amount, field="max_amount")))])
        limit = max(1, min(int(limit), 500))
        result = self.n.query(entity, where=where, fields=FIELDS, sort=[["date", False]],
                              limit=limit, offset=offset, count=True)
        items = [view(r) for r in result["items"]]
        return {"total": result.get("total"), "offset": offset, "count": len(items),
                "next_offset": offset + len(items) if offset + len(items) < (result.get("total") or 0) else None,
                "transactions": items}

    def get(self, transaction_id: str) -> dict:
        row = self.n.get("Transaction", transaction_id, FIELDS)
        if row is None:
            raise MoneyWizError(f"Transaction not found: {transaction_id}")
        return view(row)

    def _row(self, transaction_id: str) -> dict:
        row = self.n.get("Transaction", transaction_id, FIELDS)
        if row is None:
            raise MoneyWizError(f"Transaction not found: {transaction_id}")
        return row

    # ------------------------------------------------------------ input helpers

    def _splits(self, categories, total: Decimal):
        """Normalize a category split list into (category refs, amounts)."""
        if not categories:
            return NIL, NIL, []
        if isinstance(categories, (str, dict)):
            categories = [categories]
        resolved, amounts = [], []
        for entry in categories:
            if isinstance(entry, str):
                entry = {"category": entry}
            if not isinstance(entry, dict) or "category" not in entry:
                raise MoneyWizError('each category entry needs {"category": "name or id", "amount": "12.00"}')
            resolved.append(self.catalog.resolve_category(entry["category"]))
            amounts.append(amount(entry["amount"], field="category amount") if entry.get("amount") is not None else None)
        if len(resolved) == 1 and amounts[0] is None:
            amounts[0] = total
        if any(value is None for value in amounts):
            raise MoneyWizError("Give an amount for every category when splitting a transaction")
        return (arr(ref("Category", c["id"]) for c in resolved), arr(n(float(v)) for v in amounts),
                [{"id": c["id"], "name": c["path"], "amount": str(v)} for c, v in zip(resolved, amounts, strict=True)])

    def _payee(self, payee: str | None) -> dict:
        if payee is None:
            return NIL
        if not isinstance(payee, str):
            raise MoneyWizError("payee must be a name")
        for known in self.catalog.payees():
            if payee == known["id"]:
                return s(known["name"])
        return s(payee)  # MoneyWiz creates the payee when the name is new

    def _tags(self, tags) -> dict:
        if not tags:
            return NIL
        if isinstance(tags, str):
            tags = [tags]
        known = {t["id"]: t["name"] for t in self.catalog.tags()}
        return arr(s(known.get(t, t)) for t in tags)

    def _fx(self, account: dict, value: Decimal, original_amount, original_currency, exchange_rate):
        """Return (originalCurrency, originalAmount, originalExchangeRate) for the business call."""
        if original_amount is None and original_currency is None:
            return s(account["currency"]), n(float(value)), n(1.0), None
        if original_currency is None or original_amount is None:
            raise MoneyWizError("Give original_currency together with original_amount for a foreign-currency entry")
        currency = currency_code(self.n, original_currency)
        if currency == account["currency"]:
            return s(account["currency"]), n(float(value)), n(1.0), None
        foreign = amount(original_amount, field="original_amount")
        rate = amount(exchange_rate, field="exchange_rate") if exchange_rate is not None \
            else _ratio(value, foreign, "an exchange rate")
        return (s(currency), n(float(foreign)), n(float(rate)),
                {"currency": currency, "amount": str(foreign), "exchange_rate": str(rate)})

    def _created(self, result, expect_kind: str | None = None) -> dict:
        value = result.get("value")
        if not value or not value.get("gid"):
            raise MoneyWizError("MoneyWiz did not create the transaction")
        found = self.get(value["gid"])
        if expect_kind and found["kind"] != expect_kind:
            raise MoneyWizError(f"MoneyWiz created a {found['kind']} instead of a {expect_kind}")
        return found

    # ------------------------------------------------------------ create

    def create(self, *, account: str, kind: str, amount_: str, date_: str, description: str = "",
               memo: str = "", payee: str | None = None, categories=None, tags=None,
               fee=None, cheque_number: str | None = None, original_amount=None, original_currency: str | None = None,
               exchange_rate=None, refund_of: str | None = None, transfer_to: str | None = None,
               transfer_amount=None, transfer_exchange_rate=None, receive_date: str | None = None,
               status: str | None = None) -> dict:
        if kind not in ("expense", "income", "transfer", "refund"):
            raise MoneyWizError("kind must be expense, income, transfer or refund")
        source = self.accounts.resolve(account)
        value = amount(amount_, field="amount")
        when = parse_when(date_)
        fee_value = amount(fee, field="fee") if fee is not None else Decimal(0)
        if kind == "transfer":
            return self._create_transfer(source, value, when, description, memo, payee, tags, fee_value,
                                         cheque_number, transfer_to, transfer_amount, transfer_exchange_rate,
                                         categories, status,
                                         parse_when(receive_date, field="receive_date") if receive_date else None)
        currency, original, rate, original_view = self._fx(source, value, original_amount, original_currency, exchange_rate)
        cats, monies, _ = self._splits(categories, value)
        tail = [cats, monies, opt_s(cheque_number), NIL, s(memo or ""), self._tags(tags), NIL, NIL, NIL]
        if kind == "expense":
            args = [ref("Account", source["id"]), NIL, s(description), self._payee(payee), currency, original, rate,
                    n(float(value)), n(float(fee_value)), date(when), *tail]
            created = self._created(self.n.invoke("manager", CREATE_EXPENSE, args), "expense")
        elif kind == "income":
            args = [ref("Account", source["id"]), NIL, s(description), self._payee(payee), currency, rate, original,
                    n(float(value)), n(float(fee_value)), date(when), *tail]
            created = self._created(self.n.invoke("manager", CREATE_INCOME, args), "income")
        else:
            if not refund_of:
                raise MoneyWizError("A refund needs refund_of: the id of the expense being refunded")
            original_expense = self._row(refund_of)
            if ENTITY_KIND.get(original_expense["$ref"]) != "expense":
                raise MoneyWizError("refund_of must be the id of an expense transaction")
            args = [ref("Account", source["id"]), ref("Transaction", refund_of), s(description), self._payee(payee),
                    currency, rate, original, n(float(value)), n(float(fee_value)), date(when), *tail]
            created = self._created(self.n.invoke("manager", CREATE_REFUND, args), "refund")
        if status:
            created = self.set_status(created["id"], status)
        return created

    def _create_transfer(self, source, value, when, description, memo, payee, tags, fee_value, cheque_number,
                         transfer_to, transfer_amount, transfer_exchange_rate, categories, status, receive=None) -> dict:
        if not transfer_to:
            raise MoneyWizError("A transfer needs transfer_to: the destination account")
        target = self.accounts.resolve(transfer_to)
        if target["id"] == source["id"]:
            raise MoneyWizError("Transfer source and destination must differ")
        if transfer_amount is not None:
            received = amount(transfer_amount, field="transfer_amount")
            rate = Decimal(1) if received == value else _ratio(received, value, "the transfer rate")
        elif transfer_exchange_rate is not None:
            rate = amount(transfer_exchange_rate, field="transfer_exchange_rate")
            received = value * rate
        elif target["currency"] == source["currency"]:
            received, rate = value, Decimal(1)
        else:
            raise MoneyWizError(f"{source['currency']}→{target['currency']} transfer needs transfer_amount "
                                "(the amount received) or transfer_exchange_rate")
        cats, monies, _ = self._splits(categories, value)
        args = [ref("Account", source["id"]), ref("Account", target["id"]), s(description), s(memo or ""),
                n(float(value)), s(source["currency"]), n(float(rate)), n(float(received)), s(target["currency"]),
                n(float(fee_value)), s(source["currency"]), date(when), date(receive or when), cats, monies,
                self._payee(payee), NIL, opt_s(cheque_number), self._tags(tags), NIL, NIL, NIL, NIL]
        created = self._created(self.n.invoke("manager", CREATE_TRANSFER, args, quiet=TRANSFER_NOTICE))
        if created["kind"] not in ("transfer_out", "transfer_in"):
            raise MoneyWizError(f"MoneyWiz created a {created['kind']} instead of a transfer")
        if status:
            created = self.set_status(created["id"], status)
        return created

    # ------------------------------------------------------------ update

    def update(self, transaction_id: str, **changes) -> dict:
        row = self._row(transaction_id)
        current = view(row)
        kind = current["kind"]
        if kind not in EDITABLE:
            if kind == "reconcile":
                raise MoneyWizError("A balance adjustment is not edited in place: delete it and call "
                                    "moneywiz_adjust_balance again (MoneyWiz's editor does the same)")
            raise MoneyWizError(f"{kind} transactions cannot be edited through this tool yet "
                                "(investment trades use moneywiz_update_trade; budget transfers are read-only)")
        allowed = {"amount", "date", "description", "memo", "payee", "categories", "tags", "fee", "cheque_number",
                   "account", "original_amount", "original_currency", "exchange_rate", "transfer_to",
                   "transfer_amount", "transfer_exchange_rate", "receive_date"}
        unknown = set(changes) - allowed
        if unknown:
            raise MoneyWizError(f"Unknown transaction fields: {sorted(unknown)}")
        if kind == "transfer_in":
            # Both legs are one business record; edit it from the sending side.
            if not current.get("other_leg_id"):
                raise MoneyWizError("This transfer has no sending leg to edit")
            return self.update(current["other_leg_id"], **changes)

        account = self.accounts.resolve(changes["account"]) if changes.get("account") else \
            self.accounts.resolve(current["account_id"])
        value = amount(changes["amount"], field="amount") if "amount" in changes else \
            abs(Decimal(current["amount"]))
        when = parse_when(changes["date"]) if changes.get("date") else parse_when(current["date"])
        description = changes.get("description", current.get("description") or "")
        memo = changes.get("memo", current.get("memo") or "")
        fee_value = amount(changes["fee"], field="fee") if changes.get("fee") is not None else \
            Decimal(current.get("fee") or 0)
        cheque = changes.get("cheque_number", current.get("cheque_number"))
        payee = changes.get("payee", current.get("payee"))
        tags = changes.get("tags", current.get("tags"))
        if "categories" in changes:
            categories = changes["categories"]
        else:
            categories = [{"category": c["id"], "amount": str(abs(Decimal(c["amount"])))}
                          for c in current.get("categories") or []]
            # As in the app, a single category follows the new amount; a split
            # keeps its amounts unless a new list is given.
            if len(categories) == 1 and value != abs(Decimal(current["amount"])):
                categories[0]["amount"] = str(value)

        if kind == "transfer_out":
            target = self.accounts.resolve(changes["transfer_to"]) if changes.get("transfer_to") else \
                self.accounts.resolve(current["transfer_to_id"])
            other = self.n.get("Transaction", current["other_leg_id"], ["amount", "date"]) if current.get("other_leg_id") else None
            received_now = abs(Decimal(stored(other["amount"]))) if other else value
            received_when = parse_when(changes["receive_date"], field="receive_date") if changes.get("receive_date") \
                else (parse_when(show_date(other["date"])) if other else when)
            if changes.get("transfer_amount") is not None:
                received = amount(changes["transfer_amount"], field="transfer_amount")
            elif changes.get("transfer_exchange_rate") is not None:
                received = value * amount(changes["transfer_exchange_rate"], field="transfer_exchange_rate")
            elif target["currency"] == account["currency"]:
                received = value
            elif "amount" in changes:
                raise MoneyWizError("Changing a cross-currency transfer amount needs transfer_amount or transfer_exchange_rate")
            else:
                received = received_now
            rate = Decimal(1) if received == value else _ratio(received, value, "the transfer rate")
            cats, monies, _ = self._splits(categories, value)
            args = [ref("Transaction", transaction_id), ref("Account", account["id"]), ref("Account", target["id"]),
                    s(description), s(memo), n(float(value)), s(account["currency"]), n(float(rate)),
                    n(float(received)), s(target["currency"]), n(float(fee_value)), s(account["currency"]),
                    date(when), date(received_when), cats, monies, self._payee(payee), NIL, opt_s(cheque),
                    self._tags(tags)]
            self.n.invoke("manager", EDIT_TRANSFER, args, quiet=TRANSFER_NOTICE)
            return self.get(transaction_id)

        fx_source = {"currency": account["currency"]}
        if "original_amount" in changes or "original_currency" in changes or "exchange_rate" in changes:
            original_amount = changes.get("original_amount", (current.get("original") or {}).get("amount"))
            original_currency = changes.get("original_currency", (current.get("original") or {}).get("currency"))
            exchange_rate = changes.get("exchange_rate")
        elif current.get("original") and "amount" not in changes:
            original_amount = current["original"]["amount"]
            original_currency = current["original"]["currency"]
            exchange_rate = current["original"]["exchange_rate"]
        else:
            original_amount = original_currency = exchange_rate = None
        currency, original, rate, _ = self._fx(fx_source, value, original_amount, original_currency, exchange_rate)
        cats, monies, _ = self._splits(categories, value)
        tail = [cats, monies, opt_s(cheque), NIL, s(memo), self._tags(tags), NIL, NIL, NIL]
        if kind == "expense":
            args = [ref("Transaction", transaction_id), ref("Account", account["id"]), s(description),
                    self._payee(payee), currency, rate, original, n(float(value)), s(account["currency"]),
                    n(float(fee_value)), date(when), *tail]
            self.n.invoke("manager", EDIT_EXPENSE, args)
        elif kind == "income":
            args = [ref("Transaction", transaction_id), ref("Account", account["id"]), s(description),
                    self._payee(payee), currency, rate, original, n(float(value)), n(float(fee_value)),
                    date(when), *tail]
            self.n.invoke("manager", EDIT_INCOME, args)
        else:
            links = current.get("refund_of_ids") or []
            if len(links) != 1:
                raise MoneyWizError("This refund is linked to several expenses and cannot be edited here")
            args = [ref("Transaction", transaction_id), ref("Transaction", links[0]), ref("Account", account["id"]),
                    s(description), self._payee(payee), currency, rate, original, n(float(value)),
                    n(float(fee_value)), date(when), *tail]
            self.n.invoke("manager", EDIT_REFUND, args)
        return self.get(transaction_id)

    # ------------------------------------------------------------ balance adjustment

    def adjust_balance(self, *, account: str, balance, date_: str, description: str = "", memo: str = "",
                       currency: str | None = None) -> dict:
        """Set an account's balance on a date, as MoneyWiz's "adjust balance" does."""
        target = self.accounts.resolve(account)
        value = amount(balance, field="balance")
        # A bare day means its end, so the whole day counts, as for a statement's closing balance.
        when = parse_bound(date_, end=True, field="date") - timedelta(seconds=1) \
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_ or "") else parse_when(date_)
        if target["kind"] == "forex":
            if not currency:
                raise MoneyWizError("A forex account holds a balance per currency; give currency")
            code = currency_code(self.n, currency)
            where = [["investmentAccount.GID", "eq", s(target["id"])], ["symbol", "eq", s(code)]]
            existed = bool(self.n.query("InvestmentHolding", where=where, fields=[])["items"])
            args = [ref("Account", target["id"]), s(description or ""), s(memo or ""), date(when), s(code),
                    n(float(value))]
            result = self.n.invoke("manager", RECONCILE_POSITION, args, quiet=TRANSFER_NOTICE)
            # MoneyWiz prices a position it has just created from its exchange rate,
            # in a block that runs once its main queue is free (seconds later while
            # it is busy), and leaves that unsaved, which would hold up every later
            # write. Wait for that change and save it, and only it.
            positions = self.n.query("InvestmentHolding", where=where, fields=[])["items"]
            if positions:
                self.n.settle([("InvestmentHolding", p["gid"]) for p in positions], wait=0.5 if existed else 15.0)
        else:
            if currency and currency_code(self.n, currency) != target["currency"]:
                raise MoneyWizError(f"{target['name']!r} keeps its balance in {target['currency']}")
            args = [ref("Account", target["id"]), s(description or ""), s(memo or ""), n(float(value)), date(when)]
            result = self.n.invoke("manager", RECONCILE, args, quiet=TRANSFER_NOTICE)
        created = self._created(result, "reconcile")
        return {"transaction": created, "account_balance": self.accounts.get(target["id"])["balance"]}

    # ------------------------------------------------------------ state & delete

    def set_status(self, transaction_id: str, status: str) -> dict:
        current = self.get(transaction_id)
        if status in ("cleared", "pending"):
            if current["status"] != status:
                self.n.invoke("manager", "clearTransaction:" if status == "cleared" else "unclearTransaction:",
                              [ref("Transaction", transaction_id)])
        elif status in ("reconciled", "unreconciled"):
            want = status == "reconciled"
            if current["reconciled"] != want:
                self.n.invoke("manager", "setTransactionReconciled:reconciled:clearIfPendingAndReconciled:",
                              [ref("Transaction", transaction_id), b(want), b(True)])
        elif status in ("void", "unvoid"):
            want = status == "void"
            if bool(current.get("void")) != want:
                self.n.invoke("manager", "updateTransaction:setVoidCheque:", [ref("Transaction", transaction_id), b(want)])
        else:
            raise MoneyWizError("status must be cleared, pending, reconciled, unreconciled, void or unvoid")
        return self.get(transaction_id)

    def delete(self, transaction_id: str) -> dict:
        current = self.get(transaction_id)
        removed = [transaction_id]
        if current.get("other_leg_id"):
            removed.append(current["other_leg_id"])
        self.n.invoke("manager", DELETE, [ref("Transaction", transaction_id), b(True)])
        still = [x for x in removed if self.n.get("Transaction", x)]
        if still:
            raise MoneyWizError(f"Transaction still exists after delete: {still}")
        return {"deleted": removed, "description": current.get("description"), "amount": current.get("amount"),
                "account": current.get("account")}
