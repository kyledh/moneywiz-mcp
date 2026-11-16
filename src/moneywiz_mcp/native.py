"""Persistent in-process connection to the running MoneyWiz app.

One Frida session per MCP process, attached lazily on first use and
re-attached automatically if MoneyWiz restarts. All app work runs on
MoneyWiz's main queue inside the agent; this module only transports
JSON and converts the agent's tagged values.
"""
from __future__ import annotations

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
# MoneyWizManager instance-method type encodings recorded from build 466.
# Calls on the manager are checked against these, so an app update that
# changes a signature fails loudly instead of passing misaligned arguments.
SIGNATURES = json.loads(Path(__file__).with_name("signatures.json").read_text())
# How long a write waits for MoneyWiz's own unsaved objects to clear.
UNSAVED_WAIT = 5.0


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

    # ---------------------------------------------------------------- connection

    def _find_pid(self) -> int | None:
        device = frida.get_local_device()
        for process in device.enumerate_processes():
            if process.name == PROCESS_NAME:
                return process.pid
        return None

    def _connect(self):
        pid = self._find_pid()
        if pid is None and self._launch:
            # -g: do not bring it to the front; -j: launch it hidden, so an MCP
            # call does not put MoneyWiz's window on screen.
            subprocess.run(["open", "-g", "-j", "-a", APP_PATH], check=False)
            for _ in range(60):
                time.sleep(1)
                if (pid := self._find_pid()) is not None:
                    break
        if pid is None:
            raise MoneyWizError("MoneyWiz is not running; open it (with its database unlocked) and retry")
        try:
            session = frida.attach(pid)
        except frida.PermissionDeniedError as exc:
            raise MoneyWizError(
                "Cannot attach to MoneyWiz: macOS denied task_for_pid. Run "
                "`sudo security authorizationdb write system.privilege.taskport is-developer` once "
                "(see README, 'Attach permission')") from exc
        script = session.create_script(AGENT.read_text(), name="moneywiz-mcp")
        script.load()
        # Runs on Frida's own thread, so it must not take our lock: a detach
        # started from here would then deadlock against the caller holding it.
        session.on("detached", self._mark_stale)
        self._stale = False
        self._session, self._script, self._pid = session, script, pid

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

    def _call(self, name: str, *args):
        with self._lock:
            for attempt in (1, 2):
                if self._stale:
                    self._drop()
                if self._script is None:
                    self._connect()
                try:
                    return getattr(self._script.exports_sync, name)(*args)
                except frida.InvalidOperationError:
                    # Script/session destroyed (app quit or restarted): reconnect once.
                    self._drop()
                    if attempt == 2:
                        raise MoneyWizError("Lost connection to MoneyWiz") from None
                except frida.core.RPCException as exc:
                    error = MoneyWizError(exc.args[0] if exc.args else str(exc))
                    error.kind = exc.args[1] if len(exc.args) > 1 else "Error"
                    error.detail = str(exc)
                    raise error from None

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

    def get(self, entity: str, gid: str, fields=None) -> dict | None:
        items = self.query(entity, where=[["GID", "eq", s(gid)]], fields=fields, limit=2)["items"]
        if len(items) > 1:
            raise MoneyWizError(f"Duplicate GID {gid}")
        return items[0] if items else None

    def invoke(self, target, selector: str, args: list, *, encoding: str | None = None,
               fields=None, save: bool = True, quiet=()) -> dict:
        """Call a business method. quiet names [class, selector] handlers that
        only present UI, to be skipped while this call runs (see agent.js)."""
        spec = {"target": target, "selector": selector, "args": args, "fields": fields or [], "save": save}
        if quiet:
            spec["quiet"] = [list(handler) for handler in quiet]
        if encoding is None and target == "manager":
            encoding = SIGNATURES.get(selector)
            if encoding is None:
                raise MoneyWizError(f"Unknown MoneyWizManager selector {selector}")
        if encoding:
            spec["encoding"] = encoding
        return decode(self._write("invoke", spec))

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

    def methods(self, class_name: str, needle: str = "") -> list:
        return self._call("methods", class_name, needle)


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
    known = native.invoke("manager", "currenciesArray", [], save=False)["value"] or []
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
