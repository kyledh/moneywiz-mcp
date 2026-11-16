"""Live round-trip against the running MoneyWiz app.

Skipped unless MoneyWiz is running and MONEYWIZ_LIVE_TESTS=1 is set, because it
writes to the real ledger. Everything it creates is named with the SANDBOX
prefix and removed again, including on failure.
"""
from __future__ import annotations

import os
import struct
import zlib
from decimal import Decimal

import pytest

from moneywiz_mcp.accounts import Accounts
from moneywiz_mcp.backups import Backups
from moneywiz_mcp.budgets import Budgets
from moneywiz_mcp.catalog import Catalog
from moneywiz_mcp.investments import Investments
from moneywiz_mcp.native import NIL, MoneyWizError, Native, date, n, ref, s
from moneywiz_mcp.portfolio import Portfolio
from moneywiz_mcp.reports import KINDS as REPORT_KINDS, Reports
from moneywiz_mcp.scheduled import Scheduled
from moneywiz_mcp.settings import Settings
from moneywiz_mcp.transactions import CREATE_EXPENSE, Transactions
from moneywiz_mcp.values import parse_when

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


def drop_reports(native: Native):
    reports = Reports(native)
    for row in native.query("CustomReport", where=[["name", "begins", s(SANDBOX)]], fields=[])["items"]:
        native.invoke("manager", "deleteCustomReport:", [{"ref": ["CustomReport", row["gid"]]}])
    for group in reports.groups():
        if group["name"].startswith(SANDBOX):
            reports.delete_group(group["id"])


def cleanup(native: Native):
    accounts, catalog, transactions = Accounts(native), Catalog(native), Transactions(native)
    drop_scheduled(native)
    drop_budgets(native)
    drop_reports(native)
    for account in native.query("Account", where=[["name", "begins", s(SANDBOX)]], fields=["name"])["items"]:
        for row in native.query("Transaction", where=[["account.GID", "eq", s(account["gid"])]], fields=[])["items"]:
            if native.get("Transaction", row["gid"]):  # a transfer's other leg, or an expense's refunds, may be gone
                transactions.delete(row["gid"])
        accounts.delete(account["gid"])
    for row in native.query("Transaction", where=[["desc", "begins", s(SANDBOX)]], fields=[])["items"]:
        if native.get("Transaction", row["gid"]):
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
            for entity in ("Account", "Transaction", "Category", "Payee", "Tag", "ScheduledTransactionHandler", "Budget",
                           "Image")}


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


