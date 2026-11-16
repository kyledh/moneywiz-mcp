import pytest

from moneywiz_mcp import native
from moneywiz_mcp.native import MoneyWizError, Native


def unsaved():
    error = MoneyWizError("MoneyWiz has unsaved changes: inserted CustomReport x1 (a report MoneyWiz is still calculating)")
    error.kind = "UnsavedChanges"
    return error


class Scripted(Native):
    """A Native whose agent replies come from a list instead of MoneyWiz."""

    def __init__(self, replies):
        super().__init__()
        self.replies, self.calls = list(replies), 0

    def _call(self, name, *args):
        self.calls += 1
        self.last = args
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(native.time, "sleep", lambda _: None)


def test_write_waits_for_the_apps_own_unsaved_objects():
    n = Scripted([unsaved(), unsaved(), {"value": 1}])
    assert n.invoke("manager", "saveData", [], encoding="B16@0:8") == {"value": 1}
    assert n.calls == 3


def test_write_reports_changes_that_outlast_the_wait(monkeypatch):
    monkeypatch.setattr(native, "UNSAVED_WAIT", 0)
    n = Scripted([unsaved()])
    with pytest.raises(MoneyWizError, match="still pending after 0s; finish or cancel it"):
        n.invoke("manager", "saveData", [], encoding="B16@0:8")


def test_other_errors_are_not_retried():
    n = Scripted([MoneyWizError("Account not found: x")])
    with pytest.raises(MoneyWizError, match="Account not found"):
        n.invoke("manager", "saveData", [], encoding="B16@0:8")
    assert n.calls == 1


def test_quiet_handlers_travel_with_the_call():
    n = Scripted([{"value": 1}])
    n.invoke("manager", "saveData", [], encoding="B16@0:8", quiet=(("SomeController", "onThing:"),))
    assert n.last[0]["quiet"] == [["SomeController", "onThing:"]]
    n.invoke("manager", "saveData", [], encoding="B16@0:8")
    assert "quiet" not in n.last[0]
