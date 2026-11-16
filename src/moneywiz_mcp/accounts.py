"""D01 accounts and account groups."""
from __future__ import annotations

from .native import Native, MoneyWizError, s, n, ni, nb, i, b, ref, arr, cls, date, decode, opt_s, NIL, USER, currency_code
from .values import amount, clean, find_one, money, parse_when

# MoneyWiz class per account kind, with its create/edit business methods.
KINDS = {
    "cash": ("CashAccount", "createCashAccountForUser:name:desc:openingBalance:currencyName:balanceDisplayMode:groupID:"
                            "autoclearTransactions:enableCheckbookRegister:budgets:includeInNetworth:includeInGroupBalance:"
                            "iconFileName:alertBalanceFallUnder:alertCreditCardsExpire:",
             "editCashAccount:name:desc:openingBalance:currencyName:balanceDisplayMode:groupID:autoclearTransactions:"
             "enableCheckbookRegister:budgets:includeInNetworth:includeInGroupBalance:iconFileName:alertBalanceFallUnder:"
             "alertCreditCardsExpire:autoCategorizeOBTransactions:autoCreateNewOBCategories:autoDetectOBTransactionsPayees:"
             "autoCreateNewOBPayees:onlineBalanceFetchMode:"),
    "checking": ("BankChequeAccount", "createBankChequeAccountForUser:name:desc:openingBalance:currencyName:balanceDisplayMode:"
                                      "groupID:autoclearTransactions:enableCheckbookRegister:budgets:includeInNetworth:"
                                      "includeInGroupBalance:iconFileName:alertBalanceFallUnder:alertCreditCardsExpire:",
                 "editBankChequeAccount:name:desc:openingBalance:currencyName:balanceDisplayMode:groupID:autoclearTransactions:"
                 "enableCheckbookRegister:budgets:includeInNetworth:includeInGroupBalance:iconFileName:alertBalanceFallUnder:"
                 "alertCreditCardsExpire:autoCategorizeOBTransactions:autoCreateNewOBCategories:autoDetectOBTransactionsPayees:"
                 "autoCreateNewOBPayees:onlineBalanceFetchMode:"),
    "savings": ("BankSavingAccount", "createBankSavingsAccountForUser:name:desc:openingBalance:currencyName:balanceDisplayMode:"
                                     "groupID:autoclearTransactions:enableCheckbookRegister:budgets:includeInNetworth:"
                                     "includeInGroupBalance:iconFileName:alertBalanceFallUnder:alertCreditCardsExpire:",
                "editBankSavingAccount:name:desc:openingBalance:currencyName:balanceDisplayMode:groupID:autoclearTransactions:"
                "enableCheckbookRegister:budgets:includeInNetworth:includeInGroupBalance:iconFileName:alertBalanceFallUnder:"
                "alertCreditCardsExpire:autoCategorizeOBTransactions:autoCreateNewOBCategories:autoDetectOBTransactionsPayees:"
                "autoCreateNewOBPayees:onlineBalanceFetchMode:"),
    "credit_card": ("CreditCardAccount", "createCreditCardAccountForUser:name:desc:creditLimit:openingBalance:currencyName:"
                                         "balanceDisplayMode:balanceCreditDisplayMode:groupID:autoclearTransactions:"
                                         "enableCheckbookRegister:budgets:includeInNetworth:includeInGroupBalance:iconFileName:"
                                         "alertBalanceFallUnder:alertCreditCardsExpire:statementEndDay:weekendOption:",
                    "editCreditCardAccount:name:desc:creditLimit:openingBalance:currencyName:balanceDisplayMode:"
                    "balanceCreditDisplayMode:groupID:autoclearTransactions:enableCheckbookRegister:budgets:includeInNetworth:"
                    "includeInGroupBalance:iconFileName:alertBalanceFallUnder:alertCreditCardsExpire:autoCategorizeOBTransactions:"
                    "autoCreateNewOBCategories:autoDetectOBTransactionsPayees:autoCreateNewOBPayees:onlineBalanceFetchMode:"
                    "statementEndDay:weekendOption:"),
    "investment": ("InvestmentAccount", "createInvestmentAccountForUser:name:desc:cash:cashDate:currencyName:groupID:"
                                        "autoclearTransactions:enableCheckbookRegister:budgets:includeInNetworth:"
                                        "includeInGroupBalance:iconFileName:initialTradesTransactions:",
                   "editInvestmentAccount:name:desc:cash:cashDate:currencyName:groupID:autoclearTransactions:"
                   "enableCheckbookRegister:includeInNetworth:includeInGroupBalance:iconFileName:autoCategorizeOBTransactions:"
                   "autoCreateNewOBCategories:autoDetectOBTransactionsPayees:autoCreateNewOBPayees:onlineBalanceFetchMode:"),
    "forex": ("ForexAccount", "createForexAccountForUser:name:desc:currencyName:groupID:autoclearTransactions:"
                              "enableCheckbookRegister:budgets:includeInNetworth:includeInGroupBalance:iconFileName:"
                              "initialTradesTransactions:",
              "editForexAccount:name:desc:currencyName:groupID:autoclearTransactions:enableCheckbookRegister:"
              "includeInNetworth:includeInGroupBalance:iconFileName:autoCategorizeOBTransactions:autoCreateNewOBCategories:"
              "autoDetectOBTransactionsPayees:autoCreateNewOBPayees:onlineBalanceFetchMode:"),
}
CLASS_KIND = {cls: kind for kind, (cls, *_) in KINDS.items()}
CLASS_KIND["LoanAccount"] = "loan"

