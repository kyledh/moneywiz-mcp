"""MoneyWiz run by the server when nobody else runs it.

When MoneyWiz is not running, the server launches it hidden. While nobody
looks at it, it is kept out of the Dock and Cmd-Tab, and it is quit (as with
Cmd-Q) once no MCP client has used it for a while, or when the last server
using it exits. This applies only to a MoneyWiz the server launched; one the
user opened is never hidden or quit. As soon as MoneyWiz is seen in use (a
window shown, the app brought to the front), it is handed over to the user:
back in the Dock, and never quit by the server again.

Several servers can use the same MoneyWiz (say hermes and Claude), so the
facts are kept in one small file, LEASE: which MoneyWiz process a server
launched, whether it is still the server's, and when each server last used
it. A server that finds a MoneyWiz another one launched takes part in looking
after it.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import time
from pathlib import Path

LEASE = Path.home() / "Library" / "Caches" / "moneywiz-mcp" / "lifecycle.json"
POLL = 3.0  # seconds between looks at a MoneyWiz the server runs
TOUCH_EVERY = 5.0  # write the last-use time at most this often
QUIT_WAIT = 120.0  # how long MoneyWiz may take to exit after it agreed to quit
EXIT_SETTLE = 10.0  # how long quit() watches for the exit before leaving it to the next check
EXIT_WAIT = 15.0  # how long a server that exits waits for iCloud to settle before it quits MoneyWiz
# MoneyWiz methods the agent calls for this (checked against signatures.json first).
AGENT_CALLS = {
    "MWDatabaseManager": ["+ sharedManager", "- activeCloudKitEventCount", "- hasActiveExportEvents",
                          "- isStartupSyncActivityInProgress"],
}


@contextlib.contextmanager
def lease(path: Path | None = None):
    """The lease, read and written under an exclusive lock."""
    path = path or LEASE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


class Lifecycle:
    """Looks after a MoneyWiz the server launched. Native calls launched() and
    used(); start() runs the background check."""

    def __init__(self, native, *, idle_minutes: float = 10, path: Path | None = None, clock=time.time):
        from .native import recorded_calls
        self.native = native
        self.calls = recorded_calls(AGENT_CALLS)
        self.idle = max(0.0, float(idle_minutes)) * 60
        self.path = path or LEASE
        self.clock = clock
        self.me = str(os.getpid())
        self._touched = 0.0
        self._quit_asked: tuple[int, float] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_reason: str | None = None

    # ------------------------------------------------------------ hooks from Native

    def launched(self, pid: int):
        now = self.clock()
        with lease(self.path) as data:
            data.clear()
            data.update(pid=pid, owner="server", launched_at=now, last_use=now, clients={self.me: now})
        self._touched = now

    def used(self):
        now = self.clock()
        if now - self._touched < TOUCH_EVERY:
            return
        self._touched = now
        with lease(self.path) as data:
            data["last_use"] = now
            clients = data.setdefault("clients", {})
            clients[self.me] = now
            for pid in [p for p in clients if p != self.me and not alive(p)]:
                del clients[pid]

    # ------------------------------------------------------------ state

    def managed_pid(self) -> int | None:
        """The running MoneyWiz the server launched and still looks after, if any."""
        with lease(self.path) as data:
            if not data:
                return None
            pid = self.native.running_pid()
            if pid is None or data.get("pid") != pid:
                data.clear()  # that MoneyWiz is gone; a new one is not ours unless we launch it
                return None
            return pid if data.get("owner") == "server" else None

    def describe(self) -> dict:
        pid = self.native.running_pid()
        with lease(self.path) as data:
            ours = pid is not None and data.get("pid") == pid and data.get("owner") == "server"
            last = data.get("last_use") if ours else None
        out = {"managed_by_server": ours}
        if ours:
            out["quits_after_idle_minutes"] = self.idle / 60 if self.idle else None
            out["idle_minutes"] = round((self.clock() - last) / 60, 1) if last else None
            if self.last_reason:
                out["not_quit_because"] = self.last_reason
        return out

    def _others_active(self, data: dict) -> bool:
        now = self.clock()
        return any(pid != self.me and alive(pid) and (not self.idle or now - ts < self.idle)
                   for pid, ts in (data.get("clients") or {}).items())

    # ------------------------------------------------------------ the check

    def tick(self):
        pid = self.managed_pid()
        if pid is None:
            return
        state = self.native.peek("appState", self.calls)
        if state is None:
            return
        if state.get("in_use"):
            # The user opened it: theirs from now on, back in the Dock.
            with lease(self.path) as data:
                if data.get("pid") == pid:
                    data["owner"] = "user"
            if state.get("background"):
                self.native.peek("background", False)
            return
        if not state.get("background"):
            self.native.peek("background", True)
        if self.idle:
            with lease(self.path) as data:
                last = data.get("last_use") or 0
            if self.clock() - last >= self.idle:
                self.quit(pid)

    def quit(self, pid: int) -> bool:
        """Ask MoneyWiz to quit if nothing holds it; True once it has agreed."""
        now = self.clock()
        if self._quit_asked and self._quit_asked[0] == pid:
            if now - self._quit_asked[1] < QUIT_WAIT:
                return True  # still on its way out
            # It did not exit (a dialog may be holding it up): leave it, and look after it no more.
            self.last_reason = "MoneyWiz did not quit when asked"
            with lease(self.path) as data:
                if data.get("pid") == pid:
                    data["owner"] = "user"
            return False
        result = self.native.peek("quit", self.calls)
        if not result or not result.get("quit"):
            self.last_reason = (result or {}).get("reason") or "MoneyWiz is not reachable"
            return False
        self._quit_asked = (pid, now)
        self.last_reason = None
        # Detach now: MoneyWiz quits half a second after agreeing, and an attached
        # session would hold up its exit. The next call reconnects (or relaunches).
        self.native.close()
        deadline = time.monotonic() + EXIT_SETTLE
        while time.monotonic() < deadline:
            if self.native.running_pid() != pid:
                self._done(pid)
                break
            time.sleep(0.5)
        return True

    def _done(self, pid: int):
        self._quit_asked = None
        self.last_reason = None
        with lease(self.path) as data:
            if data.get("pid") == pid:
                data.clear()

    # ------------------------------------------------------------ thread and exit

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="moneywiz-lifecycle", daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.wait(POLL):
            with contextlib.suppress(Exception):  # the check must never take the server down
                self.tick()

    def at_exit(self):
        """The server is exiting: quit a MoneyWiz it looks after, unless another
        server has used it within the idle time."""
        self._stop.set()
        try:
            pid = self.managed_pid()
            if pid is None:
                return
            with lease(self.path) as data:
                data.get("clients", {}).pop(self.me, None)
                if self._others_active(data):
                    return
            deadline = time.monotonic() + EXIT_WAIT
            while time.monotonic() < deadline:
                state = self.native.peek("appState", self.calls)
                if not state or state.get("in_use"):
                    return
                if not state.get("pending") and not (state.get("cloud") or {}).get("busy"):
                    self.quit(pid)
                    return
                time.sleep(1)
        except Exception:  # noqa: BLE001 - exiting anyway
            pass
