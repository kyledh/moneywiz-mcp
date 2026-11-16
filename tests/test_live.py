"""Live round-trip against the running MoneyWiz app.

Skipped unless MoneyWiz is running and MONEYWIZ_LIVE_TESTS=1 is set, because it
writes to the real ledger. Everything it creates is named with the SANDBOX
prefix and removed again, including on failure.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

from moneywiz_mcp.accounts import Accounts
from moneywiz_mcp.budgets import Budgets
from moneywiz_mcp.catalog import Catalog
from moneywiz_mcp.investments import Investments
from moneywiz_mcp.native import MoneyWizError, Native, s
from moneywiz_mcp.portfolio import Portfolio
from moneywiz_mcp.scheduled import Scheduled
from moneywiz_mcp.settings import Settings
from moneywiz_mcp.transactions import Transactions

SANDBOX = "MCP-TEST-"

pytestmark = pytest.mark.skipif(os.environ.get("MONEYWIZ_LIVE_TESTS") != "1",
                                reason="set MONEYWIZ_LIVE_TESTS=1 to run write tests against the real ledger")


def drop_scheduled(native: Native):
    for row in native.query("ScheduledTransactionHandler", fields=["desc", "account.name"])["items"]:
        if (row.get("desc") or "").startswith(SANDBOX) or (row.get("account.name") or "").startswith(SANDBOX):
            Scheduled(native).delete(row["gid"])


def drop_budgets(native: Native):
    budgets = Budgets(native)
    for row in native.query("Budget", where=[["name", "begins", s(SANDBOX)]], fields=[])["items"]:
        budgets.delete(row["gid"])
    for group in budgets.groups():
        if group["name"].startswith(SANDBOX):
            budgets.delete_group(group["id"])


def cleanup(native: Native):
    accounts, catalog, transactions = Accounts(native), Catalog(native), Transactions(native)
    drop_scheduled(native)
    drop_budgets(native)
    for account in native.query("Account", where=[["name", "begins", s(SANDBOX)]], fields=["name"])["items"]:
        for row in native.query("Transaction", where=[["account.GID", "eq", s(account["gid"])]], fields=[])["items"]:
            transactions.delete(row["gid"])
        accounts.delete(account["gid"])
    for row in native.query("Transaction", where=[["desc", "begins", s(SANDBOX)]], fields=[])["items"]:
        transactions.delete(row["gid"])
    for holding in native.query("InvestmentHolding", where=[["symbol", "begins", s("MCPTEST")]], fields=["symbol"])["items"]:
        Investments(native).delete_holding(holding["gid"], delete_trades=True)
    for group in [g for g in accounts.groups() if g["name"].startswith(SANDBOX)]:
        accounts.delete_group(group["id"])
    for entity in ("Payee", "Tag", "Category"):
        for row in native.query(entity, where=[["name", "begins", s(SANDBOX)]], fields=["name"])["items"]:
            catalog.delete(entity, row["gid"])


@pytest.fixture(scope="module")
def live():
    native = Native()
    status = native.status()
    assert status["store_path"].endswith(".sqlite")
    cleanup(native)
    before, order_before = snapshot(native), ordering(native)
    accounts = Accounts(native)
    # Every sandbox account lives in a sandbox group: moving an account makes
    # MoneyWiz renumber the active accounts of the group it joins and of the
    # group it leaves, and that must never reach a real group. (Archiving
    # moves an account to no group without renumbering; ordering() checks it.)
    group = accounts.create_group(name=SANDBOX + "GROUP", currency="CNY")
    yield {"native": native, "accounts": accounts, "catalog": Catalog(native),
           "transactions": Transactions(native), "settings": Settings(native), "portfolio": Portfolio(native),
           "scheduled": Scheduled(native),
           "before": before, "order_before": order_before, "group": group["group_id"]}
    cleanup(native)
    try:
        assert snapshot(native) == before, "object counts changed"
        assert ordering(native) == order_before, "real accounts, groups or categories were reordered"
    finally:
        native.close()


def snapshot(native: Native) -> dict:
    return {entity: native.count(entity)
            for entity in ("Account", "Transaction", "Category", "Payee", "Tag", "ScheduledTransactionHandler", "Budget")}


def ordering(native: Native) -> dict:
    """Position, group and archive state of every real (non-sandbox) object."""
    def real(row):
        return not (row.get("name") or "").startswith(SANDBOX)
    return {
        "accounts": {a["gid"]: (a["displayOrder"], a["groupID"], a["archived"])
                     for a in native.query("Account", fields=["name", "displayOrder", "groupID", "archived"])["items"]
                     if real(a)},
        "groups": {g["gid"]: g["displayOrder"]
                   for g in native.query("Group", fields=["name", "displayOrder"])["items"] if real(g)},
        "categories": {c["gid"]: (c["displayOrder"], c.get("parentCategory.GID"))
                       for c in native.query("Category", fields=["name", "displayOrder", "parentCategory.GID"])["items"]
                       if real(c)},
        # deleteBudget: renumbers budgets in every group; the server puts the others back.
        "budgets": {b["gid"]: (b["displayOrder"], b["groupID"])
                    for b in native.query("Budget", fields=["name", "displayOrder", "groupID"])["items"] if real(b)},
    }


def test_reads_do_not_touch_the_app_state(live):
    native = live["native"]
    assert native.status()["context"]["pending"] == 0
    assert live["accounts"].list(), "the ledger should have at least one account"
    assert live["transactions"].list(limit=1)["total"] >= 0
    assert live["settings"].currencies()["default_currency"]
    assert native.status()["context"]["pending"] == 0


def test_expense_lifecycle(live):
    accounts, catalog, transactions = live["accounts"], live["catalog"], live["transactions"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "CASH", kind="cash", currency="CNY", opening_balance="100")
    category = catalog.create_category(SANDBOX + "EXP", "expense")
    try:
        created = transactions.create(account=account["id"], kind="expense", amount_="12.50", date_="2026-11-15",
                                      description=SANDBOX + "coffee", payee=SANDBOX + "PAYEE",
                                      categories=category["id"], tags=[SANDBOX + "TAG"], memo="note")
        assert created["amount"] == "-12.5"
        assert created["categories"][0]["amount"] == "-12.5"
        assert accounts.get(account["id"])["balance"] == "87.5"

        edited = transactions.update(created["id"], amount="15.00", memo="edited")
        assert edited["amount"] == "-15" and edited["memo"] == "edited"
        assert edited["categories"][0]["amount"] == "-15", "a single category follows the amount"
        assert accounts.get(account["id"])["balance"] == "85"

        assert transactions.set_status(created["id"], "pending")["status"] == "pending"
        assert transactions.set_status(created["id"], "reconciled")["reconciled"] is True
        assert transactions.set_status(created["id"], "unreconciled")["reconciled"] is False

        found = transactions.list(account=account["id"], text=SANDBOX + "coffee")
        assert [t["id"] for t in found["transactions"]] == [created["id"]]

        transactions.delete(created["id"])
        assert live["native"].get("Transaction", created["id"]) is None
        assert accounts.get(account["id"])["balance"] == "100"
    finally:
        for payee in catalog.payees(SANDBOX):
            catalog.delete("Payee", payee["id"])
        for tag in [t for t in catalog.tags() if t["name"].startswith(SANDBOX)]:
            catalog.delete("Tag", tag["id"])
        catalog.delete("Category", category["id"])
        accounts.delete(account["id"])


def test_transfer_keeps_both_legs_consistent(live):
    accounts, transactions = live["accounts"], live["transactions"]
    source = accounts.create(group_id=live["group"], name=SANDBOX + "FROM", kind="cash", currency="CNY", opening_balance="500")
    target = accounts.create(group_id=live["group"], name=SANDBOX + "TO", kind="cash", currency="USD")
    try:
        transfer = transactions.create(account=source["id"], kind="transfer", amount_="70", date_="2026-11-15",
                                       description=SANDBOX + "xfer", transfer_to=target["id"], transfer_amount="10")
        other = transactions.get(transfer["other_leg_id"])
        assert transfer["amount"] == "-70" and other["amount"] == "10"
        assert other["date"] == transfer["date"], "both legs share the transfer date"
        assert accounts.get(source["id"])["balance"] == "430"
        assert accounts.get(target["id"])["balance"] == "10"

        edited = transactions.update(transfer["id"], amount="80", transfer_amount="11.20")
        assert edited["amount"] == "-80"
        assert transactions.get(transfer["other_leg_id"])["amount"] == "11.2"

        removed = transactions.delete(transfer["id"])
        assert set(removed["deleted"]) == {transfer["id"], transfer["other_leg_id"]}
        assert accounts.get(source["id"])["balance"] == "500"
        assert accounts.get(target["id"])["balance"] == "0"
    finally:
        accounts.delete(source["id"])
        accounts.delete(target["id"])


def test_foreign_currency_expense_records_the_rate(live):
    accounts, transactions = live["accounts"], live["transactions"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "FX", kind="cash", currency="CNY")
    try:
        created = transactions.create(account=account["id"], kind="expense", amount_="72.00", date_="2026-11-15",
                                      description=SANDBOX + "fx", original_amount="10", original_currency="USD")
        assert created["original"]["currency"] == "USD"
        assert Decimal(created["original"]["exchange_rate"]) == Decimal("7.2")
        transactions.delete(created["id"])
    finally:
        accounts.delete(account["id"])


def test_refund_links_back_to_the_expense(live):
    accounts, transactions = live["accounts"], live["transactions"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "REF", kind="cash", currency="CNY", opening_balance="100")
    try:
        expense = transactions.create(account=account["id"], kind="expense", amount_="30", date_="2026-11-14",
                                      description=SANDBOX + "purchase")
        refund = transactions.create(account=account["id"], kind="refund", amount_="5", date_="2026-11-15",
                                     description=SANDBOX + "refund", refund_of=expense["id"])
        assert refund["refund_of_ids"] == [expense["id"]]
        assert transactions.get(expense["id"])["refunded_by_ids"] == [refund["id"]]
        assert accounts.get(account["id"])["balance"] == "75"
        transactions.delete(refund["id"])
        transactions.delete(expense["id"])
    finally:
        accounts.delete(account["id"])


def test_catalog_merge_moves_references(live):
    catalog, accounts, transactions = live["catalog"], live["accounts"], live["transactions"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "MERGE", kind="cash", currency="CNY")
    keep = catalog.create_named("Payee", SANDBOX + "KEEP")
    drop = catalog.create_named("Payee", SANDBOX + "DROP")
    try:
        created = transactions.create(account=account["id"], kind="expense", amount_="1", date_="2026-11-15",
                                      description=SANDBOX + "merge", payee=drop["name"])
        assert catalog.usage("Payee", drop["id"])["transactions"] == 1
        catalog.merge("Payee", [drop["id"]], keep["id"])
        assert transactions.get(created["id"])["payee_id"] == keep["id"]
        assert live["native"].get("Payee", drop["id"]) is None
        transactions.delete(created["id"])
    finally:
        catalog.delete("Payee", keep["id"])
        accounts.delete(account["id"])


def test_deleting_a_used_category_payee_or_tag_unlinks_it(live):
    # MoneyWiz's own rule: the transaction stays, without the deleted link, and
    # a category takes its subcategories with it.
    accounts, transactions, catalog = live["accounts"], live["transactions"], live["catalog"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "UNLINK", kind="cash", currency="CNY", opening_balance="10")
    parent = catalog.create_category(SANDBOX + "PARENT", "expense")
    child = catalog.create_category(SANDBOX + "CHILD", "expense", parent_id=parent["id"])
    try:
        used = transactions.create(account=account["id"], kind="expense", amount_="4", date_="2026-11-15",
                                   description=SANDBOX + "unlink", categories=child["id"],
                                   payee=SANDBOX + "PAYEE", tags=[SANDBOX + "TAG"])
        payee = next(p for p in catalog.payees(SANDBOX) if p["name"] == SANDBOX + "PAYEE")
        tag = next(x for x in catalog.tags() if x["name"] == SANDBOX + "TAG")
        assert catalog.delete("Category", parent["id"])["was_used_by"] == {"childCategories": 1}
        assert catalog.delete("Payee", payee["id"])["was_used_by"] == {"transactions": 1}
        assert catalog.delete("Tag", tag["id"])["was_used_by"] == {"transactions": 1}
        assert live["native"].get("Category", child["id"]) is None
        after = transactions.get(used["id"])
        assert after["amount"] == "-4" and not after.get("categories") and not after.get("payee") and not after.get("tags")
        assert accounts.get(account["id"])["balance"] == "6"
        transactions.delete(used["id"])
    finally:
        accounts.delete(account["id"])


def test_rejects_input_it_cannot_turn_into_a_call(live):
    # Only what the server needs to build a valid call is checked here; value
    # rules (sign, zero, precision, names) are MoneyWiz's own.
    accounts, transactions, native = live["accounts"], live["transactions"], live["native"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "BAD", kind="cash", currency="CNY")
    try:
        for bad in (
            dict(account=account["id"], kind="expense", amount_="abc", date_="2026-11-15"),
            dict(account=account["id"], kind="expense", amount_="NaN", date_="2026-11-15"),
            dict(account=account["id"], kind="expense", amount_="1", date_="15/11/2026"),
            dict(account="no-such-account", kind="expense", amount_="1", date_="2026-11-15"),
            dict(account=account["id"], kind="expense", amount_="1", date_="2026-11-15", categories="no/such/category"),
            dict(account=account["id"], kind="transfer", amount_="1", date_="2026-11-15"),
            dict(account=account["id"], kind="refund", amount_="1", date_="2026-11-15"),
        ):
            with pytest.raises(MoneyWizError):
                transactions.create(**bad)
        assert native.count("Transaction", [["account.GID", "eq", s(account["id"])]]) == 0
        assert native.status()["context"]["pending"] == 0
    finally:
        accounts.delete(account["id"])


# Archiving is not exercised here: MoneyWiz moves an archived account to "no
# group" and renumbers the active accounts there, which are the user's real
# ones. (Deleting it afterwards renumbers them again, which only restores the
# original values when they were already contiguous.)
def test_account_move_stays_inside_sandbox_groups(live):
    accounts, native = live["accounts"], live["native"]
    home = live["group"]
    other = accounts.create_group(name=SANDBOX + "OTHER", currency="CNY")["group_id"]
    made = [accounts.create(group_id=home, name=SANDBOX + f"ORDER{i}", kind="cash", currency="CNY") for i in range(3)]
    names = lambda group: [a["name"].removeprefix(SANDBOX) for a in accounts.list(group_id=group)]
    try:
        assert names(home) == ["ORDER0", "ORDER1", "ORDER2"]
        accounts.move(made[2]["id"], display_order=0)
        assert names(home) == ["ORDER2", "ORDER0", "ORDER1"]
        assert [a["display_order"] for a in accounts.list(group_id=home)] == [0, 1, 2]

        moved = accounts.move(made[0]["id"], group_id=other)  # no position: appended, not carried over
        assert moved["group_id"] == other and moved["display_order"] == 0
        assert names(home) == ["ORDER2", "ORDER1"] and names(other) == ["ORDER0"]
        assert [a["display_order"] for a in accounts.list(group_id=home)] == [0, 1], "the group it left is renumbered"
        with pytest.raises(MoneyWizError):
            accounts.move(made[0]["id"])  # same group and no position
        assert ordering(native) == live["order_before"]
    finally:
        for account in made:
            accounts.delete(account["id"])
        accounts.delete_group(other)


def test_ledger_is_unchanged_by_the_suite(live):
    assert snapshot(live["native"]) == live["before"]
    assert ordering(live["native"]) == live["order_before"]


def test_investment_trade_lifecycle(live):
    accounts, investments, portfolio = live["accounts"], Investments(live["native"]), live["portfolio"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "BROKER", kind="investment", currency="USD",
                              opening_balance="10000", opening_date="2026-11-01")
    try:
        bought = investments.trade(account=account["id"], side="buy", symbol="MCPTEST", shares="10",
                                   price="25.00", date_="2026-11-10", commission="1.00",
                                   description=SANDBOX + "stock", holding_type="Stock")
        assert bought["transaction"]["kind"] == "investment_buy"
        assert bought["transaction"]["amount"] == "-251"  # 10 x 25 + 1 commission
        assert bought["holding"]["shares"] == "10"
        assert bought["account_balance"] == "9749"

        again = investments.trade(account=account["id"], side="buy", symbol="MCPTEST", shares="5",
                                  price="30.00", date_="2026-11-12")
        assert again["holding"]["shares"] == "15"
        assert again["holding"]["cost_basis"] == "401"

        edited = investments.update_trade(bought["transaction"]["id"], shares="12", price="26.00", commission="2.00")
        assert edited["transaction"]["amount"] == "-314"
        assert edited["holding"]["shares"] == "17"

        sold = investments.trade(account=account["id"], side="sell", symbol="MCPTEST", shares="4",
                                 price="40.00", date_="2026-11-14", commission="0.50")
        assert sold["transaction"]["kind"] == "investment_sell"
        assert sold["transaction"]["amount"] == "159.5"
        assert sold["holding"]["shares"] == "13"

        for row in live["native"].query("Transaction", where=[["account.GID", "eq", s(account["id"])]],
                                        fields=[])["items"]:
            investments.delete_trade(row["gid"])
        assert portfolio.holdings(account["id"]) == []
        assert accounts.get(account["id"])["balance"] == "10000"
    finally:
        for row in live["native"].query("Transaction", where=[["account.GID", "eq", s(account["id"])]],
                                        fields=[])["items"]:
            live["transactions"].delete(row["gid"])
        accounts.delete(account["id"])


def test_commission_rebates_are_negative_commissions(live):
    accounts, investments = live["accounts"], Investments(live["native"])
    account = accounts.create(group_id=live["group"], name=SANDBOX + "REBATE", kind="investment", currency="USD",
                              opening_balance="10000", opening_date="2026-11-01")
    try:
        # Brokers such as IBKR pay rebates; MoneyWiz nets a negative commission.
        bought = investments.trade(account=account["id"], side="buy", symbol="MCPTESTR", shares="400", price="10",
                                   date_="2026-11-10", commission="-1.96", description=SANDBOX + "rebate")
        assert bought["transaction"]["amount"] == "-3998.04" and bought["transaction"]["fee"] == "-1.96"
        assert bought["account_balance"] == "6001.96"
        # The full precision of the broker's figure, and edits that leave the rebate alone.
        edited = investments.update_trade(bought["transaction"]["id"], commission="-1.961623471")
        assert edited["transaction"]["fee"] == "-1.961623471"
        assert edited["transaction"]["amount"] == "-3998.038376529"
        edited = investments.update_trade(bought["transaction"]["id"], description=SANDBOX + "rebate2")
        assert edited["transaction"]["fee"] == "-1.961623471"
        sold = investments.trade(account=account["id"], side="sell", symbol="MCPTESTR", shares="100", price="12",
                                 date_="2026-11-12", commission="-0.5")
        assert sold["transaction"]["amount"] == "1200.5"
        for row in live["native"].query("Transaction", where=[["account.GID", "eq", s(account["id"])]],
                                        fields=[])["items"]:
            investments.delete_trade(row["gid"])
        assert accounts.get(account["id"])["balance"] == "10000"
    finally:
        for row in live["native"].query("Transaction", where=[["account.GID", "eq", s(account["id"])]],
                                        fields=[])["items"]:
            live["transactions"].delete(row["gid"])
        accounts.delete(account["id"])


def test_refund_uses_the_expense_category_and_full_precision(live):
    accounts, transactions, catalog = live["accounts"], live["transactions"], live["catalog"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "REF2", kind="cash", currency="USD", opening_balance="100")
    category = catalog.create_category(SANDBOX + "FEES", "expense")
    try:
        expense = transactions.create(account=account["id"], kind="expense", amount_="2.123456789", date_="2026-11-14",
                                      description=SANDBOX + "fee", categories=category["id"])
        assert expense["amount"] == "-2.123456789"
        # As in MoneyWiz, a refund is filed under expense categories.
        refund = transactions.create(account=account["id"], kind="refund", amount_="2.123456789", date_="2026-11-15",
                                     description=SANDBOX + "cancel", refund_of=expense["id"], categories=category["id"])
        assert refund["refund_of_ids"] == [expense["id"]]
        assert [c["id"] for c in refund["categories"]] == [category["id"]]
        assert refund["categories"][0]["amount"] == "2.123456789"
        edited = transactions.update(refund["id"], amount="1.5", categories=[{"category": category["id"], "amount": "1.5"}])
        assert edited["amount"] == "1.5" and [c["id"] for c in edited["categories"]] == [category["id"]]
        transactions.delete(refund["id"])
        transactions.delete(expense["id"])
        assert accounts.get(account["id"])["balance"] == "100"
    finally:
        accounts.delete(account["id"])
        catalog.delete("Category", category["id"])


def test_selling_what_is_not_held_opens_a_short(live):
    accounts, investments = live["accounts"], Investments(live["native"])
    account = accounts.create(group_id=live["group"], name=SANDBOX + "SHORT", kind="investment", currency="USD",
                              opening_balance="1000")
    try:
        short = investments.trade(account=account["id"], side="sell", symbol="MCPTESTS", shares="10", price="20",
                                  date_="2026-11-10", commission="1", holding_type="Stock")
        assert short["transaction"]["amount"] == "199" and short["holding"]["shares"] == "-10"
        assert short["account_balance"] == "1199"
        cover = investments.trade(account=account["id"], side="buy", symbol="MCPTESTS", shares="10", price="18",
                                  date_="2026-11-12", commission="1")
        assert cover["holding"]["shares"] == "0" and cover["account_balance"] == "1018"
    finally:
        for row in live["native"].query("Transaction", where=[["account.GID", "eq", s(account["id"])]],
                                        fields=[])["items"]:
            investments.delete_trade(row["gid"])
        for holding in live["native"].query("InvestmentHolding", where=[["symbol", "eq", s("MCPTESTS")]], fields=[])["items"]:
            investments.delete_holding(holding["gid"], delete_trades=True)
        accounts.delete(account["id"])


def test_manual_holding_and_price(live):
    accounts, investments, portfolio = live["accounts"], Investments(live["native"]), live["portfolio"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "BROKER2", kind="investment", currency="USD", opening_balance="100")
    try:
        holding = investments.create_holding(account=account["id"], symbol="MCPTESTY", name=SANDBOX + "Y",
                                             holding_type="Stock", shares="100", price="2.50")
        assert holding["shares"] == "100" and holding["price_per_share"] == "2.5"
        assert portfolio.set_holding_price(holding["id"], "3.10", "2026-11-14")["holding"]["price_per_share"] == "3.1"
        assert portfolio.set_holding_shares(holding["id"], "120")["shares"] == "120"
        assert portfolio.set_holding_cost_basis(holding["id"], "300")["cost_basis"] == "300"
        investments.delete_holding(holding["id"])
        assert portfolio.holdings(account["id"]) == []
    finally:
        accounts.delete(account["id"])


def _transactions_of(native, account_id):
    return native.query("Transaction", where=[["account.GID", "eq", s(account_id)]], fields=[])["items"]


def test_scheduled_bill_lifecycle(live):
    accounts, catalog, scheduled, transactions = live["accounts"], live["catalog"], live["scheduled"], live["transactions"]
    account = accounts.create(group_id=live["group"], name=SANDBOX + "BILLS", kind="cash", currency="CNY",
                              opening_balance="1000")
    category = catalog.create_category(SANDBOX + "RENT", "expense")
    try:
        bill = scheduled.create(kind="expense", account=account["id"], amount_="50", date_="2027-01-15",
                                description=SANDBOX + "rent", payee=SANDBOX + "LANDLORD", categories=category["id"],
                                tags=[SANDBOX + "TAG"], memo="m")
        # Scheduled methods store the amount as given; expenses are passed negative, as the app's editor does.
        assert bill["amount"] == "-50" and bill["categories"][0]["amount"] == "-50"
        assert bill["next_due"].startswith("2027-01-15") and bill["repeat"] == {"every": 1, "unit": "month"}
        assert [x[:10] for x in bill["upcoming_dates"]] == ["2027-02-15", "2027-03-15", "2027-04-15"]

        assert scheduled.update(bill["id"], amount="60")["categories"][0]["amount"] == "-60"
        paid = scheduled.execute(bill["id"])
        assert paid["transaction"]["amount"] == "-60" and paid["transaction"]["date"].startswith("2027-01-15")
        assert paid["scheduled"]["next_due"].startswith("2027-02-15") and paid["scheduled"]["executions"] == 1
        assert accounts.get(account["id"])["balance"] == "940"

        # Values handed back exactly as stored keep the schedule's history.
        kept = scheduled.update(bill["id"], description=SANDBOX + "rent2")
        assert kept["executions"] == 1 and kept["first_date"].startswith("2027-01-15")

        skipped = scheduled.skip(bill["id"])
        assert skipped["skipped"].startswith("2027-02-15") and skipped["scheduled"]["next_due"].startswith("2027-03-15")
        assert len(_transactions_of(live["native"], account["id"])) == 1

        partial = scheduled.execute(bill["id"], "55")
        assert partial["transaction"]["amount"] == "-55" and partial["transaction"]["categories"][0]["amount"] == "-55"
        assert partial["scheduled"]["amount"] == "-60"

        # MoneyWiz's rule: a new repeat rule restarts the schedule from its date.
        weekly = scheduled.update(bill["id"], repeat="week", every=2)
        assert weekly["repeat"] == {"every": 14, "unit": "day"} and weekly["executions"] == 0
        assert weekly["first_date"].startswith("2027-04-15")

        removed = scheduled.delete(bill["id"])
        assert removed["deleted"] == bill["id"] and live["native"].get("ScheduledTransactionHandler", bill["id"]) is None
        assert len(_transactions_of(live["native"], account["id"])) == 2, "recorded transactions stay"
    finally:
        drop_scheduled(live["native"])
        for row in _transactions_of(live["native"], account["id"]):
            transactions.delete(row["gid"])
        accounts.delete(account["id"])
        for payee in catalog.payees(SANDBOX):
            catalog.delete("Payee", payee["id"])
        for tag in [x for x in catalog.tags() if x["name"].startswith(SANDBOX)]:
            catalog.delete("Tag", tag["id"])
        catalog.delete("Category", category["id"])


def test_scheduled_series_rules_and_transfers(live):
    accounts, scheduled, transactions = live["accounts"], live["scheduled"], live["transactions"]
    source = accounts.create(group_id=live["group"], name=SANDBOX + "PAY", kind="cash", currency="CNY",
                             opening_balance="500")
    target = accounts.create(group_id=live["group"], name=SANDBOX + "SAVE", kind="cash", currency="CNY")
    try:
        series = scheduled.create(kind="income", account=source["id"], amount_="300", date_="2027-01-01",
                                  repeat="week", occurrences=3, weekends="after", description=SANDBOX + "pay")
        assert series["amount"] == "300" and series["occurrences"] == 3 and series["weekends"] == "after"
        assert series["end_date"].startswith("2027-01-15")
        assert [x[:10] for x in series["upcoming_dates"]] == ["2027-01-08", "2027-01-15"], "stops at the end"
        for _ in range(3):
            last = scheduled.execute(series["id"])
        assert last["transaction"]["date"].startswith("2027-01-15")
        assert last["scheduled"]["finished"] is True and "next_due" not in last["scheduled"]
        with pytest.raises(MoneyWizError):
            scheduled.execute(series["id"])  # MoneyWiz itself would record the last date again
        assert accounts.get(source["id"])["balance"] == "1400"

        month_end = scheduled.create(kind="expense", account=source["id"], amount_="9.99", date_="2027-01-29",
                                     week_of_month="last", day_of_week="weekday", description=SANDBOX + "lastweekday")
        assert month_end["on"] == {"week_of_month": "last", "day_of_week": "weekday"}
        assert [x[:10] for x in month_end["upcoming_dates"]] == ["2027-02-26", "2027-03-31", "2027-04-30"]

        transfer = scheduled.create(kind="transfer", account=source["id"], transfer_to=target["id"], amount_="100",
                                    date_="2027-01-20", description=SANDBOX + "save")
        assert transfer["amount"] == "-100" and transfer["transfer_to_id"] == target["id"]
        moved = scheduled.execute(transfer["id"])
        assert moved["transaction"]["kind"] == "transfer_out" and moved["transaction"]["other_leg_id"]
        assert [accounts.get(x["id"])["balance"] for x in (source, target)] == ["1300", "100"]
        changed = scheduled.update(transfer["id"], amount="120")
        assert changed["amount"] == "-120" and changed["executions"] == 1

        once = scheduled.create(kind="expense", account=source["id"], amount_="5", date_="2027-02-01", repeat="none",
                                description=SANDBOX + "once")
        assert once["repeats"] is False and "upcoming_dates" not in once
        assert scheduled.execute(once["id"])["scheduled"]["finished"] is True

        for bad in (dict(repeat="month", every=0), dict(repeat="week", occurrences=0), dict(repeat="fortnight"),
                    dict(weekends="sometimes")):
            with pytest.raises(MoneyWizError):
                scheduled.create(kind="expense", account=source["id"], amount_="1", date_="2027-01-01",
                                 description=SANDBOX + "bad", **bad)
        assert live["native"].status()["context"]["pending"] == 0
    finally:
        drop_scheduled(live["native"])
        for account in (source, target):
            for row in _transactions_of(live["native"], account["id"]):
                if live["native"].get("Transaction", row["gid"]):  # a transfer's other leg goes with it
                    transactions.delete(row["gid"])
            accounts.delete(account["id"])


def test_adjust_balance_books_the_difference(live):
    accounts, transactions, native = live["accounts"], live["transactions"], live["native"]
    bank = accounts.create(group_id=live["group"], name=SANDBOX + "BANK", kind="checking", currency="CNY",
                           opening_balance="100")
    fx = accounts.create(group_id=live["group"], name=SANDBOX + "FXADJ", kind="forex", currency="CNY")
    try:
        transactions.create(account=bank["id"], kind="expense", amount_="30", date_="2026-11-10",
                            description=SANDBOX + "e1")
        # A bare date means the end of that day, so the noon expense counts.
        first = transactions.adjust_balance(account=bank["id"], balance="50", date_="2026-11-10",
                                            description=SANDBOX + "stmt")
        assert first["transaction"]["kind"] == "reconcile" and first["transaction"]["amount"] == "-20"
        assert first["transaction"]["balance_set_to"] == "50" and first["account_balance"] == "50"
        transactions.create(account=bank["id"], kind="expense", amount_="5", date_="2026-11-12",
                            description=SANDBOX + "e2")
        second = transactions.adjust_balance(account=bank["id"], balance="60", date_="2026-11-11",
                                             description=SANDBOX + "stmt2")
        assert second["transaction"]["amount"] == "10" and second["account_balance"] == "55"
        with pytest.raises(MoneyWizError):
            transactions.update(second["transaction"]["id"], description="x")  # delete and adjust again instead
        transactions.delete(first["transaction"]["id"])
        assert accounts.get(bank["id"])["balance"] == "75"

        with pytest.raises(MoneyWizError):
            transactions.adjust_balance(account=fx["id"], balance="100", date_="2026-11-10")  # which currency?
        position = transactions.adjust_balance(account=fx["id"], balance="100", currency="USD", date_="2026-11-10",
                                               description=SANDBOX + "usd")
        assert position["transaction"]["balance_set_to"] == {"currency": "USD", "amount": "100"}
        holdings = live["portfolio"].holdings(fx["id"])
        assert [(h["symbol"], h["shares"]) for h in holdings] == [("USD", "100")]
        # MoneyWiz prices the new position afterwards without saving; the server saves that, so writes go on.
        assert native.status()["context"]["pending"] == 0
    finally:
        for account in (bank, fx):
            for row in _transactions_of(native, account["id"]):
                transactions.delete(row["gid"])
            for holding in live["portfolio"].holdings(account["id"]):
                Investments(native).delete_holding(holding["id"], delete_trades=True)
            accounts.delete(account["id"])


def test_budget_lifecycle(live):
    accounts, catalog, transactions, native = live["accounts"], live["catalog"], live["transactions"], live["native"]
    budgets = Budgets(native)
    account = accounts.create(group_id=live["group"], name=SANDBOX + "SPEND", kind="cash", currency="CNY",
                              opening_balance="1000")
    category = catalog.create_category(SANDBOX + "FOOD", "expense")
    try:
        food = budgets.create(name=SANDBOX + "FOOD", amount_="100", categories=[category["id"]], accounts=[account["id"]],
                              start_date="2026-11-01")
        assert food["kind"] == "expense" and food["repeat"] == {"every": 1, "unit": "month"}
        assert food["current_period"]["budgeted"] == "100" and food["current_period"]["spent"] == "0"
        assert [a["id"] for a in food["accounts"]] == [account["id"]]
        transactions.create(account=account["id"], kind="expense", amount_="30", date_="2026-11-10",
                            categories=category["id"], description=SANDBOX + "lunch")
        # Going over the limit raises MoneyWiz's budget warning; the server skips that alert.
        transactions.create(account=account["id"], kind="expense", amount_="90", date_="2026-11-12",
                            categories=category["id"], description=SANDBOX + "dinner")
        over = budgets.get(food["id"])["current_period"]
        assert over["spent"] == "120" and over["remaining"] == "-20"

        raised = budgets.update(food["id"], amount="150", apply_to=["current", "future"])
        assert raised["current_period"]["budgeted"] == "150" and raised["current_period"]["remaining"] == "30"
        tagged = budgets.update(food["id"], rollover=True, tags=[SANDBOX + "TAG"])
        assert tagged["rollover"] is True and tagged["tags"] == [SANDBOX + "TAG"]
        renamed = budgets.update(food["id"], name=SANDBOX + "FOOD2", tags=[])
        assert renamed["name"] == SANDBOX + "FOOD2" and "tags" not in renamed
        assert renamed["current_period"]["budgeted"] == "150", "a rename keeps the amount"

        trip = budgets.create(name=SANDBOX + "TRIP", amount_="500", categories=[category["id"]], accounts=[account["id"]],
                              repeat="none", start_date="2026-11-01", end_date="2026-11-30")
        assert trip["repeats"] is False and trip["end_date"].startswith("2026-11-30T23:59:59")
        assert trip["current_period"]["spent"] == "120"

        group = budgets.create_group(name=SANDBOX + "BGROUP", budget_ids=[food["id"]])
        assert group["budget_ids"] == [food["id"]]
        group = budgets.update_group(group["id"], budget_ids=[food["id"], trip["id"]])
        assert set(group["budget_ids"]) == {food["id"], trip["id"]}
        assert "group_id" not in budgets.update(trip["id"], group="")
        assert budgets.update(trip["id"], group=group["id"])["group_id"] == group["group_id"]

        moved = budgets.transfer(from_budget=trip["id"], to_budget=food["id"], amount_="20", date_="2026-11-15",
                                 description=SANDBOX + "move")
        assert moved["from"]["current_period"]["available"] == "480"
        assert moved["to"]["current_period"]["available"] == "170"
        transactions.delete(moved["transfer_id"])
        assert budgets.get(food["id"])["current_period"]["available"] == "150"

        removed = budgets.delete_group(group["id"])
        assert set(removed["budget_ids"]) == {food["id"], trip["id"]}
        for budget in (food, trip):
            budgets.delete(budget["id"])
            assert native.get("Budget", budget["id"]) is None
        assert ordering(native)["budgets"] == live["order_before"]["budgets"], "real budgets keep their order"
    finally:
        drop_budgets(native)
        for row in _transactions_of(native, account["id"]):
            transactions.delete(row["gid"])
        accounts.delete(account["id"])
        catalog.delete("Category", category["id"])
        for tag in [x for x in catalog.tags() if x["name"].startswith(SANDBOX)]:
            catalog.delete("Tag", tag["id"])
