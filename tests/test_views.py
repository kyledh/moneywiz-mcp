"""The row -> tool-output mapping, on rows shaped exactly like the agent's."""
from moneywiz_mcp.accounts import view as account_view
from moneywiz_mcp.native import SIGNATURES, decode
from moneywiz_mcp.portfolio import period
from moneywiz_mcp.transactions import view as transaction_view


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


def test_decode_unwraps_agent_tags():
    assert decode({"$date": 1767225600}) == "2026-01-01T00:00:00+00:00"
    assert decode({"$dec": "12.50"}) == "12.50"
    assert decode({"$int": "7000000000000000123"}) == 7000000000000000123
    assert decode([{"a": {"$dec": "1"}}]) == [{"a": "1"}]


def test_repeat_period_only_names_verified_units():
    assert period(1, 8) == "every month"
    assert period(2, 4) == "every 2 years"
    assert period(1, 2) == "1 x unit 2"  # not verified against the app: reported raw
    assert period(0, 8) is None


def test_shipped_signatures_cover_the_selectors_the_domains_call():
    import inspect

    from moneywiz_mcp import accounts, catalog, portfolio, settings, transactions

    for module in (accounts, catalog, portfolio, settings, transactions):
        source = inspect.getsource(module)
        for name in dir(module):
            value = getattr(module, name)
            if isinstance(value, str) and value.endswith(":") and "invoke" in source and " " not in value:
                assert value in SIGNATURES, f"{module.__name__}.{name} is not a known build-466 selector"