def _png(path, width, height, rgb):
    """A small valid PNG to attach."""
    rows = b"".join(b"\0" + bytes(rgb) * width for _ in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


def test_attachments_survive_edits_and_go_with_the_transaction(live, tmp_path):
    accounts, transactions, native = live["accounts"], live["transactions"], live["native"]
    source = accounts.create(group_id=live["group"], name=SANDBOX + "RCPT", kind="cash", currency="CNY", opening_balance="50")
    target = accounts.create(group_id=live["group"], name=SANDBOX + "RCPT2", kind="cash", currency="CNY")
    first, second = _png(tmp_path / "a.png", 64, 48, (200, 30, 30)), _png(tmp_path / "b.png", 32, 32, (30, 30, 200))
    try:
        expense = transactions.create(account=source["id"], kind="expense", amount_="12", date_="2026-11-15",
                                      description=SANDBOX + "receipt")
        transactions.add_attachment(expense["id"], str(first))
        attached = transactions.add_attachment(expense["id"], str(second))["attachments"]
        assert [x["index"] for x in attached] == [0, 1]
        saved = transactions.get_attachment(expense["id"], 0, save_to=str(tmp_path / "out.png"))
        assert (tmp_path / "out.png").read_bytes() == first.read_bytes() and saved["size"] == first.stat().st_size
        copies = [x["path"] for x in _images_of(native, expense["id"])]
        assert all(os.path.exists(p) for p in copies)
        # MoneyWiz's edit methods set the image list to what they are given; edits must keep it.
        assert len(transactions.update(expense["id"], memo="edited")["attachments"]) == 2
        left = transactions.remove_attachment(expense["id"], 0)["attachments"]
        assert len(left) == 1 and not os.path.exists(copies[0])

        transfer = transactions.create(account=source["id"], kind="transfer", amount_="5", date_="2026-11-15",
                                       transfer_to=target["id"], description=SANDBOX + "rcpt-xfer")
        transactions.add_attachment(transfer["id"], str(first))
        edited = transactions.update(transfer["id"], memo="x")
        assert edited["attachments"] and transactions.get(edited["other_leg_id"])["attachments"]

        transactions.delete(expense["id"])
        assert not os.path.exists(copies[1]), "MoneyWiz deletes its copy with the transaction"
    finally:
        for account in (source, target):
            for row in _transactions_of(native, account["id"]):
                if native.get("Transaction", row["gid"]):
                    transactions.delete(row["gid"])
            accounts.delete(account["id"])


def _images_of(native, transaction_id):
    from moneywiz_mcp.transactions import _images
    return _images(native.get("Transaction", transaction_id, ["images.sortOrder", "images.imageAbsolutePath",
                                                               "images.receiptAsset.originalFileName",
                                                               "images.receiptAsset.mimeType",
                                                               "images.receiptAsset.byteSize"]))


def test_split_across_accounts_and_refunds_going_with_their_expense(live):
    accounts, catalog, transactions, native = live["accounts"], live["catalog"], live["transactions"], live["native"]
    one = accounts.create(group_id=live["group"], name=SANDBOX + "PAY1", kind="cash", currency="CNY", opening_balance="100")
    two = accounts.create(group_id=live["group"], name=SANDBOX + "PAY2", kind="cash", currency="CNY", opening_balance="100")
    category = catalog.create_category(SANDBOX + "SPLIT", "expense")
    try:
        parts = transactions.create_split(kind="expense", parts=[{"account": one["id"], "amount": "20"},
                                                                 {"account": two["id"], "amount": "10"}],
                                          date_="2026-11-15", description=SANDBOX + "split", categories=category["id"])
        assert [(p["account_id"], p["amount"]) for p in parts] == [(one["id"], "-20"), (two["id"], "-10")]
        assert [p["categories"][0]["amount"] for p in parts] == ["-20", "-10"], "the split follows each part"
        income = transactions.create_split(kind="income", parts=[{"account": one["id"], "amount": "3"},
                                                                 {"account": two["id"], "amount": "4"}],
                                           date_="2026-11-15", description=SANDBOX + "split-in")
        assert [p["amount"] for p in income] == ["3", "4"]
        refunds = transactions.create_split(kind="refund", parts=[{"account": one["id"], "amount": "2"},
                                                                  {"account": two["id"], "amount": "1"}],
                                            date_="2026-11-16", description=SANDBOX + "split-ref", refund_of=parts[0]["id"])
        assert all(r["refund_of_ids"] == [parts[0]["id"]] for r in refunds)
        assert [accounts.get(x["id"])["balance"] for x in (one, two)] == ["85", "95"]
        # MoneyWiz deletes an expense's refunds with it; the result says so.
        removed = transactions.delete(parts[0]["id"])
        assert set(removed["deleted"]) == {parts[0]["id"], refunds[0]["id"], refunds[1]["id"]}
        with pytest.raises(MoneyWizError):
            transactions.create_split(kind="expense", parts=[{"account": one["id"], "amount": "1"}], date_="2026-11-15")
    finally:
        for account in (one, two):
            for row in _transactions_of(native, account["id"]):
                if native.get("Transaction", row["gid"]):
                    transactions.delete(row["gid"])
            accounts.delete(account["id"])
        catalog.delete("Category", category["id"])


def test_budget_transfers_edits_order_and_warnings(live):
    accounts, catalog, transactions, native = live["accounts"], live["catalog"], live["transactions"], live["native"]
    budgets = Budgets(native)
    account = accounts.create(group_id=live["group"], name=SANDBOX + "BUD", kind="cash", currency="CNY", opening_balance="500")
    category = catalog.create_category(SANDBOX + "BUDCAT", "expense")
    try:
        first = budgets.create(name=SANDBOX + "ONE", amount_="100", categories=[category["id"]], accounts=[account["id"]],
                               start_date="2026-11-01")
        second = budgets.create(name=SANDBOX + "TWO", amount_="100", categories=[category["id"]], accounts=[account["id"]],
                                start_date="2026-11-01")
        moved = budgets.transfer(from_budget=first["id"], to_budget=second["id"], amount_="10", date_="2026-11-15",
                                 description=SANDBOX + "bt")
        edited = transactions.update(moved["transfer_id"], amount="15", memo="m")
        assert edited["kind"] == "budget_transfer" and edited["amount"] == "15" and edited["from_budget_id"] == first["id"]
        assert budgets.get(first["id"])["current_period"]["available"] == "85"
        again = transactions.update(moved["transfer_id"], memo="m2")  # what is not given stays
        assert again["amount"] == "15" and budgets.get(first["id"])["current_period"]["available"] == "85"
        transactions.delete(moved["transfer_id"])

        assert [b["id"] for b in budgets.reorder([second["id"], first["id"]])] == [second["id"], first["id"]]
        assert budgets.get(second["id"])["display_order"] == 0 and budgets.get(first["id"])["display_order"] == 1

        # Through the tool: MoneyWiz's own "exceeded" verdict comes back instead of an alert.
        from moneywiz_mcp import server
        try:
            result = server.moneywiz_create_transaction(account=account["id"], kind="expense", amount="150",
                                                        date="2026-11-16", categories=category["id"],
                                                        description=SANDBOX + "big")
        finally:
            server.native.close()
        assert {b["id"] for b in result["budget_warnings"]["exceeded"]} == {first["id"], second["id"]}
    finally:
        drop_budgets(native)
        for row in _transactions_of(native, account["id"]):
            if native.get("Transaction", row["gid"]):
                transactions.delete(row["gid"])
        accounts.delete(account["id"])
        catalog.delete("Category", category["id"])


def test_scheduled_transfer_categories_and_foreign_currency(live):
    accounts, catalog, scheduled, transactions, native = (live["accounts"], live["catalog"], live["scheduled"],
                                                          live["transactions"], live["native"])
    source = accounts.create(group_id=live["group"], name=SANDBOX + "STC", kind="cash", currency="CNY", opening_balance="1000")
    target = accounts.create(group_id=live["group"], name=SANDBOX + "STC2", kind="cash", currency="CNY")
    category = catalog.create_category(SANDBOX + "STCAT", "expense")
    try:
        plain = transactions.create(account=source["id"], kind="transfer", amount_="7", date_="2026-11-15",
                                    transfer_to=target["id"], categories=[{"category": category["id"], "amount": "7"}],
                                    description=SANDBOX + "plain")
        planned = scheduled.create(kind="transfer", account=source["id"], transfer_to=target["id"], amount_="7",
                                   date_="2027-01-10", categories=[{"category": category["id"], "amount": "7"}],
                                   description=SANDBOX + "planned")
        done = scheduled.execute(planned["id"])["transaction"]
        legs = lambda t: [t["categories"][0]["amount"], transactions.get(t["other_leg_id"])["categories"][0]["amount"]]
        assert legs(done) == legs(plain) == ["-7", "7"], "a scheduled transfer records categories like a plain one"

        usd = scheduled.create(kind="expense", account=source["id"], amount_="10", date_="2027-01-10", currency="USD",
                               description=SANDBOX + "usd")
        assert usd["currency"] == "USD" and usd["amount"] == "-10"
        recorded = scheduled.execute(usd["id"])["transaction"]
        assert recorded["currency"] == "CNY" and recorded["original"]["currency"] == "USD"
        assert recorded["original"]["amount"] == "-10"
        assert Decimal(recorded["amount"]) == Decimal(recorded["original"]["amount"]) * Decimal(recorded["original"]["exchange_rate"])
    finally:
        drop_scheduled(native)
        for account in (source, target):
            for row in _transactions_of(native, account["id"]):
                if native.get("Transaction", row["gid"]):
                    transactions.delete(row["gid"])
            accounts.delete(account["id"])
        catalog.delete("Category", category["id"])


def test_saved_reports_and_groups(live):
    accounts, catalog, native = live["accounts"], live["catalog"], live["native"]
    reports, budgets = Reports(native), Budgets(native)
    saved_before = len(reports.list())
    account = accounts.create(group_id=live["group"], name=SANDBOX + "REPA", kind="cash", currency="CNY", opening_balance="10")
    expense = catalog.create_category(SANDBOX + "REPE", "expense")
    income = catalog.create_category(SANDBOX + "REPI", "income")
    payee, tag = catalog.create_named("Payee", SANDBOX + "REPP"), catalog.create_named("Tag", SANDBOX + "REPT")
    budget = budgets.create(name=SANDBOX + "REPB", amount_="10", categories=[expense["id"]], accounts=[account["id"]],
                            start_date="2026-11-01")
    common = dict(accounts=[account["id"]], categories=[expense["id"]], income_categories=[income["id"]],
                  payees=[payee["id"]], tags=[tag["id"]], budgets=[budget["id"]], budget=budget["id"])
    try:
        made = {kind: reports.create(kind=kind, name=SANDBOX + kind, **common) for kind in REPORT_KINDS}
        assert all(made[kind]["kind"] == kind for kind in REPORT_KINDS), "each creator stores its own report type"
        assert native.status()["context"]["pending"] == 0, "nothing is left unsaved"
        assert made["forecast"]["period"] == "next_3_months" and made["forecast"]["include_scheduled"] is True
        custom = reports.create(kind="categories", name=SANDBOX + "range", period="custom", from_date="2026-01-01",
                                to_date="2026-06-30", chart="bar", **common)
        assert custom["from_date"].startswith("2026-01-01") and custom["to_date"].startswith("2026-07-01")
        assert custom["chart"] == "bar"
        with pytest.raises(MoneyWizError):
            reports.create(kind="budget_balance", name=SANDBOX + "nobudget")

        first, second, third = made["categories"]["id"], made["payees"]["id"], made["tags"]["id"]
        group = reports.create_group(name=SANDBOX + "RG", report_ids=[first, second])
        assert group["report_ids"] == [first, second]
        assert reports.update(third, group=group["id"])["group_id"] == group["group_id"]
        assert [x["id"] for x in reports.reorder([third, second, first], group=group["id"])] == [third, second, first]
        renamed = reports.update(second, name=SANDBOX + "renamed")
        assert renamed["name"] == SANDBOX + "renamed" and renamed["display_order"] == 1, "a rename keeps its place"
        assert "group_id" not in reports.update(third, group="")
        removed = reports.delete_group(group["id"])
        assert set(removed["report_ids"]) == {first, second}
        assert all("group_id" not in reports.get(x) for x in (first, second)), "its reports stay, ungrouped"
        for report in [*made.values(), custom]:
            reports.delete(report["id"])
        assert len(reports.list()) == saved_before
    finally:
        drop_reports(native)
        drop_budgets(native)
        accounts.delete(account["id"])
        for item in (expense, income):
            catalog.delete("Category", item["id"])
        catalog.delete("Payee", payee["id"])
        catalog.delete("Tag", tag["id"])


def test_saved_report_figures_leave_nothing_behind(live):
    accounts, catalog, native, transactions = live["accounts"], live["catalog"], live["native"], live["transactions"]
    reports = Reports(native)
    # What a forecast's calculation writes into its context (it records future occurrences of every
    # scheduled transaction); none of it may reach the ledger.
    def schedules():
        def rows(entity, fields):
            return sorted(native.query(entity, fields=fields)["items"], key=lambda r: r["gid"] or "")
        return (rows("ScheduledTransactionHandler", ["executeDate", "executesCount"]),
                native.count("PaymentPlanItem", [["paid", "eq", {"nb": True}]]), native.count("CategoryAssigment"))
    account = accounts.create(group_id=live["group"], name=SANDBOX + "FIGA", kind="cash", currency="CNY",
                              opening_balance="1000")
    food = catalog.create_category(SANDBOX + "FIGFOOD", "expense")
    rent = catalog.create_category(SANDBOX + "FIGRENT", "expense")
    pay = catalog.create_category(SANDBOX + "FIGPAY", "income")
    for amount, category, day in (("30", food, "2026-11-03"), ("20", food, "2026-11-10"), ("500", rent, "2026-11-05")):
        transactions.create(account=account["id"], kind="expense", amount_=amount, date_=day, categories=category["id"],
                            description=SANDBOX + "fig", payee=SANDBOX + "FIGSHOP")
    transactions.create(account=account["id"], kind="income", amount_="800", date_="2026-11-01",
                        categories=pay["id"], description=SANDBOX + "fig")
    schedules_before = schedules()
    november = dict(period="custom", from_date="2026-11-01", to_date="2026-11-30", accounts=[account["id"]],
                    categories=[food["id"], rent["id"]], income_categories=[pay["id"]])
    try:
        def figures(kind, transactions=0, **options):
            report = reports.create(kind=kind, name=SANDBOX + "fig-" + kind, **(november | options))
            data = reports.data(report["id"], transactions=transactions)
            assert native.status()["context"]["pending"] == 0, f"{kind}: computing left something unsaved"
            return data

        cats = figures("categories", transactions=1)
        assert cats["totals"] == {"figures": {"amount": "-550", "percent": "100"}, "currency": "CNY"}
        by_name = {row["name"]: row for row in cats["rows"]}
        assert by_name[SANDBOX + "FIGFOOD"]["figures"]["amount"] == "-50"
        assert by_name[SANDBOX + "FIGFOOD"]["object"] == {"type": "Category", "id": food["id"]}
        assert by_name[SANDBOX + "FIGFOOD"]["transaction_count"] == 2
        assert len(by_name[SANDBOX + "FIGFOOD"]["transactions"]) == 1, "listed up to the count asked for"
        assert by_name[SANDBOX + "FIGRENT"]["figures"]["amount"] == "-500"

        flow = figures("cashflow")
        month = next(row for row in flow["rows"] if row.get("date", "").startswith("2026-11-01"))
        assert month["figures"]["expenses"] == "550" and month["figures"]["incomes"] == "800"
        assert "transactions" not in month and month["transaction_count"] == 4

        stats = figures("statistics")["statistics"]
        assert stats["expenses"]["total"] == "-550" and stats["incomes"]["total"] == "800"
        assert [c["name"] for c in stats["top_expense_categories"]] == [SANDBOX + "FIGRENT", SANDBOX + "FIGFOOD"]
        assert stats["transaction_counts"]["expense"] == 3 and stats["transaction_counts"]["income"] == 1

        balance = figures("balance_over_time")
        row = next(r for r in balance["rows"] if r.get("object", {}).get("id") == account["id"])
        assert row["figures"]["balance"] == "1250"
        assert balance["series"][0]["values"][-1] == "1250" and len(balance["series"][0]["dates"]) >= 2

        table = figures("payees_by_category_table", payees=[SANDBOX + "FIGSHOP"])
        shop = next(r for r in table["rows"] if r["name"] == SANDBOX + "FIGSHOP")
        assert {r["name"]: r["figures"]["amount"] for r in shop["rows"] if r.get("object")} == \
            {SANDBOX + "FIGFOOD": "-50", SANDBOX + "FIGRENT": "-500"}

        forecast = reports.create(kind="forecast", name=SANDBOX + "fig-forecast", accounts=[account["id"]])
        assert any(r.get("object", {}).get("id") == account["id"] for r in reports.data(forecast["id"])["rows"])
        net_worth = reports.create(kind="net_worth", name=SANDBOX + "fig-networth")
        assert "series" in reports.data(net_worth["id"])  # every account; nothing about them is asserted
        assert native.status()["context"]["pending"] == 0
        assert schedules() == schedules_before, "the forecast's scratch occurrences stayed out of the ledger"
        with pytest.raises(MoneyWizError):
            reports.data(SANDBOX + "no such report")
    finally:
        drop_reports(native)
        accounts.delete(account["id"], confirm_transactions=4)
        for item in (food, rent, pay):
            catalog.delete("Category", item["id"])
        for row in catalog.payees(SANDBOX + "FIGSHOP"):
            catalog.delete("Payee", row["id"])


def _brokers(live, *suffixes):
    return [live["accounts"].create(group_id=live["group"], name=SANDBOX + suffix, kind="investment", currency="USD",
                                    opening_balance="10000") for suffix in suffixes]


def _drop(live, *accounts):
    native, transactions = live["native"], live["transactions"]
    for account in accounts:
        for row in _transactions_of(native, account["id"]):
            if native.get("Transaction", row["gid"]):
                transactions.delete(row["gid"])
        live["accounts"].delete(account["id"])
    for holding in native.query("InvestmentHolding", where=[["symbol", "begins", s("MCPTEST")]], fields=[])["items"]:
        Investments(native).delete_holding(holding["gid"], delete_trades=True)


def test_stock_split_in_every_account_holding_the_symbol(live):
    investments, portfolio, accounts = Investments(live["native"]), live["portfolio"], live["accounts"]
    one, two = _brokers(live, "SPLIT1", "SPLIT2")
    try:
        first = investments.trade(account=one["id"], side="buy", symbol="MCPTESTSPL", shares="10", price="100",
                                  date_="2026-11-02", commission="1", holding_type="Stock", description=SANDBOX + "spl")
        investments.trade(account=two["id"], side="buy", symbol="MCPTESTSPL", shares="3", price="90", date_="2026-11-03",
                          holding_type="Stock", description=SANDBOX + "spl")
        portfolio.set_holding_price(first["holding"]["id"], "120", "2026-11-05")

        split = investments.split(symbol="MCPTESTSPL", old_shares="1", new_shares="4", date_="2026-11-10")
        assert split["date"].startswith("2026-11-10T00:00:00"), "the day's start, as MoneyWiz's split editor passes it"
        trades = {t["account_id"]: t for t in split["transactions"]}
        assert set(trades) == {one["id"], two["id"]}
        assert all(t["kind"] == "investment_buy" and t["stock_split"] and t["amount"] == "0"
                   and t["price_per_share"] == "0" for t in trades.values())
        assert (trades[one["id"]]["shares"], trades[two["id"]]["shares"]) == ("30", "9")
        held = {h["account_id"]: h for h in split["holdings"]}
        assert (held[one["id"]]["shares"], held[one["id"]]["cost_basis"]) == ("40", "1001")
        assert held[one["id"]]["price_per_share"] == "30", "the day-before manual price divided by the ratio"
        assert (held[two["id"]]["shares"], held[two["id"]]["cost_basis"]) == ("12", "270")
        assert [accounts.get(a["id"])["balance"] for a in (one, two)] == ["8999", "9730"]

        reverse = investments.split(symbol="MCPTESTSPL", old_shares="10", new_shares="1", date_="2026-11-12")
        assert all(t["kind"] == "investment_sell" and t["stock_split"] for t in reverse["transactions"])
        held = {h["account_id"]: h for h in reverse["holdings"]}
        assert (held[one["id"]]["shares"], held[one["id"]]["cost_basis"], held[one["id"]]["realized_gain"]) == \
            ("4", "1001", "0"), "a reverse split realizes nothing"

        with pytest.raises(MoneyWizError):
            investments.split(symbol="MCPTESTNONE", old_shares="1", new_shares="2", date_="2026-11-10")
        with pytest.raises(MoneyWizError):
            investments.split(symbol="MCPTESTSPL", old_shares="0", new_shares="2", date_="2026-11-10")
        for trade in split["transactions"] + reverse["transactions"]:
            investments.delete_trade(trade["id"])
        assert [h["shares"] for h in portfolio.holdings(symbol="MCPTESTSPL")] in (["10", "3"], ["3", "10"])
    finally:
        _drop(live, one, two)


def test_trades_move_between_accounts_with_their_attachments(live, tmp_path):
    investments, transactions, accounts = Investments(live["native"]), live["transactions"], live["accounts"]
    one, two = _brokers(live, "MOVE1", "MOVE2")
    cash = accounts.create(group_id=live["group"], name=SANDBOX + "MOVECASH", kind="cash", currency="USD")
    receipt = _png(tmp_path / "trade.png", 24, 24, (20, 160, 20))
    try:
        bought = investments.trade(account=one["id"], side="buy", symbol="MCPTESTMOV", shares="5", price="20",
                                   date_="2026-11-04", holding_type="Stock", description=SANDBOX + "mov")
        transactions.add_attachment(bought["transaction"]["id"], str(receipt))
        # MoneyWiz records a trade moved to another account (or symbol) again, deleting
        # the stored attachment files first; the server passes copies along.
        moved = investments.update_trade(bought["transaction"]["id"], account=two["id"])
        assert moved["replaced"] == bought["transaction"]["id"] and moved["transaction"]["account_id"] == two["id"]
        assert moved["holding"]["shares"] == "5" and moved["account_balance"] == "9900"
        assert moved["previous_account"]["balance"] == "10000"
        saved = transactions.get_attachment(moved["transaction"]["id"], 0, save_to=str(tmp_path / "back.png"))
        assert (tmp_path / "back.png").read_bytes() == receipt.read_bytes() and saved["size"] == receipt.stat().st_size
        renamed = investments.update_trade(moved["transaction"]["id"], symbol="MCPTESTMOV2")
        assert renamed["replaced"] == moved["transaction"]["id"] and len(renamed["transaction"]["attachments"]) == 1
        edited = investments.update_trade(renamed["transaction"]["id"], shares="6")
        assert "replaced" not in edited and edited["holding"]["cost_basis"] == "120"
        with pytest.raises(MoneyWizError):
            investments.update_trade(edited["transaction"]["id"], account=cash["id"])
    finally:
        _drop(live, one, two, cash)


def test_income_becomes_a_refund_of_an_expense(live, tmp_path):
    accounts, transactions, catalog, native = live["accounts"], live["transactions"], live["catalog"], live["native"]
    paid = accounts.create(group_id=live["group"], name=SANDBOX + "RFI1", kind="cash", currency="CNY", opening_balance="1000")
    back = accounts.create(group_id=live["group"], name=SANDBOX + "RFI2", kind="cash", currency="CNY", opening_balance="1000")
    food, misc = catalog.create_category(SANDBOX + "RFIFOOD", "expense"), catalog.create_category(SANDBOX + "RFIMISC", "expense")
    earned = catalog.create_category(SANDBOX + "RFIINC", "income")
    try:
        expense = transactions.create(account=paid["id"], kind="expense", amount_="50", date_="2026-11-10",
                                      description=SANDBOX + "dinner", tags=[SANDBOX + "RFIT1"],
                                      categories=[{"category": food["id"], "amount": "30"},
                                                  {"category": misc["id"], "amount": "20"}])
        income = transactions.create(account=back["id"], kind="income", amount_="10", date_="2026-11-12",
                                     description=SANDBOX + "share", categories=earned["id"], tags=[SANDBOX + "RFIT2"],
                                     memo="note", payee=SANDBOX + "Friend")
        transactions.add_attachment(income["id"], str(_png(tmp_path / "i.png", 16, 16, (1, 2, 3))))
        transactions.set_status(income["id"], "reconciled")
        with pytest.raises(MoneyWizError):
            transactions.convert_to_refund(expense["id"], income["id"])
        result = transactions.convert_to_refund(income["id"], expense["id"])
        refund = result["refund"]
        assert result["income_deleted"] and native.get("Transaction", income["id"]) is None
        assert (refund["kind"], refund["amount"], refund["account_id"], refund["refund_of_ids"]) == \
            ("refund", "10", back["id"], [expense["id"]])
        assert (refund["memo"], refund["payee"], refund["reconciled"]) == ("note", SANDBOX + "Friend", True)
        assert sorted(refund["tags"]) == [SANDBOX + "RFIT1", SANDBOX + "RFIT2"]
        assert {c["id"]: c["amount"] for c in refund["categories"]} == {food["id"]: "6", misc["id"]: "4"}, \
            "the expense's categories in proportion"
        assert len(refund["attachments"]) == 1 and all(os.path.exists(x["path"]) for x in _images_of(native, refund["id"]))
        assert accounts.get(back["id"])["balance"] == "1010"
    finally:
        _drop(live, paid, back)
        for category in (food, misc, earned):
            catalog.delete("Category", category["id"])


def test_bulk_status_and_delete(live):
    accounts, transactions, native = live["accounts"], live["transactions"], live["native"]
    one = accounts.create(group_id=live["group"], name=SANDBOX + "BULK1", kind="cash", currency="CNY", opening_balance="100")
    two = accounts.create(group_id=live["group"], name=SANDBOX + "BULK2", kind="cash", currency="CNY", opening_balance="100")
    try:
        ids = [transactions.create(account=one["id"], kind="expense", amount_=str(k), date_="2026-11-14",
                                   description=SANDBOX + f"bulk{k}")["id"] for k in (1, 2, 3)]
        transfer = transactions.create(account=one["id"], kind="transfer", amount_="7", date_="2026-11-14",
                                       transfer_to=two["id"], description=SANDBOX + "bulkx")
        ids.append(transfer["id"])
        pending = transactions.set_status_many(ids, "pending")
        assert pending["changed"] == ids and {x["status"] for x in pending["transactions"]} == {"pending"}
        assert transactions.set_status_many(ids, "pending")["changed"] == []
        both = ids + [transfer["other_leg_id"]]
        reconciled = transactions.set_status_many(both, "reconciled")
        assert all(x["reconciled"] and x["status"] == "cleared" for x in reconciled["transactions"]), \
            "reconciling clears a pending transaction, as in MoneyWiz"
        assert transactions.set_status_many(ids[:2], "unreconciled")["changed"] == ids[:2]
        with pytest.raises(MoneyWizError):
            transactions.set_status_many(ids + ["no-such-transaction"], "cleared")
        refund = transactions.create(account=one["id"], kind="refund", amount_="1", date_="2026-11-15",
                                     refund_of=ids[2], description=SANDBOX + "bulkref")
        gone = transactions.delete_many([ids[0], ids[2], transfer["id"]])
        assert set(gone["deleted"]) == {ids[0], ids[2], transfer["id"], transfer["other_leg_id"], refund["id"]}
        assert set(gone["also_deleted"]) == {transfer["other_leg_id"], refund["id"]}
        assert [accounts.get(a["id"])["balance"] for a in (one, two)] == ["98", "100"]
        assert native.status()["context"]["pending"] == 0
    finally:
        _drop(live, one, two)


def test_deleting_an_account_can_keep_its_transfers(live, tmp_path):
    accounts, transactions = live["accounts"], live["transactions"]
    other = accounts.create(group_id=live["group"], name=SANDBOX + "KEEP", kind="cash", currency="CNY", opening_balance="1000")
    gone = accounts.create(group_id=live["group"], name=SANDBOX + "GONE", kind="cash", currency="CNY", opening_balance="500")
    plain = accounts.create(group_id=live["group"], name=SANDBOX + "GONE2", kind="cash", currency="CNY", opening_balance="500")
    try:
        sent = transactions.create(account=gone["id"], kind="transfer", amount_="40", date_="2026-11-11",
                                   transfer_to=other["id"], description=SANDBOX + "in", memo="m1")
        received = transactions.create(account=other["id"], kind="transfer", amount_="15", date_="2026-11-12",
                                       transfer_to=gone["id"], description=SANDBOX + "out")
        transactions.set_status(received["id"], "pending")
        transactions.add_attachment(sent["id"], str(_png(tmp_path / "t.png", 8, 8, (9, 9, 9))))
        transactions.create(account=gone["id"], kind="expense", amount_="5", date_="2026-11-13", description=SANDBOX + "x")
        balance = accounts.get(other["id"])["balance"]
        with pytest.raises(MoneyWizError):
            accounts.delete(gone["id"], convert_transfers=True)  # the transaction count is still required
        result = accounts.delete(gone["id"], confirm_transactions=3, convert_transfers=True)
        assert (result["deleted_transactions"], result["converted_transfers"]) == (3, 2)
        assert accounts.get(other["id"])["balance"] == balance
        kept = {t["description"]: t for t in transactions.list(account=other["id"])["transactions"]}
        assert (kept[SANDBOX + "in"]["kind"], kept[SANDBOX + "in"]["amount"], kept[SANDBOX + "in"]["memo"]) == \
            ("income", "40", "m1")
        assert len(kept[SANDBOX + "in"]["attachments"]) == 1
        assert (kept[SANDBOX + "out"]["kind"], kept[SANDBOX + "out"]["status"]) == ("expense", "pending")
        # MoneyWiz names the deleted account as the payee of what it converted.
        assert kept[SANDBOX + "in"]["payee"] == SANDBOX + "GONE"

        lost = transactions.create(account=plain["id"], kind="transfer", amount_="9", date_="2026-11-14",
                                   transfer_to=other["id"], description=SANDBOX + "lost")
        result = accounts.delete(plain["id"], confirm_transactions=1)
        assert result["deleted_transfers"] == 1
        assert live["native"].get("Transaction", lost["other_leg_id"]) is None, "without converting, both legs go"
        assert accounts.get(other["id"])["balance"] == balance
    finally:
        for account in (other, gone, plain):
            if live["native"].get("Account", account["id"]):
                _drop(live, account)


def test_unsaved_report_figures_save_and_leave_nothing(live):
    accounts, catalog, native, transactions = live["accounts"], live["catalog"], live["native"], live["transactions"]
    reports = Reports(native)

    def schedules():
        def rows(entity, fields):
            return sorted(native.query(entity, fields=fields)["items"], key=lambda r: r["gid"] or "")
        return (rows("ScheduledTransactionHandler", ["executeDate", "executesCount"]),
                native.count("PaymentPlanItem", [["paid", "eq", {"nb": True}]]), native.count("CategoryAssigment"))
    account = accounts.create(group_id=live["group"], name=SANDBOX + "ADHOC", kind="cash", currency="CNY",
                              opening_balance="1000")
    food = catalog.create_category(SANDBOX + "ADHFOOD", "expense")
    rent = catalog.create_category(SANDBOX + "ADHRENT", "expense")
    pay = catalog.create_category(SANDBOX + "ADHPAY", "income")
    for amount, category, day in (("30", food, "2026-11-03"), ("20", food, "2026-11-10"), ("500", rent, "2026-11-05")):
        transactions.create(account=account["id"], kind="expense", amount_=amount, date_=day, categories=category["id"],
                            description=SANDBOX + "adhoc")
    transactions.create(account=account["id"], kind="income", amount_="800", date_="2026-11-01",
                        categories=pay["id"], description=SANDBOX + "adhoc")
    november = dict(period="custom", from_date="2026-11-01", to_date="2026-11-30", accounts=[account["id"]],
                    categories=[food["id"], rent["id"]], income_categories=[pay["id"]])
    reports_before, schedules_before = native.count("CustomReport"), schedules()
    try:
        def figures(kind, **options):
            data = reports.compute(kind=kind, **(november | options))
            assert native.status()["context"]["pending"] == 0, f"{kind}: something was left unsaved"
            assert native.count("CustomReport") == reports_before, f"{kind}: a report was kept"
            return data

        cats = figures("categories", transactions=1)
        assert cats["report"]["kind"] == "categories"
        assert cats["totals"] == {"figures": {"amount": "-550", "percent": "100"}, "currency": "CNY"}
        by_name = {row["name"]: row for row in cats["rows"]}
        assert by_name[SANDBOX + "ADHFOOD"]["figures"]["amount"] == "-50"
        assert by_name[SANDBOX + "ADHFOOD"]["transaction_count"] == 2
        assert len(by_name[SANDBOX + "ADHFOOD"]["transactions"]) == 1

        flow = figures("cashflow")
        month = next(row for row in flow["rows"] if row.get("date", "").startswith("2026-11-01"))
        assert month["figures"]["expenses"] == "550" and month["figures"]["incomes"] == "800"

        stats = figures("statistics")["statistics"]
        assert stats["expenses"]["total"] == "-550" and stats["incomes"]["total"] == "800"

        assert any(r.get("object", {}).get("id") == account["id"]
                   for r in figures("forecast", period=None, from_date=None, to_date=None)["rows"])
        assert "series" in figures("net_worth", period="this_year", accounts=None)
        assert schedules() == schedules_before, "the forecast's scratch occurrences stayed out of the ledger"
        with pytest.raises(MoneyWizError):
            reports.compute(kind="no_such_kind")
    finally:
        accounts.delete(account["id"], confirm_transactions=4)
        for item in (food, rent, pay):
            catalog.delete("Category", item["id"])


def test_backup_goes_into_moneywiz_backups_folder(live):
    native = live["native"]
    counts = snapshot(native)
    folder = os.path.expanduser("~/Library/Containers/com.moneywiz.personalfinance/Data/MoneyWizBackups")
    existing = set(os.listdir(folder)) if os.path.isdir(folder) else set()
    made = Backups(native).create()
    try:
        assert "/MoneyWizBackups/" in made["path"] or made["path"].startswith(os.path.expanduser("~"))
        assert made["file_name"].startswith("iMoneyWiz-") and made["file_name"].endswith(".zip")
        assert made["size"] > 0 and os.path.getsize(made["path"]) == made["size"]
        assert native.status()["context"]["pending"] == 0 and snapshot(native) == counts
    finally:
        # Each run would otherwise leave a copy of the whole database in the real backups folder.
        # A backup made in the same minute as an existing one replaces it (same name, as in MoneyWiz);
        # that one is the user's and stays.
        if made["file_name"] not in existing and os.path.basename(made["path"]) == made["file_name"] \
                and os.path.exists(made["path"]):
            os.remove(made["path"])


def test_currency_exchange_in_a_forex_account(live, tmp_path):
    accounts, transactions, native, portfolio = live["accounts"], live["transactions"], live["native"], live["portfolio"]
    investments = Investments(native)
    fx = accounts.create(group_id=live["group"], name=SANDBOX + "FXEX", kind="forex", currency="CNY")
    cash = accounts.create(group_id=live["group"], name=SANDBOX + "FXCASH", kind="cash", currency="CNY")
    try:
        transactions.adjust_balance(account=fx["id"], balance="1000", currency="USD", date_="2026-11-01",
                                    description=SANDBOX + "usd")
        # MoneyWiz values the exchange from its own rates (none are set here).
        made = investments.exchange(account=fx["id"], from_currency="USD", from_amount="100", to_currency="EUR",
                                    to_amount="92", date_="2026-11-05", fee="1", description=SANDBOX + "fx", memo="m")
        trade = made["transaction"]
        assert trade["kind"] == "investment_exchange"
        assert trade["exchange"] == {"from_currency": "USD", "from_amount": "100", "to_currency": "EUR", "to_amount": "92",
                                     "fee": "1", "fee_currency": "USD", "positions": {"USD": "-101", "EUR": "92"}}
        held = {h["symbol"]: h for h in made["holdings"]}
        assert (held["USD"]["shares"], held["EUR"]["shares"]) == ("899", "92")
        assert Decimal(trade["amount"]) == -101 * Decimal(held["USD"]["price_per_share"]), "valued in CNY at the USD rate"
        assert native.status()["context"]["pending"] == 0
        transactions.add_attachment(trade["id"], str(_png(tmp_path / "fx.png", 8, 8, (5, 5, 5))))
        edited = investments.update_exchange(trade["id"], to_amount="93")
        assert edited["transaction"]["id"] == trade["id"] and edited["transaction"]["exchange"]["to_amount"] == "93"
        assert len(edited["transaction"]["attachments"]) == 1
        assert {h["symbol"]: h["shares"] for h in edited["holdings"]}["EUR"] == "93"
        kept = investments.update_exchange(trade["id"], memo="m2")  # what is not given stays
        assert kept["transaction"]["exchange"] == edited["transaction"]["exchange"]
        assert {h["symbol"]: h["shares"] for h in kept["holdings"]} == {h["symbol"]: h["shares"] for h in edited["holdings"]}
        with pytest.raises(MoneyWizError):
            investments.exchange(account=cash["id"], from_currency="USD", from_amount="1", to_currency="EUR",
                                 to_amount="1", date_="2026-11-05")
        with pytest.raises(MoneyWizError):
            investments.exchange(account=fx["id"], from_currency="USD", from_amount="1", to_currency="usd",
                                 to_amount="1", date_="2026-11-05")
        investments.delete_trade(trade["id"])
        assert {h["symbol"]: h["shares"] for h in portfolio.holdings(fx["id"])}["USD"] == "1000"
    finally:
        for account in (fx, cash):
            for row in _transactions_of(native, account["id"]):
                if native.get("Transaction", row["gid"]):
                    transactions.delete(row["gid"])
            for holding in portfolio.holdings(account["id"]):
                investments.delete_holding(holding["id"], delete_trades=True)
            accounts.delete(account["id"])


def _stored(native, entity, gid, fields):
    """Fields exactly as stored (tagged dates as seconds), without the row's own keys."""
    rows = native.query(entity, where=[["GID", "eq", s(gid)]], fields=fields, raw=True)["items"]
    assert len(rows) == 1
    return {k: v for k, v in rows[0].items() if k not in ("$ref", "gid")}


def test_edits_leave_what_they_are_not_given_alone(live):
    """An edit hands every value it is not given back exactly as stored. Up to 0.1.7 a rename
    took the account out of its budgets (deleting a budget left monitoring nothing), turned its
    online-banking switches off and moved an investment account's cash date to now; a memo edit
    flipped negative amounts, added a transfer's fee to its amount again, unlinked an expense from
    its forex position, dropped a foreign amount when the amount changed, and rounded dates to
    the second and trade figures to 15 digits."""
    native, accounts, transactions, catalog = live["native"], live["accounts"], live["transactions"], live["catalog"]
    budgets, investments = Budgets(native), Investments(native)
    group = live["group"]
    cash = accounts.create(group_id=group, name=SANDBOX + "KEEP", kind="cash", currency="CNY", opening_balance="500")
    cash2 = accounts.create(group_id=group, name=SANDBOX + "KEEP2", kind="cash", currency="CNY")
    usd = accounts.create(group_id=group, name=SANDBOX + "KEEPUSD", kind="cash", currency="USD")
    card = accounts.create(group_id=group, name=SANDBOX + "KEEPCARD", kind="credit_card", currency="CNY", credit_limit="1000")
    invest = accounts.create(group_id=group, name=SANDBOX + "KEEPINV", kind="investment", currency="USD",
                             opening_balance="100", opening_date="2026-11-01")
    forex = accounts.create(group_id=group, name=SANDBOX + "KEEPFX", kind="forex", currency="CNY")
    category = catalog.create_category(SANDBOX + "KEEPCAT", "expense")
    budget = budgets.create(name=SANDBOX + "KEEP", amount_="100", categories=[category["id"]],
                            accounts=[cash["id"], card["id"]], currency="CNY")
    sole = budgets.create(name=SANDBOX + "KEEPSOLE", amount_="100", categories=[category["id"]], accounts=[cash2["id"]],
                          currency="CNY")
    try:
        # Accounts.
        settings = ["autoCategorizeOBTransactions", "autoCreateNewOBCategories", "autoDetectOBTransactionsPayees",
                    "autoCreateNewOBPayees", "onlineBankBalanceFetchMode", "balanceDisplayMode", "infoDisplayMode",
                    "cashAsOfDate", "openingBalance", "creditLimit", "statementEndDay", "includeInNetworth", "info"]
        assert _stored(native, "Account", card["id"], ["infoDisplayMode"])["infoDisplayMode"] == 0, "as the app's own cards"
        for account in (cash, cash2, card, invest, forex):
            before = _stored(native, "Account", account["id"], settings)
            accounts.update(account["id"], name=account["name"] + "-RENAMED")
            assert _stored(native, "Account", account["id"], settings) == before, account["name"]
        assert {a["id"] for a in budgets.get(budget["id"])["accounts"]} == {cash["id"], card["id"]}
        assert budgets.get(sole["id"])["accounts"] == [{"id": cash2["id"], "name": SANDBOX + "KEEP2-RENAMED"}]
        changed = accounts.update(card["id"], note="n", credit_limit="2000")
        assert changed["note"] == "n" and changed["credit_limit"] == "2000"

        # Transactions: a memo edit changes nothing else, on either leg.
        fields = ["amount", "fee", "originalAmount", "originalCurrency", "originalExchangeRate", "originalFee", "date",
                  "categoriesAssigments.amount", "symbol", "numberOfShares", "investmentHolding.GID", "status"]

        def memo_keeps(transaction_id):
            ids = [transaction_id] + [x for x in [transactions.get(transaction_id).get("other_leg_id")] if x]
            before = [_stored(native, "Transaction", x, fields) for x in ids]
            transactions.update(transaction_id, memo=SANDBOX + "memo")
            assert [_stored(native, "Transaction", x, fields) for x in ids] == before
            return transactions.get(transaction_id)

        purchase = transactions.create(account=cash["id"], kind="expense", amount_="30", date_="2026-11-09",
                                       description=SANDBOX + "purchase")
        cases = [dict(kind="income", amount_="-5"), dict(kind="expense", amount_="-5"),
                 dict(kind="refund", amount_="-5", refund_of=purchase["id"]),
                 dict(kind="expense", amount_="20", fee="1", categories=category["id"]),
                 dict(kind="income", amount_="20", fee="1"),
                 dict(kind="transfer", amount_="20", fee="1", transfer_to=cash2["id"], categories=category["id"]),
                 dict(kind="transfer", amount_="70", fee="1", transfer_to=usd["id"], transfer_amount="10"),
                 dict(kind="expense", amount_="72", original_amount="10", original_currency="USD"),
                 dict(kind="expense", amount_="3", date_="2026-11-10T10:00:00.25+08:00")]
        made = {}
        for number, case in enumerate(cases):
            case = {"date_": "2026-11-10", **case}
            made[number] = memo_keeps(transactions.create(account=cash["id"], description=SANDBOX + f"keep{number}",
                                                          **case)["id"])
        assert made[0]["amount"] == "-5" and made[2]["amount"] == "-5"
        assert made[5]["amount"] == "-21" and transactions.get(made[5]["other_leg_id"])["amount"] == "20"
        # A new amount keeps the foreign amount and gives the rate.
        rated = transactions.update(made[7]["id"], amount="80")
        assert rated["original"] == {"currency": "USD", "amount": "-10", "exchange_rate": "8"}

        # An expense booked against a forex position stays booked against it.
        transactions.adjust_balance(account=forex["id"], balance="100", currency="USD", date_="2026-11-01")
        args = [ref("Account", forex["id"]), NIL, s(SANDBOX + "fxspend"), NIL, s("CNY"), n(35.0), n(1.0), n(35.0),
                n(0.0), date(parse_when("2026-11-10")), NIL, NIL, NIL, NIL, s(""), NIL, NIL, s("USD"), n(5.0)]
        spent = native.invoke("manager", CREATE_EXPENSE, args)["value"]["gid"]
        position = [["investmentAccount.GID", "eq", s(forex["id"])], ["symbol", "eq", s("USD")]]
        assert native.query("InvestmentHolding", where=position, fields=["numberOfShares"])["items"][0]["numberOfShares"] == 95
        memo_keeps(spent)
        assert native.query("InvestmentHolding", where=position, fields=["numberOfShares"])["items"][0]["numberOfShares"] == 95

        # A trade's figures stay exact through a description edit.
        bought = investments.trade(account=invest["id"], side="buy", symbol="MCPTESTK", shares="3.141592653589793",
                                   price="10.123456789012345", commission="-0.123456789", date_="2026-11-05")
        trade = ["numberOfShares", "pricePerShare", "fee", "amount", "date"]
        before = _stored(native, "Transaction", bought["transaction"]["id"], trade)
        investments.update_trade(bought["transaction"]["id"], description=SANDBOX + "renamed")
        assert _stored(native, "Transaction", bought["transaction"]["id"], trade) == before
        assert native.status()["context"]["pending"] == 0
    finally:
        drop_budgets(native)
        for account in (cash, cash2, usd, card, invest, forex):
            for row in _transactions_of(native, account["id"]):
                if native.get("Transaction", row["gid"]):
                    transactions.delete(row["gid"])
            for holding in live["portfolio"].holdings(account["id"]):
                investments.delete_holding(holding["id"], delete_trades=True)
            accounts.delete(account["id"])
        catalog.delete("Category", category["id"])