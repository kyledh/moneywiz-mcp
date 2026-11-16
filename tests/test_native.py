import frida
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
    always = [list(h) for h in native.ALWAYS_QUIET]
    n = Scripted([{"value": 1}])
    n.invoke("manager", "saveData", [], encoding="B16@0:8", quiet=(("SomeController", "onThing:"),))
    assert n.last[0]["quiet"] == [["SomeController", "onThing:"], *always]
    n.invoke("manager", "saveData", [], encoding="B16@0:8")
    assert n.last[0]["quiet"] == always, "every write skips the budget warning alert"


def test_questions_are_read_only_and_do_not_wait():
    # A question is answered while MoneyWiz holds unsaved changes (the agent does not
    # refuse it), so there is nothing to wait for, and nothing is saved or muted.
    n = Scripted([{"value": ["USD", "CNY"]}])
    assert n.ask("manager", "currenciesArray", []) == ["USD", "CNY"]
    spec = n.last[0]
    assert spec["read_only"] is True and spec["save"] is False and "quiet" not in spec
    assert spec["encoding"] == native.MANAGER_SIGNATURES["currenciesArray"]
    assert n.calls == 1


class Script:
    """A loaded agent: is_destroyed as Frida reports it, exports that record calls."""

    def __init__(self, log, fail_with=None):
        self.is_destroyed, self.log, self.fail_with = False, log, fail_with
        script = self

        class Exports:
            def invoke(self, spec):
                script.log.append(spec["selector"])  # MoneyWiz has the call from here on
                if script.fail_with:
                    raise script.fail_with
                return {"value": None}
        self.exports_sync = Exports()


def connected(scripts):
    """A Native whose (re)connections hand out the given scripts in turn."""
    n = Native()
    pending = list(scripts)
    def connect(launch=True):
        n._script = pending.pop(0)
    n._connect = connect
    return n


@pytest.mark.parametrize("lost", [frida.InvalidOperationError("script has been destroyed"),
                                  frida.TransportError("connection closed")])
def test_a_call_lost_while_moneywiz_has_it_is_not_sent_again(lost):
    # MoneyWiz may have carried the write out (and saved it) before it quit or crashed:
    # sending it again could record it twice, or crash MoneyWiz a second time.
    log = []
    n = connected([Script(log, fail_with=lost), Script(log)])
    with pytest.raises(MoneyWizError, match="may or may not have been carried out"):
        n.invoke("manager", "saveData", [], encoding="B16@0:8")
    assert log == ["saveData"]
    assert n._script is None, "the next call starts a new connection"


def test_a_connection_that_ended_between_calls_is_replaced_before_sending():
    log = []
    first, second = Script(log), Script(log)
    n = connected([first, second])
    n.invoke("manager", "saveData", [], encoding="B16@0:8")
    first.is_destroyed = True  # MoneyWiz restarted in between
    n.invoke("manager", "saveData", [], encoding="B16@0:8")
    assert log == ["saveData", "saveData"] and n._script is second
