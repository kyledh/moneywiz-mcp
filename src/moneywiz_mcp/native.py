"""Persistent in-process connection to the running MoneyWiz app.

One Frida session per MCP process, attached lazily on first use and
re-attached on the next call if MoneyWiz restarted in between. A call is sent
once: if the connection is lost while MoneyWiz handles it, the call may or may
not have been carried out, so it is reported, never sent again. All app work
runs on MoneyWiz's main queue inside the agent; this module only transports
JSON and converts the agent's tagged values.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frida

PROCESS_NAME = "MoneyWiz"
APP_PATH = "/Applications/MoneyWiz.app"
AGENT = Path(__file__).with_name("agent.js")
# Type encodings recorded from one MoneyWiz build: every MoneyWizManager
# instance method, plus the other methods and observers the server calls.
# Every call is checked against these, so an app update that changes a
# signature fails loudly instead of passing misaligned arguments.
# scripts/check_build.py compares a new build with this table.
_RECORDED = json.loads(Path(__file__).with_name("signatures.json").read_text())
RECORDED_BUILD = _RECORDED["build"]
MANAGER_SIGNATURES: dict[str, str] = _RECORDED["MoneyWizManager"]
CLASS_SIGNATURES: dict[str, dict[str, str]] = _RECORDED["classes"]
# How long a write waits for MoneyWiz's own unsaved objects to clear.
UNSAVED_WAIT = 5.0
# How long the first call waits for a MoneyWiz it launched to open its ledger.
STARTUP_WAIT = 60.0
# Main-window observers that only put up an alert, skipped for every saving
# call: any transaction change can push a budget over, and MoneyWiz answers
# with a "the following budgets are low / exceeded" alert.
ALWAYS_QUIET = (("iphoneMoneyWizViewController", "onBudgetsWarning:"),)


class MoneyWizError(RuntimeError):
    """A request MoneyWiz (or our validation) refused. Safe to show to the user."""


class Native:
    def __init__(self, launch: bool = False):
        self._lock = threading.RLock()
        self._session = None
        self._script = None
        self._pid = None
        self._stale = False
        self._launch = launch
        # A Lifecycle (lifecycle.py) when the server looks after MoneyWiz itself.
        self.lifecycle = None
        # What muted observers were told during calls (see agent.js), so a tool
        # can report it; read with take_notices().
        self.notices: list[dict] = []

    # ---------------------------------------------------------------- connection

    def _find_pid(self) -> int | None:
        device = frida.get_local_device()
        for process in device.enumerate_processes():
            if process.name == PROCESS_NAME:
                return process.pid
        return None

    def running_pid(self) -> int | None:
        return self._find_pid()

    def _connect(self, launch: bool = True):
        pid = self._find_pid()
        launched = False
        if pid is None and self._launch and launch:
            # -g: do not bring it to the front; -j: launch it hidden, so an MCP
            # call does not put MoneyWiz's window on screen.
            subprocess.run(["open", "-g", "-j", "-a", APP_PATH], check=False)
            for _ in range(60):
                time.sleep(1)
                if (pid := self._find_pid()) is not None:
                    launched = True
                    break
            if launched and self.lifecycle is not None:
                self.lifecycle.launched(pid)
        if pid is None:
            raise MoneyWizError("MoneyWiz is not running; open it (with its database unlocked) and retry")
        try:
            session = frida.attach(pid)
        except frida.PermissionDeniedError as exc:
            raise MoneyWizError(
                "Cannot attach to MoneyWiz: macOS denied task_for_pid. Run "
                "`sudo security authorizationdb write system.privilege.taskport is-developer` once "
                "(see README, 'Attach permission')") from exc
        except (frida.ProcessNotFoundError, frida.TransportError) as exc:
            raise MoneyWizError(f"Cannot attach to MoneyWiz ({exc}); retry once it has started") from None
        try:
            script = session.create_script(AGENT.read_text(), name="moneywiz-mcp")
            script.load()
        except (frida.InvalidOperationError, frida.TransportError) as exc:
            # A session without its agent would only hold MoneyWiz up when it quits.
            with contextlib.suppress(frida.InvalidOperationError, frida.TransportError):
                session.detach()
            raise MoneyWizError(f"Could not load the agent into MoneyWiz ({exc}); retry in a moment") from None
        # Runs on Frida's own thread, so it must not take our lock: a detach
        # started from here would then deadlock against the caller holding it.
        session.on("detached", self._mark_stale)
        self._stale = False
        self._session, self._script, self._pid = session, script, pid
        if launched:
            self._wait_until_ready()

    def _wait_until_ready(self):
        """A MoneyWiz just launched takes a few seconds to open its ledger."""
        deadline = time.monotonic() + STARTUP_WAIT
        while True:
            try:
                self._script.exports_sync.status()
                return
            except frida.core.RPCException as exc:
                if time.monotonic() >= deadline:
                    raise MoneyWizError(
                        f"MoneyWiz was started but its ledger is not open after {STARTUP_WAIT:g}s "
                        f"({exc.args[0] if exc.args else exc}); if it asks for a passcode, unlock it and retry"
                    ) from None
            except (frida.InvalidOperationError, frida.TransportError):
                self._drop()
                raise MoneyWizError("MoneyWiz quit while it was starting; retry") from None
            time.sleep(1)

    def _mark_stale(self, *_):
        self._stale = True

    def _drop(self):
        self._session = self._script = self._pid = None
        self._stale = False

    def close(self):
        with self._lock:
            script, session = self._script, self._session
            self._drop()
        if script is not None:
            try:
                script.unload()
                session.detach()
            except (frida.InvalidOperationError, frida.TransportError):
                pass

    def _call(self, name: str, *args, launch: bool = True):
        with self._lock:
            if launch and self.lifecycle is not None:
                self.lifecycle.used()
            # A connection that ended between calls (MoneyWiz quit or restarted) is
            # replaced before anything is sent.
            if self._script is not None and (self._stale or self._script.is_destroyed):
                self._drop()
            if self._script is None:
                self._connect(launch)
            try:
                return getattr(self._script.exports_sync, name)(*args)
            except (frida.InvalidOperationError, frida.TransportError):
                # Lost while MoneyWiz had the call: it may have run (and saved) it, so
                # sending it again could record it twice, or crash MoneyWiz twice.
                self._drop()
                raise MoneyWizError(
                    "The connection to MoneyWiz was lost during the call (MoneyWiz quit or crashed). "
                    "It may or may not have been carried out: check before repeating it") from None
            except frida.core.RPCException as exc:
                error = MoneyWizError(exc.args[0] if exc.args else str(exc))
                error.kind = exc.args[1] if len(exc.args) > 1 else "Error"
                error.detail = str(exc)
                raise error from None

    def peek(self, name: str, *args):
        """A call for the lifecycle check: never launches MoneyWiz and does not
        count as use. None when MoneyWiz is not running or refuses."""
        if self._find_pid() is None:
            return None
        try:
            return self._call(name, *args, launch=False)
        except (MoneyWizError, frida.InvalidOperationError, frida.TransportError):
            return None

    def _write(self, name: str, *args):
        # The agent refuses before calling anything while MoneyWiz holds unsaved
        # objects. Its own background work clears within about a second (the
        # portfolio overview's temporary report), so wait for that; an editor
        # left open is still dirty after the wait and is reported.
        deadline = time.monotonic() + UNSAVED_WAIT
        while True:
            try:
                return self._call(name, *args)
            except MoneyWizError as exc:
                if getattr(exc, "kind", None) != "UnsavedChanges":
                    raise
                if time.monotonic() >= deadline:
                    raise MoneyWizError(f"{exc}, still pending after {UNSAVED_WAIT:g}s; "
                                        "finish or cancel it in MoneyWiz, then retry") from None
            time.sleep(0.2)

    # ---------------------------------------------------------------- api

    def status(self) -> dict:
        return decode(self._call("status"))

    def query(self, entity: str, *, where=None, sort=None, limit=None, offset=None,
              fields=None, count=False, raw=False) -> dict:
        """raw keeps the agent's tagged values ({"$date": seconds}), so a stored
        value can be handed back to MoneyWiz bit for bit."""
        spec = {"entity": entity, "where": where or [], "sort": sort or [], "fields": fields or [], "count": count}
        if limit:
            spec["limit"] = limit
        if offset:
            spec["offset"] = offset
        result = self._call("query", spec)
        return result if raw else decode(result)

    def count(self, entity: str, where=None) -> int:
        return self._call("count", {"entity": entity, "where": where or []})

    def counts(self, specs: list[tuple[str, list]]) -> list[int]:
        """Several counts ([(entity, where), ...]) in one call."""
        return self._call("counts", [{"entity": entity, "where": where or []} for entity, where in specs]) if specs else []

    def get(self, entity: str, gid: str, fields=None) -> dict | None:
        items = self.query(entity, where=[["GID", "eq", s(gid)]], fields=fields, limit=2)["items"]
        if len(items) > 1:
            raise MoneyWizError(f"Duplicate GID {gid}")
        return items[0] if items else None

    def invoke(self, target, selector: str, args: list, *, encoding: str | None = None,
               fields=None, quiet=()) -> dict:
        """Call a business method that changes the ledger, and save what it left
        unsaved. Refused (after a short wait) while MoneyWiz holds unsaved changes
        of its own, which a save would commit too. quiet names [class, selector]
        handlers that only present UI, to be skipped while this call runs (see
        agent.js); the budget warning alert is skipped for every write."""
        spec = {"target": target, "selector": selector, "args": args, "fields": fields or [], "save": True,
                "quiet": [list(handler) for handler in (*quiet, *ALWAYS_QUIET)],
                "encoding": encoding or recorded_encoding(target, selector)}
        result = decode(self._write("invoke", spec))
        self.notices.extend(result.pop("notices", None) or [])
        return result

    def ask(self, target, selector: str, args: list) -> Any:
        """Call a method that only answers a question (a validator, a computed value,
        a list MoneyWiz keeps) and return its value. Works while MoneyWiz holds
        unsaved changes, since nothing is saved; the agent refuses the answer, and
        undoes what it can, if the method left anything unsaved (see agent.js)."""
        spec = {"target": target, "selector": selector, "args": args, "fields": [], "save": False,
                "read_only": True, "encoding": recorded_encoding(target, selector)}
        return decode(self._call("invoke", spec))["value"]

    def batch(self, specs: list[dict]) -> dict:
        """Several calls in one main-queue turn, for business objects that take
        two calls (see agent.js batch). Each spec is {"target", "selector",
        "args", "save"?, "quiet"?}; a later spec can pass prev(i), the object
        the i-th earlier call returned. If one fails, what the batch inserted is
        deleted again."""
        prepared = []
        for spec in specs:
            spec = dict(spec)
            spec.setdefault("args", [])
            spec.setdefault("fields", [])
            if spec.get("encoding") is None:
                spec["encoding"] = recorded_encoding(spec["target"], spec["selector"])
            quiet = tuple(spec.pop("quiet", ())) + (ALWAYS_QUIET if spec.get("save") else ())
            if quiet:
                spec["quiet"] = [list(handler) for handler in quiet]
            prepared.append(spec)
        result = decode(self._write("batch", prepared))
        self.notices.extend(result.pop("notices", None) or [])
        return result

    def take_notices(self) -> list[dict]:
        notices, self.notices = self.notices, []
        return notices

    def save(self) -> dict:
        return self._call("save")

    def settle(self, allow: list[tuple[str, str]], wait: float = 1.0) -> dict:
        """Save MoneyWiz's own unsaved follow-up of our last call, but only if
        every pending object is one of `allow` ([entity, gid]). Waits up to
        `wait` seconds for it to appear; anything else pending is left alone."""
        deadline = time.monotonic() + wait
        while True:
            result = self._call("settle", [list(a) for a in allow])
            if result.get("saved") or result.get("others") or time.monotonic() >= deadline:
                return result
            time.sleep(0.1)

    # Attachments: MoneyWiz is sandboxed, so files travel as bytes (see agent.js).
    def stage_file(self, name: str, base64_data: str) -> str:
        return self._call("stageFile", name, base64_data)

    def unstage_file(self, path: str) -> bool:
        return self._call("unstageFile", path)

    def attachment(self, transaction_id: str, index: int) -> dict:
        return self._call("attachment", transaction_id, index)

    def report_data(self, spec: dict, calls: dict[str, list[str]]) -> dict:
        """Compute a report in a forked child context (see the agent's reportData): a saved
        one (spec gid), or one MoneyWiz builds unsaved for it (spec create), which, like a
        write, waits for MoneyWiz's own unsaved objects to clear first.
        Returns the agent's tagged values ($date, $dec, $ref) undecoded."""
        spec = {**spec, "calls": recorded_calls(calls)}
        return self._write("reportData", spec) if spec.get("create") else self._call("reportData", spec)

    def backup(self, calls: dict[str, list[str]], *, timeout: float) -> dict:
        """Have MoneyWiz make a backup into its backups folder (see the agent's backup)."""
        return self._call("backup", {"calls": recorded_calls(calls), "timeout": timeout})

    # Introspection for scripts/check_build.py (read-only).
    def live_signatures(self, pairs: list[tuple[str, str]]) -> list[str | None]:
        return self._call("signatures", [list(p) for p in pairs])

    def has_ivar(self, class_name: str, name: str) -> bool:
        return self._call("hasIvar", class_name, name)

    def model(self) -> dict:
        return self._call("model")

    def methods(self, class_name: str, needle: str = "") -> list:
        return self._call("methods", class_name, needle)


def recorded_encoding(target, selector: str) -> str:
    """The recorded type encoding for a call, keyed as the code names the
    receiver: the manager, the user, an entity for ref(), a class for cls()."""
    if target == "manager":
        name, key, table = "MoneyWizManager", selector, MANAGER_SIGNATURES
    else:
        if target == "user":
            name, key = "User", "- " + selector
        elif isinstance(target, dict) and "cls" in target:
            name, key = target["cls"], "+ " + selector
        elif isinstance(target, dict) and "ref" in target:
            name, key = target["ref"][0], "- " + selector
        else:
            raise MoneyWizError(f"Unknown invocation target {target!r}")
        table = CLASS_SIGNATURES.get(name, {})
    found = table.get(key)
    if found is None:
        raise MoneyWizError(f"No build-{RECORDED_BUILD} signature recorded for {name} {key}")
    return found


def recorded_calls(calls: dict[str, list[str]]) -> list[list[str]]:
    """[class, "- selector", encoding] for methods the agent calls itself,
    listed by class as a module's AGENT_CALLS."""
    out = []
    for name, keys in calls.items():
        for key in keys:
            table = MANAGER_SIGNATURES if name == "MoneyWizManager" else CLASS_SIGNATURES.get(name, {})
            found = table.get(key[2:] if name == "MoneyWizManager" else key)
            if found is None:
                raise MoneyWizError(f"No build-{RECORDED_BUILD} signature recorded for {name} {key}")
            out.append([name, key, found])
    return out


# -------------------------------------------------------------------- typed args

def s(value: str) -> dict: return {"s": value}
def n(value: float) -> dict: return {"n": float(value)}
def ni(value: int) -> dict:
    """NSNumber from an integer; 64-bit ids travel as text to stay exact in JS."""
    value = int(value)
    return {"ni": value} if abs(value) <= 2**53 - 1 else {"nbig": str(value)}
def nb(value: bool) -> dict: return {"nb": bool(value)}
def dec(value) -> dict: return {"dec": str(value)}
def ref(entity: str, gid: str) -> dict: return {"ref": [entity, gid]}
def arr(items) -> dict: return {"arr": list(items)}
def b(value: bool) -> dict: return {"b": bool(value)}
def i(value: int) -> dict: return {"i": int(value)}
def d(value: float) -> dict: return {"d": float(value)}
def cls(name: str) -> dict: return {"cls": name}

def prev(index: int) -> dict:
    """In a batch: the object returned by an earlier call (see Native.batch)."""
    return {"prev": int(index)}


NIL = {"nil": True}
USER = {"user": True}


def date(value: datetime) -> dict:
    if value.tzinfo is None:
        raise MoneyWizError("datetime must be timezone-aware")
    return {"date": value.timestamp()}


def currency_code(native: "Native", code) -> str:
    """Upper-case a currency code and check it against MoneyWiz's own list."""
    if not isinstance(code, str):
        raise MoneyWizError("currency: a code such as USD is required")
    code = code.strip().upper()
    known = native.ask("manager", "currenciesArray", []) or []
    if isinstance(known, list) and known and code not in known:
        raise MoneyWizError(f"MoneyWiz does not know the currency {code}")
    return code


def opt_s(value: str | None) -> dict:
    return NIL if value is None else s(value)


# -------------------------------------------------------------------- decoding

def decode(value: Any) -> Any:
    """Convert the agent's tagged JSON ($date/$dec/$ref) into plain JSON."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if "$date" in value and len(value) == 1:
            return datetime.fromtimestamp(value["$date"], timezone.utc).isoformat()
        if "$dec" in value and len(value) == 1:
            return value["$dec"]
        if "$int" in value and len(value) == 1:
            return int(value["$int"])
        return {k: decode(v) for k, v in value.items()}
    return value
