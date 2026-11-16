"""The MCP tool surface: what clients are sent, and the conventions every tool follows."""
import asyncio
import json

import pytest

from moneywiz_mcp import server
from moneywiz_mcp.accounts import brief
from moneywiz_mcp.catalog import Catalog
from moneywiz_mcp.native import MoneyWizError
from moneywiz_mcp.values import find_one


def listed():
    return asyncio.run(server.mcp.list_tools())


def call(name, args):
    result = asyncio.run(server.mcp._tool_manager.call_tool(name, args, convert_result=True))
    return result if isinstance(result, tuple) else (result, None)


def test_a_result_goes_out_once_as_compact_json(monkeypatch):
    # FastMCP sends a list as one indented block per item plus a structured copy; the server sends one text.
    tags = [{"id": "T1", "name": "旅行", "transactions": 3}, {"id": "T2", "name": "work", "transactions": 0}]
    monkeypatch.setattr(server.catalog, "tags", lambda: tags)
    monkeypatch.setattr(server.catalog, "with_uses", lambda entity, items: items)
    content, structured = call("moneywiz_list_tags", {})
    assert structured is None and len(content) == 1
    assert content[0].text == json.dumps(tags, ensure_ascii=False, separators=(",", ":"))


def test_tool_definitions_carry_no_titles_or_output_schemas():
    tools = listed()
    assert tools and all(t.outputSchema is None for t in tools)
    assert '"title"' not in json.dumps([t.inputSchema for t in tools])
    names = [t.name for t in tools]
    assert len(names) == len(set(names)) and all(n.startswith("moneywiz_") for n in names)


def test_titles_go_but_a_property_named_title_stays():
    schema = {"title": "Args", "type": "object", "properties": {"title": {"title": "Title", "type": "string"},
                                                                "tags": {"anyOf": [{"type": "array", "title": "x"}]}}}
    assert server._without_titles(schema) == {"type": "object", "properties": {
        "title": {"type": "string"}, "tags": {"anyOf": [{"type": "array"}]}}}


READS = ("moneywiz_list_", "moneywiz_get_report_data", "moneywiz_compute_report", "moneywiz_get_account",
         "moneywiz_get_transaction", "moneywiz_get_exchange_rate", "moneywiz_status", "moneywiz_net_worth",
         "moneywiz_currency_settings", "moneywiz_loan_schedule")


@pytest.mark.parametrize("tool", listed(), ids=lambda t: t.name)
def test_annotations_follow_what_a_tool_does(tool):
    # Clients ask before running a write and warn before a destructive one, so a tool never mixes
    # a read with a write, and every delete or removal is marked destructive.
    read = tool.name.startswith(READS)
    assert tool.annotations.readOnlyHint is read
    if tool.name.startswith(("moneywiz_delete_", "moneywiz_remove_")):
        assert tool.annotations.destructiveHint is True


def test_lists_of_accounts_show_what_tells_them_apart():
    account = {"id": "A1", "name": "Card", "kind": "credit_card", "currency": "CNY", "balance": "-5",
               "available_balance": "CNY 995.00", "opening_balance": "0", "group_id": 7, "credit_limit": "1000",
               "display_order": 3, "include_in_networth": True, "icon": "x", "archived": False}
    assert brief(account) == {"id": "A1", "name": "Card", "kind": "credit_card", "currency": "CNY", "balance": "-5",
                              "group_id": 7, "credit_limit": "1000"}
    assert brief(account | {"archived": True})["archived"] is True


def test_a_group_is_found_by_id_number_or_name():
    groups = [{"id": "G-1", "group_id": 7000000000000000123, "name": "Home"},
              {"id": "G-2", "group_id": 5, "name": "Trips"}, {"id": "G-3", "group_id": 6, "name": "Trips"}]
    assert find_one(groups, "G-1", "account group")["name"] == "Home"
    assert find_one(groups, "7000000000000000123", "account group")["id"] == "G-1", "a number given as text"
    assert find_one(groups, 5, "budget group")["id"] == "G-2"
    with pytest.raises(MoneyWizError, match="Several budget groups match 'Trips'"):
        find_one(groups, "Trips", "budget group")
    with pytest.raises(MoneyWizError, match="Report group not found: Nope"):
        find_one(groups, "Nope", "report group")


class Counting:
    """Stands in for Native: counts answer 0, 1, 2, ... in the order asked (or all zero)."""

    def __init__(self, zero=False):
        self.asked, self.zero = [], zero

    def counts(self, specs):
        self.asked = specs
        return [0] * len(specs) if self.zero else list(range(len(specs)))


def test_list_entries_carry_how_many_use_them():
    native = Counting()
    payees = Catalog(native).with_uses("Payee", [{"id": "P1", "name": "Cafe"}, {"id": "P2", "name": "Shop"}])
    # One call; per payee its transactions, then its scheduled transactions.
    assert native.asked == [("Transaction", [["payee.GID", "eq", {"s": "P1"}]]),
                            ("ScheduledTransactionHandler", [["payee.GID", "eq", {"s": "P1"}]]),
                            ("Transaction", [["payee.GID", "eq", {"s": "P2"}]]),
                            ("ScheduledTransactionHandler", [["payee.GID", "eq", {"s": "P2"}]])]
    # Transactions are always shown; other users only when there are any.
    assert payees == [{"id": "P1", "name": "Cafe", "transactions": 0, "scheduled": 1},
                      {"id": "P2", "name": "Shop", "transactions": 2, "scheduled": 3}]
    zero = Catalog(Counting(zero=True)).with_uses("Tag", [{"id": "T1", "name": "x"}])
    assert zero == [{"id": "T1", "name": "x", "transactions": 0}]


def test_list_rows_name_accounts_instead_of_repeating_their_ids():
    row = {"id": "T1", "account_id": "A" * 70, "account": "Card", "payee_id": "P1", "payee": "Cafe",
           "transfer_to_id": "A2", "transfer_to": "Cash", "reconciled": False, "other_leg_id": "T2"}
    assert server.named(row, reconciled=row["reconciled"]) == {"id": "T1", "account": "Card", "payee": "Cafe",
                                                               "transfer_to": "Cash", "other_leg_id": "T2"}
    assert server.named(row | {"reconciled": True}, reconciled=True)["reconciled"] is True
    assert server.named({"account_id": "A1"}) == {"account_id": "A1"}, "an id without a name stays"
