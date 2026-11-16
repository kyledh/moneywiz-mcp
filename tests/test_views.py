"""The row -> tool-output mapping, on rows shaped exactly like the agent's."""
from datetime import datetime, timezone

from moneywiz_mcp.accounts import view as account_view
from moneywiz_mcp.budgets import decode_tags, view as budget_view
from moneywiz_mcp.native import SIGNATURES, decode
from moneywiz_mcp.scheduled import view as scheduled_view
from moneywiz_mcp.transactions import view as transaction_view
from moneywiz_mcp.values import local_tz


def test_account_view_maps_balance_and_kind():
    row = {"$ref": "CreditCardAccount", "gid": "A1", "name": "Card", "currencyName": "USD", "archived": 0,
           "openingBalance": -50.0, "displayOrder": 1, "groupID": 7000000000000000123, "includeInNetworth": 1,
           "includeInGroupBalance": 1, "clearTransactionOnCreation": 1, "enableCheckbookRegister": 0,
           "creditLimit": 2000.0, "statementEndDay": 9, "info": "",
           "balanceDictionary": {"BalanceNumber": -1234.5600000000002, "AvailableBalance": "USD 1,234.56"}}
    view = account_view(row)
    assert view["kind"] == "credit_card"
    assert view["balance"] == "-1234.56"
    assert view["credit_limit"] == "2000"
    assert view["group_id"] == 7000000000000000123
    assert view["archived"] is False


def test_account_view_hides_sentinel_group():
    view = account_view({"$ref": "CashAccount", "gid": "A2", "name": "Cash", "currencyName": "CNY",
                         "archived": 1, "groupID": -1, "balanceDictionary": {}})
    assert "group_id" not in view
    assert view["archived"] is True


def test_transaction_view_reports_split_and_fx():
    row = {"$ref": "WithdrawTransaction", "gid": "T1", "date": "2026-11-10T01:03:41+00:00",
           "amount": -150.0, "desc": "Hotel", "notes": "", "status": 2, "reconciled": 0, "voidCheque": False,
           "originalAmount": -20.0, "originalCurrency": "USD", "originalExchangeRate": 7.5,
           "fee": 0, "account.GID": "A1", "account.name": "Card", "account.currencyName": "CNY",
           "payee.name": "Hotel", "payee.GID": "P1", "tags.name": ["travel"],
           "categoriesAssigments.amount": [-100.0, -50.0],
           "categoriesAssigments.category.GID": ["C1", "C2"],
           "categoriesAssigments.category.name": ["Transit", "Fees"]}
    view = transaction_view(row)
    assert view["kind"] == "expense" and view["status"] == "cleared"
    assert view["amount"] == "-150"
    assert [c["amount"] for c in view["categories"]] == ["-100", "-50"]
    assert view["original"] == {"currency": "USD", "amount": "-20", "exchange_rate": "7.5"}
    assert "fee" not in view


def test_transaction_view_links_transfer_legs():
    out_leg = transaction_view({"$ref": "TransferWithdrawTransaction", "gid": "T2", "amount": -80.0,
                                "status": 1, "reconciled": 0, "account.currencyName": "USD",
                                "recipientAccount.GID": "A3", "recipientAccount.name": "Card",
                                "recipientTransaction.GID": "T3"})
    assert out_leg["kind"] == "transfer_out" and out_leg["status"] == "pending"
    assert out_leg["transfer_to_id"] == "A3" and out_leg["other_leg_id"] == "T3"
    in_leg = transaction_view({"$ref": "TransferDepositTransaction", "gid": "T3", "amount": 80.0,
                               "status": 2, "reconciled": 1, "account.currencyName": "USD",
                               "senderTransaction.GID": "T2", "senderTransaction.account.name": "Savings",
                               "senderTransaction.account.GID": "A4"})
    assert in_leg["kind"] == "transfer_in" and in_leg["reconciled"] is True
    assert in_leg["transfer_from_id"] == "A4" and in_leg["other_leg_id"] == "T2"


def test_balance_adjustment_reports_the_balance_it_set():
    cash = transaction_view({"$ref": "ReconcileTransaction", "gid": "R1", "amount": -20.0, "status": 2, "reconciled": 0,
                             "account.currencyName": "CNY", "reconcileAmount": 50.0, "originalCurrency": "CNY"})
    assert cash["kind"] == "reconcile" and cash["amount"] == "-20" and cash["balance_set_to"] == "50"
    fx = transaction_view({"$ref": "ReconcileTransaction", "gid": "R2", "amount": 667.12, "status": 2, "reconciled": 0,
                           "account.currencyName": "CNY", "reconcileAmount": 667.12, "reconcileNumberOfShares": 100.0,
                           "originalCurrency": "USD", "originalAmount": 100.0, "originalExchangeRate": 0.0,
                           "investmentHolding.symbol": "USD", "numberOfShares": 100.0, "pricePerShare": 0.0})
    assert fx["balance_set_to"] == {"currency": "USD", "amount": "100"}
    assert "original" not in fx and "symbol" not in fx


