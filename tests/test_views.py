"""The row -> tool-output mapping, on rows shaped exactly like the agent's."""
from datetime import datetime, timezone

from moneywiz_mcp.accounts import view as account_view
from moneywiz_mcp.budgets import decode_tags, view as budget_view
from moneywiz_mcp.native import decode
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


def test_every_call_has_a_recorded_signature():
    # scripts/check_build.py compares exactly this table with a new MoneyWiz build,
    # so every call the server makes must be in it.
    from moneywiz_mcp import calls, native

    missing = sorted(x for x in calls.manager_selectors() if x not in native.MANAGER_SIGNATURES)
    missing += sorted(f"{name} {key}" for name, key in calls.object_calls()
                      if key not in native.CLASS_SIGNATURES.get(name, {}))
    missing += sorted(f"{cls} - {sel}" for cls, sel in calls.quiet_handlers()
                      if "- " + sel not in native.CLASS_SIGNATURES.get(cls, {}))
    assert not missing, f"no recorded signature for {missing}"
    for call in calls.invocations():
        assert call["target"] is not None, f"receiver not written out at {call['module']}:{call['line']}"
        if call["selectors"] is None and call["target"][0] != "manager":
            # chosen at run time among methods the table lists for that receiver
            assert native.CLASS_SIGNATURES.get(call["target"][1]), f"{call['module']}:{call['line']}"


def test_recorded_encoding_is_looked_up_for_every_receiver():
    from moneywiz_mcp.native import MoneyWizError, recorded_encoding
    assert recorded_encoding("manager", "saveData") == "B16@0:8"
    assert recorded_encoding({"cls": "TagsPredicate"}, "ORPredicateStringFromTagsNames:") == "@24@0:8@16"
    assert recorded_encoding({"ref": ["Budget", "B1"]}, "remainingAmountForPeriodIndex:") == "@24@0:8q16"
    assert recorded_encoding("user", "nonInvestmentAccountsArray") == "@16@0:8"
    try:
        recorded_encoding({"cls": "TagsPredicate"}, "somethingNew:")
    except MoneyWizError as error:
        assert "No build-466 signature" in str(error)
    else:
        raise AssertionError("an unrecorded call must be refused")


def test_report_rows_read_moneywiz_columns():
    from moneywiz_mcp.reports import _row, _rows

    category = {"groupName": "Food", "groupObject": {"$ref": "Category", "gid": "C1"}, "groupAmount": -50,
                "groupAmountPercent": 9.090909090909092, "groupValuesNamesArray": ["AMOUNT", "CURRENCY_NAME", "/",
                                                                                  "AMOUNT_PERCENTS", "%"],
                "groupValuesArray": [-50, "CNY", "/", 9.090909090909092, "%"], "transactionsArray#": 2,
                "transactionsArray": [{"GID": "T1", "date": {"$date": 0}, "amount": -30, "desc": "x",
                                       "accountCurrencyName": "CNY", "tagsNames": []}]}
    row = _row(category)
    assert row["object"] == {"type": "Category", "id": "C1"} and row["currency"] == "CNY"
    assert row["figures"] == {"amount": "-50", "percent": "9.09090909"}
    assert row["transaction_count"] == 2 and row["transactions"][0]["id"] == "T1"
    assert "tags" not in row["transactions"][0]

    # A period row with several columns, one of them left without a value: the group amount
    # MoneyWiz leaves at 0 there is not a figure.
    month = _row({"groupName": "Nov", "groupObject": {"$date": 1793462400}, "groupAmount": 0, "groupAmountPercent": 0,
                  "groupValuesNamesArray": ["EXPENSES", "CURRENCY_NAME", "INCOMES", "CURRENCY_NAME",
                                            "TRANSFERS_LOOSES", "CURRENCY_NAME"],
                  "groupValuesArray": [550, "CNY", 800, "CNY"]})
    assert month["figures"] == {"expenses": "550", "incomes": "800"} and month["date"].startswith("2026-1")
    # Rows of a table carry their figure as the group amount; a percent of 0 is one not worked out.
    cell = _row({"groupName": "Shop", "groupAmount": -550, "groupAmountPercent": 0,
                 "subGroupesArray": [{"groupName": " "}, {"groupName": "Total", "groupAmount": -550}]})
    assert cell["figures"] == {"amount": "-550"}
    assert [r["name"] for r in cell["rows"]] == ["Total"], "blank spacer rows are dropped"
    assert _rows([{"groupName": "Mixed", "groupValuesNamesArray": ["EXPENSES", "CURRENCY_NAME", "BUDGET_BALANCE",
                                                                   "CURRENCY_NAME"],
                   "groupValuesArray": [50, "CNY", 100, "USD"]}])[0]["currencies"] == \
        {"expenses": "CNY", "budget_balance": "USD"}
    account = _row({"groupName": "Cash", "groupObject": {"$object": "MWAccountDTO"}, "groupObject.GID": "A1"})
    assert account["object"] == {"type": "Account", "id": "A1"}


def test_report_series_and_statistics():
    from moneywiz_mcp.reports import _series, _statistics

    over_time = _series({"seriesName": "Cash", "currencyName": "CNY", "seriesXValue": [{"$date": 0}, {"$date": 86400}],
                         "seriesYValue": [1000, 1250.0000000000002]})
    assert over_time["values"] == ["1000", "1250"] and len(over_time["dates"]) == 2
    pie = _series({"seriesName": "Food", "seriesXValue": [0.57], "seriesYValue": [50, 9.09]})
    assert "dates" not in pie and "labels" not in pie, "slice angles are not data"
    assert _series({"seriesName": "Shop", "seriesXValue": ["Nov"], "seriesYValue": [5]})["labels"] == ["Nov"]

    stats = _statistics({"expensesAllTimeTotal": -550, "expensesDailyAvarage": -18.333333333333332,
                         "expensesCurrencyName": "CNY", "topExpenseCategoriesNamesArray": ["Rent", "Food"],
                         "topExpenseCategoriesAmountsArray": [500, 50],
                         "topExpenseCategoriesPercentsArray": [90.9090909090909, 9.090909090909092],
                         "topIncomePayeeNamesArray": [], "higestNetWorthAmount": 1800,
                         "higestNetWorthDate": {"$date": 0}, "totalExpenseTransactionsCount": 3})
    assert stats["expenses"] == {"daily_average": "-18.33333333", "total": "-550", "currency": "CNY"}
    assert stats["top_expense_categories"][0] == {"name": "Rent", "amount": "500", "percent": "90.90909091"}
    assert "top_income_payees" not in stats
    assert stats["net_worth"]["highest"]["amount"] == "1800" and stats["transaction_counts"] == {"expense": 3}


def test_report_data_checks_every_agent_call_against_the_table():
    from moneywiz_mcp import calls, native
    from moneywiz_mcp.reports import AGENT_CALLS

    listed = native.recorded_calls(AGENT_CALLS)
    assert ["TransactionsFilter", "- setUser:", "v24@0:8@16"] in listed
    assert all(len(entry) == 3 and entry[2] for entry in listed)
    assert ("CustomReport", "- setReportSortingType:") in calls.object_calls()
    assert "forkToChildManagedObjectContext" in calls.manager_selectors()
