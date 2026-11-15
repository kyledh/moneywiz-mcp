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
from moneywiz_mcp.catalog import Catalog
from moneywiz_mcp.investments import Investments
from moneywiz_mcp.native import MoneyWizError, Native, s
from moneywiz_mcp.portfolio import Portfolio
from moneywiz_mcp.settings import Settings
from moneywiz_mcp.transactions import Transactions

SANDBOX = "MCP-TEST-"

pytestmark = pytest.mark.skipif(os.environ.get("MONEYWIZ_LIVE_TESTS") != "1",
                                reason="set MONEYWIZ_LIVE_TESTS=1 to run write tests against the real ledger")


def cleanup(native: Native):
    accounts, catalog, transactions = Accounts(native), Catalog(native), Transactions(native)
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
    yield {"native": native, "accounts": Accounts(native), "catalog": Catalog(native),
           "transactions": Transactions(native), "settings": Settings(native), "portfolio": Portfolio(native),
           "before": snapshot(native)}
    cleanup(native)
    native.close()


def snapshot(native: Native) -> dict:
    return {entity: native.count(entity) for entity in ("Account", "Transaction", "Category", "Payee", "Tag")}


def test_reads_do_not_touch_the_app_state(live):
    native = live["native"]
    assert native.status()["context"]["pending"] == 0
    assert live["accounts"].list(), "the ledger should have at least one account"
    assert live["transactions"].list(limit=1)["total"] >= 0
    assert live["settings"].currencies()["default_currency"]
    assert native.status()["context"]["pending"] == 0


def test_expense_lifecycle(live):
    accounts, catalog, transactions = live["accounts"], live["catalog"], live["transactions"]
    account = accounts.create(name=SANDBOX + "CASH", kind="cash", currency="CNY", opening_balance="100")
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
    source = accounts.create(name=SANDBOX + "FROM", kind="cash", currency="CNY", opening_balance="500")
    target = accounts.create(name=SANDBOX + "TO", kind="cash", currency="USD")
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
    account = accounts.create(name=SANDBOX + "FX", kind="cash", currency="CNY")
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
    account = accounts.create(name=SANDBOX + "REF", kind="cash", currency="CNY", opening_balance="100")
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
    account = accounts.create(name=SANDBOX + "MERGE", kind="cash", currency="CNY")
    keep = catalog.create_named("Payee", SANDBOX + "KEEP")
    drop = catalog.create_named("Payee", SANDBOX + "DROP")
    try:
        created = transactions.create(account=account["id"], kind="expense", amount_="1", date_="2026-11-15",
                                      description=SANDBOX + "merge", payee=drop["name"])
        assert catalog.usage("Payee", drop["id"])["transactions"] == 1
        with pytest.raises(MoneyWizError):
            catalog.delete("Payee", drop["id"])
        catalog.merge("Payee", [drop["id"]], keep["id"])
        assert transactions.get(created["id"])["payee_id"] == keep["id"]
        assert live["native"].get("Payee", drop["id"]) is None
        transactions.delete(created["id"])
    finally:
        catalog.delete("Payee", keep["id"])
        accounts.delete(account["id"])


def test_rejects_bad_input_before_touching_the_app(live):
    accounts, transactions, native = live["accounts"], live["transactions"], live["native"]
    account = accounts.list()[0]
    for bad in (
        dict(account=account["id"], kind="expense", amount_="abc", date_="2026-11-15"),
        dict(account=account["id"], kind="expense", amount_="1", date_="15/11/2026"),
        dict(account=account["id"], kind="expense", amount_="0", date_="2026-11-15"),
        dict(account="no-such-account", kind="expense", amount_="1", date_="2026-11-15"),
        dict(account=account["id"], kind="transfer", amount_="1", date_="2026-11-15"),
        dict(account=account["id"], kind="refund", amount_="1", date_="2026-11-15"),
    ):
        with pytest.raises(MoneyWizError):
            transactions.create(**bad)
    assert native.status()["context"]["pending"] == 0


def test_ledger_is_unchanged_by_the_suite(live):
    assert snapshot(live["native"]) == live["before"]


def test_investment_trade_lifecycle(live):
    accounts, investments, portfolio = live["accounts"], Investments(live["native"]), live["portfolio"]
    account = accounts.create(name=SANDBOX + "BROKER", kind="investment", currency="USD",
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

        with pytest.raises(MoneyWizError):
            investments.delete_holding(sold["holding"]["id"])  # still has trades

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


def test_manual_holding_and_price(live):
    accounts, investments, portfolio = live["accounts"], Investments(live["native"]), live["portfolio"]
    account = accounts.create(name=SANDBOX + "BROKER2", kind="investment", currency="USD", opening_balance="100")
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