def test_decode_unwraps_agent_tags():
    assert decode({"$date": 1767225600}) == "2026-01-01T00:00:00+00:00"
    assert decode({"$dec": "12.50"}) == "12.50"
    assert decode({"$int": "7000000000000000123"}) == 7000000000000000123
    assert decode([{"a": {"$dec": "1"}}]) == [{"a": "1"}]


def test_budget_view_and_tag_filter():
    row = {"$ref": "Budget", "gid": "B1", "name": "Food", "type": 0, "currencyName": "CNY", "isRepeatable": 1,
           "duration": 1, "durationUnits": 8, "startDate": "2026-10-31T16:00:00+00:00", "endDate": None,
           "isPeriodMoneyTransferEnabled": 1, "carriedBalance": 0, "openingBalance": 100.0, "groupID": 7000000000000000001,
           "iconFileName": "mw2_category_icon_260", "tagsPredicateToMonitor": "%E6%97%85%E8%A1%8C OR trip",
           "accountsLinksToMonitor.account.GID": ["A1"], "accountsLinksToMonitor.account.name": ["Cash"],
           "categoriesAssigmentsToMonitor.category.GID": ["C1"], "categoriesAssigmentsToMonitor.category.name": ["Food"]}
    view = budget_view(row, {"number": 0, "budgeted": "100", "spent": "120", "remaining": "-20"})
    assert view["kind"] == "expense" and view["repeat"] == {"every": 1, "unit": "month"} and view["rollover"] is True
    assert view["tags"] == ["旅行", "trip"] and view["tags_match"] == "any" and "tag_filter" not in view
    assert view["current_period"]["remaining"] == "-20" and "amount" not in view
    # Stored as local midnight of the day after the last day (whatever the machine's zone is).
    next_day = datetime(2026, 12, 1, tzinfo=local_tz()).astimezone(timezone.utc).isoformat()
    once = budget_view(row | {"isRepeatable": 0, "type": 1, "endDate": next_day, "tagsPredicateToMonitor": None})
    assert once["kind"] == "income" and once["amount"] == "100" and "repeat" not in once and "rollover" not in once
    assert once["end_date"].startswith("2026-11-30T23:59:59")
    assert decode_tags("a AND b") == (["a", "b"], "all")
    assert decode_tags("a OR b AND c") == (None, None)


def test_scheduled_view_reads_the_raw_row():
    # Raw rows keep the agent's tagged dates. A week is stored as 7 days, and
    # executeDate runs ahead of the next due date while one is overdue.
    row = {"$ref": "ScheduledWithdrawTransactionHandler", "gid": "S1", "desc": "Rent", "notes": "",
           "amount": -60.0, "currencyName": "CNY", "isRepeatable": 1, "duration": 14, "durationUnits": 16,
           "weekendsHandler": 2, "weekdayNumber": 1001, "weekdayName": 8, "installments": 0,
           "executeDate": {"$date": 1798689600}, "firstWaitingExecuteDate": {"$date": 1797480000},
           "firstExecuteDate": {"$date": 1767225600}, "lastExecuteDate": None, "autoPay": 0,
           "disableExecution": 0, "executesCount": 3, "waitingExecutesCount": 1,
           "account.GID": "A1", "account.name": "Cash", "payee.name": "Landlord", "tags.name": [],
           "categoriesAssigments.category.GID": ["C1"], "categoriesAssigments.category.name": ["Rent"],
           "categoriesAssigments.amount": [-60.0]}
    view = scheduled_view(row)
    assert view["kind"] == "expense" and view["amount"] == "-60"
    assert view["repeat"] == {"every": 14, "unit": "day"}
    assert view["on"] == {"week_of_month": "last", "day_of_week": "weekday"}
    assert view["weekends"] == "after"
    assert view["next_due"].startswith("2026-12-1") and view["due_now"] == 1
    assert "end_date" not in view and "occurrences" not in view and "finished" not in view
    assert view["categories"] == [{"id": "C1", "name": "Rent", "amount": "-60"}]

    done = scheduled_view(row | {"isRepeatable": 0, "disableExecution": 1, "waitingExecutesCount": 0}, finished=True)
    assert done["repeats"] is False and done["finished"] is True
    assert "next_due" not in done and "repeat" not in done and "on" not in done


def test_shipped_signatures_cover_the_selectors_the_domains_call():
    import inspect

    from moneywiz_mcp import accounts, budgets, catalog, investments, portfolio, scheduled, settings, transactions

    for module in (accounts, budgets, catalog, investments, portfolio, scheduled, settings, transactions):
        source = inspect.getsource(module)
        for name in dir(module):
            if name.startswith("_"):
                continue  # fragments such as a shared selector tail
            value = getattr(module, name)
            for selector in value.values() if isinstance(value, dict) else [value]:
                if isinstance(selector, str) and selector.endswith(":") and "invoke" in source and " " not in selector:
                    assert selector in SIGNATURES, f"{module.__name__}.{name} is not a known build-466 selector"
