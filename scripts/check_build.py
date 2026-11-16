"""Compare the running MoneyWiz build with the build the server was recorded on.

    uv run scripts/check_build.py              report; exits 1 if anything the server uses changed
    uv run scripts/check_build.py --record     record this build (refused while a used call changed)
    uv run scripts/check_build.py --record --accept-changed
                                               record it anyway, after the code has been adapted

Checks, against the live app (read-only):
  * every MoneyWizManager method the server calls, and every other method and
    muted observer in signatures.json: present, same type encoding (offsets
    ignored, as the agent's own check does);
  * what the agent itself needs: +isInitialized, +sharedManager, the
    _cachedCurrentUser ivar;
  * every Core Data key path the server reads (calls.FIELD_LISTS).
Then run the live suite (MONEYWIZ_LIVE_TESTS=1) before using the server on the new build.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from moneywiz_mcp import calls, native
from moneywiz_mcp.native import Native

TABLE = Path(native.__file__).with_name("signatures.json")


def shape(encoding: str | None) -> str | None:
    return None if encoding is None else re.sub(r"[0-9]", "", encoding)


def resolve_path(model: dict, entity: str, path: str, responds) -> str | None:
    """None if MoneyWiz can read the key path from that entity, else why not."""
    current = entity
    for part in path.split("."):
        family, queue = [], [current]
        while queue:  # the entity and all of its sub-entities
            name = queue.pop()
            family.append(name)
            queue.extend(model[name]["children"])
        prop = next((model[e]["properties"][part] for e in family if part in model[e]["properties"]), None)
        if prop is None:
            if responds(family, part):
                return None  # a computed getter (balanceDictionary, installments, ...)
            return f"{current} has no property or getter {part!r}"
        if "to" not in prop:
            return None  # an attribute ends the path
        current = prop["to"]
    return None


def main(argv: list[str]) -> int:
    record, accept = "--record" in argv, "--accept-changed" in argv
    app = Native()
    status = app.status()
    model = app.model()
    print(f"MoneyWiz {status['version']} (build {status['build']}); recorded: build {native.RECORDED_BUILD}")

    problems, notes = [], []

    used = sorted(calls.manager_selectors())
    live = dict(zip(used, app.live_signatures([("MoneyWizManager", "- " + s) for s in used]), strict=True))
    for selector in used:
        recorded = native.MANAGER_SIGNATURES[selector]
        if live[selector] is None:
            problems.append(f"MoneyWizManager -{selector}: missing")
        elif shape(live[selector]) != shape(recorded):
            problems.append(f"MoneyWizManager -{selector}: {recorded} -> {live[selector]}")

    def class_of(name: str) -> str:
        return model[name]["cls"] if name in model else name

    entries = [(name, key) for name, table in native.CLASS_SIGNATURES.items() for key in table]
    found = app.live_signatures([(class_of(n), k) for n, k in entries])
    live_classes = {}
    for (name, key), encoding in zip(entries, found, strict=True):
        recorded = native.CLASS_SIGNATURES[name][key]
        live_classes.setdefault(name, {})[key] = encoding
        if encoding is None:
            problems.append(f"{class_of(name)} {key}: missing")
        elif shape(encoding) != shape(recorded):
            problems.append(f"{class_of(name)} {key}: {recorded} -> {encoding}")

    for cls, selector in (("MoneyWizManager", "+ isInitialized"), ("MoneyWizManager", "+ sharedManager")):
        if app.live_signatures([(cls, selector)])[0] is None:
            problems.append(f"agent needs {cls} {selector}: missing")
    if not app.has_ivar("MoneyWizManager", "_cachedCurrentUser"):
        problems.append("agent needs the MoneyWizManager ivar _cachedCurrentUser: missing")
    if not app.has_ivar("MoneyWizManager", "_isFullyInitialized"):
        notes.append("MoneyWizManager no longer has _isFullyInitialized (the agent then skips that check)")

    def responds(family: list[str], key: str) -> bool:
        return any(app.live_signatures([(model[e]["cls"], "- " + key)])[0] for e in family)

    for entity, lists in calls.FIELD_LISTS.items():
        if entity not in model:
            problems.append(f"entity {entity} is gone")
            continue
        for path in sorted({p for fields in lists for p in fields}):
            why = resolve_path(model, entity, path, responds)
            if why:
                problems.append(f"field {entity}.{path}: {why}")

    unused_changed = 0
    others = sorted(set(native.MANAGER_SIGNATURES) - set(used))
    for selector, encoding in zip(others, app.live_signatures([("MoneyWizManager", "- " + s) for s in others]),
                                  strict=True):
        if shape(encoding) != shape(native.MANAGER_SIGNATURES[selector]):
            unused_changed += 1
    notes.append(f"{len(used)} manager methods in use; {unused_changed} of the {len(others)} unused recorded ones "
                 "changed or went away (no effect on the server)")

    for line in notes:
        print("note:", line)
    if problems:
        print(f"\n{len(problems)} change(s) affect the server:")
        for line in problems:
            print("  -", line)
    else:
        print("\nNothing the server uses has changed.")

    if record:
        if problems and not accept:
            print("\nNot recorded: adapt the code to the changes above first, then pass --accept-changed.")
            return 1
        manager = {name[2:]: types for name, types in app.methods("MoneyWizManager", "") if name.startswith("- ")}
        data = json.loads(TABLE.read_text())
        data.update({"app_version": status["version"], "build": status["build"], "MoneyWizManager": manager,
                     "classes": {n: {k: (live_classes[n][k] or v) for k, v in t.items()}
                                 for n, t in native.CLASS_SIGNATURES.items()}})
        data["note"] = re.sub(r"build \d+", f"build {status['build']}", data.get("note", ""))
        TABLE.write_text(json.dumps(data, indent=1) + "\n")
        print(f"\nRecorded build {status['build']} in {TABLE}. Run the live suite before relying on it.")
    app.close()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