NO_GROUP = -1
BALANCE_DISPLAY_DEFAULT = 3  # value used by every account in the observed ledger
# A new credit card's second display mode (balanceCreditDisplayMode, stored as
# infoDisplayMode): what every card MoneyWiz's own editor made in the observed ledger holds.
CREDIT_INFO_DISPLAY_DEFAULT = 0
# The delete dialog's "convert transfers" (MWAlertViewHelper confirmDeleteAccount:…):
# runs before deleteAccount:, so the other accounts keep their balances.
CONVERT_TRANSFERS = "convertAccountTransfersToExpensesAndIncomes:"

FIELDS = ["name", "info", "currencyName", "archived", "openingBalance", "displayOrder", "groupID", "iconFileName",
          "includeInNetworth", "includeInGroupBalance", "clearTransactionOnCreation", "enableCheckbookRegister",
          "alertBalanceFallUnderAmount", "alertCreditCardsExpire", "creditLimit", "statementEndDay", "weekendOption",
          "balanceDisplayMode", "balanceDictionary"]
# What an edit hands back unchanged. MoneyWiz's edit methods set every setting they
# take, so one not passed back as stored is changed (online-banking switches turned
# off, the cash date moved to now).
EDIT_FIELDS = FIELDS + ["autoCategorizeOBTransactions", "autoCreateNewOBCategories", "autoDetectOBTransactionsPayees",
                        "autoCreateNewOBPayees", "onlineBankBalanceFetchMode", "infoDisplayMode", "cashAsOfDate"]
GROUP_FIELDS = ["name", "groupId", "groupObjectsType", "displayOrder", "colorNum", "groupCurrency", "isCollapsed"]
ACCOUNT_GROUP_TYPE = 0


def view(row: dict) -> dict:
    balance = row.get("balanceDictionary") or {}
    kind = CLASS_KIND.get(row["$ref"], row["$ref"])
    out = {
        "id": row["gid"], "name": row.get("name"), "kind": kind, "currency": row.get("currencyName"),
        "archived": bool(row.get("archived")),
        "balance": money(balance.get("BalanceNumber")),
        "available_balance": balance.get("AvailableBalance"),
        "opening_balance": money(row.get("openingBalance")),
        "group_id": None if row.get("groupID") in (None, NO_GROUP) else row.get("groupID"),
        "display_order": row.get("displayOrder"),
        "note": row.get("info") or None,
        "include_in_networth": bool(row.get("includeInNetworth")),
        "include_in_group_balance": bool(row.get("includeInGroupBalance")),
        "auto_clear_transactions": bool(row.get("clearTransactionOnCreation")),
        "checkbook_register": bool(row.get("enableCheckbookRegister")),
        "icon": row.get("iconFileName") or None,
        "low_balance_alert": money(row.get("alertBalanceFallUnderAmount")),
    }
    if kind in ("credit_card", "loan"):
        out["credit_limit"] = money(row.get("creditLimit"))
        out["statement_end_day"] = row.get("statementEndDay")
    return clean(out) | {"archived": out["archived"]}


