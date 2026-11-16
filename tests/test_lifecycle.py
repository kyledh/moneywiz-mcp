"""The rules for a MoneyWiz the server launched (lifecycle.py), against a stand-in app."""
import os

from moneywiz_mcp import lifecycle as lc


class App:
    """Stands in for Native: one MoneyWiz process and the agent's answers."""

    def __init__(self, pid=4242):
        self.pid = pid
        self.state = {"in_use": False, "background": False, "pending": 0, "cloud": {"busy": False}}
        self.quit_answer = {"quit": True}
        self.stuck = False  # agrees to quit but does not exit (a dialog in the way)
        self.calls = []

    def running_pid(self):
        return self.pid

    def close(self):
        self.calls.append(("close",))

    def peek(self, name, *args):
        self.calls.append((name, *args[:1]) if name == "background" else (name,))
        if name == "appState":
            return dict(self.state)
        if name == "background":
            self.state["background"] = args[0]
            return {"background": args[0]}
        if name == "quit":
            if self.quit_answer.get("quit") and not self.stuck:
                self.pid = None
            return self.quit_answer
        raise AssertionError(name)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path, app=None, idle=10):
    app, clock = app or App(), Clock()
    return app, clock, lc.Lifecycle(app, idle_minutes=idle, path=tmp_path / "lease.json", clock=clock)


def read(life):
    with lc.lease(life.path) as data:
        return dict(data)


def test_a_moneywiz_the_user_opened_is_left_alone(tmp_path):
    app, clock, life = make(tmp_path)
    clock.now += 3600
    life.tick()
    assert app.calls == [], "no lease: not even looked at"
    assert life.describe() == {"managed_by_server": False}


def test_launched_hidden_kept_out_of_the_dock_and_quit_when_idle(tmp_path):
    app, clock, life = make(tmp_path)
    life.launched(app.pid)
    life.tick()
    assert ("background", True) in app.calls and ("quit",) not in app.calls
    clock.now += 9 * 60
    life.used()  # a tool call resets the idle time
    clock.now += 9 * 60
    life.tick()
    assert ("quit",) not in app.calls
    clock.now += 2 * 60
    life.tick()
    assert ("quit",) in app.calls and app.pid is None
    assert app.calls[app.calls.index(("quit",)) + 1] == ("close",), "detached so it can exit at once"
    assert read(life) == {}, "the lease goes with the process"


def test_opening_it_hands_it_to_the_user(tmp_path):
    app, clock, life = make(tmp_path)
    life.launched(app.pid)
    life.tick()
    app.state["in_use"] = True  # a window shown or the app brought to the front
    life.tick()
    assert app.state["background"] is False, "back in the Dock"
    assert read(life)["owner"] == "user"
    app.state["in_use"] = False  # hidden again later: still the user's
    clock.now += 3600
    calls = len(app.calls)
    life.tick()
    assert app.calls[calls:] == [] and app.pid is not None
    assert life.describe() == {"managed_by_server": False}


def test_no_quit_while_icloud_is_busy_or_after_a_restart(tmp_path):
    app, clock, life = make(tmp_path, idle=1)
    life.launched(app.pid)
    app.quit_answer = {"quit": False, "reason": "iCloud sync in progress"}
    clock.now += 120
    life.tick()
    assert app.pid is not None and life.describe()["not_quit_because"] == "iCloud sync in progress"
    app.pid = 5151  # MoneyWiz restarted (by the user): not ours
    life.tick()
    assert read(life) == {} and life.managed_pid() is None


def test_exit_quits_unless_another_server_still_uses_it(tmp_path):
    app, clock, life = make(tmp_path)
    life.launched(app.pid)
    with lc.lease(life.path) as data:
        data["clients"][str(os.getppid())] = clock.now  # another live server, just now
    life.at_exit()
    assert app.pid is not None, "the other server goes on using it"

    app, clock, life = make(tmp_path / "b")
    (tmp_path / "b").mkdir()
    life.launched(app.pid)
    with lc.lease(life.path) as data:
        data["clients"]["999999999"] = clock.now  # a server that is gone
    life.at_exit()
    assert app.pid is None


def test_idle_zero_never_quits(tmp_path):
    app, clock, life = make(tmp_path, idle=0)
    life.launched(app.pid)
    clock.now += 10 ** 6
    life.tick()
    assert ("quit",) not in app.calls and ("background", True) in app.calls


def test_a_moneywiz_that_does_not_exit_is_left_to_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "EXIT_SETTLE", 0.0)
    app, clock, life = make(tmp_path, idle=1)
    app.stuck = True
    life.launched(app.pid)
    clock.now += 120
    life.tick()
    assert ("quit",) in app.calls and app.pid is not None
    clock.now += 60
    life.tick()
    assert app.calls.count(("quit",)) == 1, "asked once, then given time"
    clock.now += lc.QUIT_WAIT
    life.tick()
    assert read(life)["owner"] == "user" and life.last_reason == "MoneyWiz did not quit when asked"