# What a list of accounts shows of each; moneywiz_get_account shows every setting.
BRIEF = ("id", "name", "kind", "currency", "balance", "group_id", "credit_limit")


def brief(account: dict) -> dict:
    out = {k: account[k] for k in BRIEF if account.get(k) is not None}
    return out | {"archived": True} if account.get("archived") else out


class Accounts:
    def __init__(self, native: Native):
        self.n = native

    # ------------------------------------------------------------ reads

    def list(self, *, include_archived: bool = False, kind: str | None = None, group_id: int | None = None) -> list[dict]:
        where = []
        if not include_archived:
            where.append(["archived", "ne", nb(True)])
        if group_id is not None:
            where.append(["groupID", "eq", ni(group_id)])
        entity = KINDS[kind][0] if kind and kind in KINDS else ("LoanAccount" if kind == "loan" else "Account")
        rows = self.n.query(entity, where=where, fields=FIELDS, sort=[["displayOrder", True], ["name", True]])["items"]
        return [view(r) for r in rows]

    def get(self, account_id: str) -> dict:
        row = self.n.get("Account", account_id, FIELDS)
        if row is None:
            raise MoneyWizError(f"Account not found: {account_id}")
        return view(row)

    def _stored(self, account_id: str) -> dict:
        """The account as stored, undecoded ({"$date": seconds}), so an edit can hand back bit for bit."""
        rows = self.n.query("Account", where=[["GID", "eq", s(account_id)]], fields=EDIT_FIELDS, limit=2, raw=True)["items"]
        if len(rows) != 1:
            raise MoneyWizError(f"Account not found: {account_id}" if not rows else f"Duplicate GID {account_id}")
        return rows[0]

    def resolve(self, value: str) -> dict:
        """Accept an account id or an exact/unique name."""
        row = self.n.get("Account", value, FIELDS)
        if row:
            return view(row)
        rows = self.n.query("Account", where=[["name", "eq", s(value)]], fields=FIELDS)["items"]
        if len(rows) == 1:
            return view(rows[0])
        if rows:
            raise MoneyWizError(f"Several accounts are named {value!r}; use the account id")
        raise MoneyWizError(f"Account not found: {value!r}")

    def groups(self) -> list[dict]:
        rows = self.n.query("Group", where=[["groupObjectsType", "eq", ni(ACCOUNT_GROUP_TYPE)]],
                            fields=GROUP_FIELDS, sort=[["displayOrder", True]])["items"]
        return [clean({"id": r["gid"], "group_id": r.get("groupId"), "name": r.get("name"),
                       "currency": r.get("groupCurrency"), "color": r.get("colorNum"),
                       "display_order": r.get("displayOrder"), "collapsed": bool(r.get("isCollapsed"))}) for r in rows]

    def group(self, group_ref: str | int) -> dict:
        return find_one(self.groups(), group_ref, "account group")

    # ------------------------------------------------------------ writes

    def _check_name(self, name: str, account_id: str | None = None):
        """MoneyWiz's own account-name rule (DataValidatorHelper): not empty and,
        ignoring case, not used by another account."""
        if not isinstance(name, str):
            raise MoneyWizError("name: text required")
        validator = cls("DataValidatorHelper")
        if account_id is None:
            ok = self.n.ask(validator, "validateAccountName:", [s(name)])
        else:
            ok = self.n.ask(validator, "validateAccountEditName:forAccount:", [s(name), ref("Account", account_id)])
        if not ok:
            raise MoneyWizError(f"MoneyWiz rejects the account name {name!r}: it is empty or another account uses it")

    def create(self, *, name: str, kind: str, currency: str, opening_balance="0", note: str = "",
               group_id: int | None = None, icon: str | None = None, include_in_networth: bool = True,
               include_in_group_balance: bool = True, auto_clear_transactions: bool = True,
               checkbook_register: bool = False, credit_limit=None, statement_end_day: int | None = None,
               opening_date: str | None = None) -> dict:
        if kind not in KINDS:
            raise MoneyWizError(f"kind must be one of {sorted(KINDS)} (creating loan accounts is not supported yet, see STATUS.md)")
        self._check_name(name)
        currency = currency_code(self.n, currency)
        group = ni(self.group(group_id)["group_id"]) if group_id is not None else ni(NO_GROUP)
        opening = amount(opening_balance, field="opening_balance")
        common_tail = [group, b(auto_clear_transactions), b(checkbook_register), NIL,
                       b(include_in_networth), b(include_in_group_balance), opt_s(icon)]
        entity, create_selector, _ = KINDS[kind]
        if kind == "credit_card":
            limit = amount(credit_limit if credit_limit is not None else 0, field="credit_limit")
            args = [USER, s(name), s(note), n(float(limit)), n(float(opening)), s(currency), i(BALANCE_DISPLAY_DEFAULT),
                    i(CREDIT_INFO_DISPLAY_DEFAULT), *common_tail, NIL, b(False),
                    ni(statement_end_day or 1), i(0)]  # statementEndDay is an NSNumber, weekendOption an int
        elif kind == "investment":
            when = parse_when(opening_date) if opening_date else None
            args = [USER, s(name), s(note), n(float(opening)), date(when) if when else NIL, s(currency), *common_tail, NIL]
        elif kind == "forex":
            if opening != 0:
                raise MoneyWizError("Forex accounts start empty; add holdings or trades instead of an opening balance")
            args = [USER, s(name), s(note), s(currency), *common_tail, NIL]
        else:
            args = [USER, s(name), s(note), n(float(opening)), s(currency), i(BALANCE_DISPLAY_DEFAULT),
                    *common_tail, NIL, b(False)]
        result = self.n.invoke("manager", create_selector, args)["value"]
        if not result:
            raise MoneyWizError("MoneyWiz did not create the account")
        return self.get(result["gid"])

    def update(self, account_id: str, **changes) -> dict:
        """Change some settings; every other one MoneyWiz's edit method takes is handed
        back exactly as stored. Those methods set all they are given: budgets left out
        stop monitoring the account (a budget left monitoring nothing is deleted), and
        settings left out are reset."""
        unknown = set(changes) - {"name", "note", "opening_balance", "group_id", "icon", "include_in_networth",
                                  "include_in_group_balance", "auto_clear_transactions", "checkbook_register",
                                  "credit_limit", "statement_end_day", "low_balance_alert"}
        if unknown:
            raise MoneyWizError(f"Unknown account fields: {sorted(unknown)}")
        stored = self._stored(account_id)
        row = decode(stored)
        current = view(row)
        kind = current["kind"]
        if kind == "loan":
            raise MoneyWizError("Editing loan accounts is not supported yet (see STATUS.md)")
        name = changes.get("name", row.get("name"))
        if name != row.get("name"):
            self._check_name(name, account_id)
        group_raw = changes.get("group_id", current.get("group_id"))
        group = ni(self.group(group_raw)["group_id"]) if group_raw is not None else ni(NO_GROUP)

        def number(change: str, column: str, default=None) -> dict:
            """NSNumber: the new value, else the stored one as it is (nil stays nil)."""
            if change in changes:
                value = changes[change]
                return NIL if value is None else n(float(amount(value, field=change)))
            value = row.get(column)
            value = default if value is None else value
            return NIL if value is None else n(float(value))

        def flag(change: str, column: str) -> dict:
            return b(bool(changes[change]) if change in changes else bool(row.get(column)))

        def integer(column: str, default: int = 0) -> dict:
            return i(int(row[column]) if row.get(column) is not None else default)

        head = [ref("Account", account_id), s(name), s(changes.get("note", row.get("info") or ""))]
        clearing = [flag("auto_clear_transactions", "clearTransactionOnCreation"),
                    flag("checkbook_register", "enableCheckbookRegister")]
        totals = [flag("include_in_networth", "includeInNetworth"),
                  flag("include_in_group_balance", "includeInGroupBalance")]
        icon = opt_s(changes.get("icon", row.get("iconFileName")))
        alerts = [number("low_balance_alert", "alertBalanceFallUnderAmount"), b(bool(row.get("alertCreditCardsExpire")))]
        online = [b(bool(row.get(k))) for k in ("autoCategorizeOBTransactions", "autoCreateNewOBCategories",
                                                 "autoDetectOBTransactionsPayees", "autoCreateNewOBPayees")] \
            + [integer("onlineBankBalanceFetchMode")]
        currency = s(row["currencyName"])
        opening = number("opening_balance", "openingBalance", 0)
        _, _, edit_selector = KINDS[kind]
        if kind in ("investment", "forex"):
            cash = [opening, {"date": stored["cashAsOfDate"]["$date"]} if stored.get("cashAsOfDate") else NIL] \
                if kind == "investment" else []
            args = [*head, *cash, currency, group, *clearing, *totals, icon, *online]
        else:
            # The budgets that monitor it, as MoneyWiz's setBudgets:toAccount: compares them.
            budgets = arr(ref("Budget", x["gid"]) for x in self.n.ask("manager", "budgetsForAccount:",
                                                                       [ref("Account", account_id)]) or [])
            display = integer("balanceDisplayMode", BALANCE_DISPLAY_DEFAULT)
            if kind == "credit_card":
                end_day = changes["statement_end_day"] if "statement_end_day" in changes else row.get("statementEndDay")
                args = [*head, number("credit_limit", "creditLimit", 0), opening, currency, display,
                        integer("infoDisplayMode", CREDIT_INFO_DISPLAY_DEFAULT), group, *clearing, budgets, *totals,
                        icon, *alerts, *online, ni(end_day if end_day is not None else 1), integer("weekendOption")]
            else:
                args = [*head, opening, currency, display, group, *clearing, budgets, *totals, icon, *alerts, *online]
        self.n.invoke("manager", edit_selector, args)
        return self.get(account_id)

    def set_archived(self, account_id: str, archived: bool) -> dict:
        current = self.get(account_id)
        if current["archived"] == archived:
            return current
        self.n.invoke("manager", "archiveAccount:" if archived else "unArchiveAccount:", [ref("Account", account_id)])
        return self.get(account_id)

    def delete(self, account_id: str, *, confirm_transactions: int | None = None,
               convert_transfers: bool = False) -> dict:
        current = self.get(account_id)
        count = self.n.count("Transaction", [["account.GID", "eq", s(account_id)]])
        if count and confirm_transactions != count:
            raise MoneyWizError(
                f"Account {current['name']!r} still holds {count} transactions, which would be deleted with it. "
                f"Pass confirm_transactions={count} to proceed, or archive the account instead.")
        transfers = sum(self.n.count(entity, [["account.GID", "eq", s(account_id)]])
                        for entity in ("TransferWithdrawTransaction", "TransferDepositTransaction"))
        # MoneyWiz's "convert transfers" when deleting an account: the other side
        # of each transfer becomes an ordinary income or expense of that account.
        if convert_transfers and transfers and \
                self.n.invoke("manager", CONVERT_TRANSFERS, [ref("Account", account_id)])["value"] is False:
            raise MoneyWizError("MoneyWiz reported that it could not convert the account's transfers; "
                                "the account was not deleted (check its transfers in MoneyWiz)")
        result = self.n.invoke("manager", "deleteAccount:", [ref("Account", account_id)])
        if result["value"] is False:
            raise MoneyWizError("MoneyWiz refused to delete the account")
        if self.n.get("Account", account_id):
            raise MoneyWizError("Account still exists after delete")
        out = {"deleted": account_id, "name": current["name"], "deleted_transactions": count}
        if transfers:
            out["converted_transfers" if convert_transfers else "deleted_transfers"] = transfers
        return out

    def move(self, account_id: str, *, group_id: int | None = ..., display_order: int | None = None) -> dict:
        """Move an account to a position and/or another group.

        display_order is the position among the target group's active accounts.
        MoneyWiz's updateAccount:withDisplayOrder:andGroup: only counts active
        siblings (its filter block is `!archived`) and renumbers them 0..n, as a
        drag in the app does: the target group's, and on a group change also
        those of the group the account leaves. Archived accounts keep their values.
        """
        current = self.get(account_id)
        target_group = current.get("group_id") if group_id is ... else group_id
        changing_group = target_group != current.get("group_id")
        if display_order is None:
            if not changing_group:
                raise MoneyWizError("Give display_order (position among the group's active accounts) or another group")
            # Append after the target group's active accounts, as unarchiving does,
            # instead of carrying over the position it had in the old group.
            display_order = self.n.count("Account", [["groupID", "eq", ni(NO_GROUP if target_group is None else target_group)],
                                                     ["archived", "ne", nb(True)], ["GID", "ne", s(account_id)]])
        # MoneyWiz clamps an out-of-range position to the ends of the list.
        if not isinstance(display_order, int):
            raise MoneyWizError("display_order must be an integer")
        group = ref("Group", self.group(target_group)["id"]) if target_group is not None else NIL
        self.n.invoke("manager", "updateAccount:withDisplayOrder:andGroup:",
                      [ref("Account", account_id), ni(display_order), group])
        return self.get(account_id)

    # ------------------------------------------------------------ groups

    def create_group(self, *, name: str, currency: str, color: int = 4, account_ids: list[str] | None = None) -> dict:
        accounts = arr(ref("Account", x) for x in account_ids) if account_ids else NIL
        result = self.n.invoke("manager", "createAccountGroupForUser:name:colorNum:currency:accounts:",
                               [USER, s(name), i(color), s(currency_code(self.n, currency)), accounts])["value"]
        if not result:
            raise MoneyWizError("MoneyWiz did not create the group")
        return self.group(result["gid"])

    def update_group(self, group_ref: str | int, *, name: str | None = None, currency: str | None = None,
                     color: int | None = None, account_ids: list[str] | None = None) -> dict:
        group = self.group(group_ref)
        args = [ref("Group", group["id"]), s(name or group["name"]),
                i(group.get("color", 4) if color is None else color), s(currency_code(self.n, currency or group["currency"]))]
        if account_ids is None:
            self.n.invoke("manager", "updateAccountGroup:name:colorNum:currency:", args)
        else:
            self.n.invoke("manager", "updateAccountGroup:name:colorNum:currency:accounts:",
                          [*args, arr(ref("Account", x) for x in account_ids)])
        return self.group(group["id"])

    def delete_group(self, group_ref: str | int) -> dict:
        group = self.group(group_ref)
        self.n.invoke("manager", "deleteAccountGroup:", [ref("Group", group["id"])])
        if self.n.get("Group", group["id"]):
            raise MoneyWizError("Group still exists after delete")
        return {"deleted": group["id"], "name": group["name"]}

    def reorder_groups(self, ordered_ids: list[str]) -> list[dict]:
        known = {g["id"]: g for g in self.groups()}
        # A partial order would leave two groups at the same position.
        if sorted(ordered_ids) != sorted(known):
            raise MoneyWizError("Give every account group exactly once (see moneywiz_list_groups, kind account)")
        for position, group_id in enumerate(ordered_ids):
            if known[group_id].get("display_order") != position:
                self.n.invoke("manager", "updateAccountGroup:withDisplayOrder:", [ref("Group", group_id), ni(position)])
        return self.groups()
