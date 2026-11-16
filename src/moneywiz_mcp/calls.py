"""What the server relies on in MoneyWiz, read from its own source.

Used by scripts/check_build.py to compare a MoneyWiz build with the recorded
one, and by the offline tests to make sure every call has a recorded
signature. Calls are found by reading every `.invoke(target, selector, ...)`
in the domain modules; selectors held in module constants count as well.
"""
from __future__ import annotations

import ast
import inspect

from . import (accounts, backups, budgets, catalog, investments, lifecycle, native, portfolio, reports, scheduled,
               settings, transactions)

MODULES = (accounts, backups, budgets, catalog, investments, lifecycle, portfolio, reports, scheduled, settings,
           transactions)

# Field lists read with native.query(), by the entity they are read from.
FIELD_LISTS = {
    "Account": [accounts.FIELDS], "Group": [accounts.GROUP_FIELDS, budgets.GROUP_FIELDS, reports.GROUP_FIELDS],
    "Transaction": [transactions.FIELDS], "InvestmentExchangeTransaction": [transactions.EXCHANGE_FIELDS],
    "Category": [catalog.CATEGORY_FIELDS],
    "ScheduledTransactionHandler": [scheduled.FIELDS], "Budget": [budgets.FIELDS],
    "InvestmentHolding": [portfolio.HOLDING_FIELDS], "PaymentPlan": [portfolio.PLAN_FIELDS],
    "PaymentPlanItem": [portfolio.PLAN_ITEM_FIELDS], "CustomReport": [reports.FIELDS],
}


def _strings(module) -> dict[str, list[str]]:
    """Module-level string constants, and dicts of them, by name."""
    out = {}
    for name, value in vars(module).items():
        if isinstance(value, str):
            out[name] = [value]
        elif isinstance(value, dict) and value and all(isinstance(v, str) for v in value.values()):
            out[name] = list(value.values())
    return out


def _target(node) -> tuple[str, str] | None:
    """(kind, name) for a call target written out in the source."""
    if isinstance(node, ast.Constant) and node.value in ("manager", "user"):
        return (node.value, "MoneyWizManager" if node.value == "manager" else "User")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args \
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        if node.func.id == "ref":
            return ("ref", node.args[0].value)
        if node.func.id == "cls":
            return ("cls", node.args[0].value)
    if isinstance(node, ast.Dict) and len(node.keys) == 1 and isinstance(node.keys[0], ast.Constant) \
            and node.keys[0].value == "cls" and isinstance(node.values[0], ast.Constant):
        return ("cls", node.values[0].value)
    return None


def _literals(node) -> list[str] | None:
    """The strings an expression can be: a literal, or a conditional between literals."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        body, orelse = _literals(node.body), _literals(node.orelse)
        return body + orelse if body is not None and orelse is not None else None
    return None


def _local_strings(function) -> dict[str, list[str]]:
    """Names a function assigns from literals (selector = "a:" if x else "b:")."""
    out = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values = _literals(node.value)
            if values:
                out.setdefault(node.targets[0].id, []).extend(values)
    return out


def _local_targets(tree) -> dict[str, tuple[str, str]]:
    """Names assigned a receiver written out in the source (validator = cls("X"))."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = _target(node.value)
            if target:
                out[node.targets[0].id] = target
    return out


def invocations() -> list[dict]:
    """Every `.invoke(...)` in the domain modules, with what could be resolved."""
    found = []
    for module in MODULES:
        constants, tree = _strings(module), ast.parse(inspect.getsource(module))
        local = {}
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = _local_strings(function)
                for inner in ast.walk(function):
                    local[id(inner)] = scope
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):  # a batch spec: {"target": ..., "selector": ..., ...}
                keys = {k.value: v for k, v in zip(node.keys, node.values, strict=True) if isinstance(k, ast.Constant)}
                if "target" not in keys or "selector" not in keys:
                    continue
                target_node, selector = keys["target"], keys["selector"]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "invoke" \
                    and len(node.args) >= 2:
                target_node, selector = node.args[0], node.args[1]
            else:
                continue
            scope = local.get(id(node), {})
            if isinstance(selector, ast.Constant) and isinstance(selector.value, str):
                selectors = [selector.value]
            elif isinstance(selector, ast.Name) and selector.id in scope:
                selectors = scope[selector.id]
            elif isinstance(selector, ast.Name) and selector.id in constants:
                selectors = constants[selector.id]
            elif isinstance(selector, ast.Subscript) and isinstance(selector.value, ast.Name) \
                    and selector.value.id in constants:
                selectors = constants[selector.value.id]
            else:
                selectors = None  # chosen at run time; the recorded table must list it
            target = _target(target_node)
            if target is None and isinstance(target_node, ast.Name):
                target = _local_targets(tree).get(target_node.id)
            found.append({"module": module.__name__, "line": node.lineno, "target": target, "selectors": selectors})
    return found


def agent_calls() -> set[tuple[str, str]]:
    """(class, "- selector") for methods the agent calls itself on a module's
    behalf (a module's AGENT_CALLS, by class), such as the report calculation."""
    found = set()
    for module in MODULES:
        for name, keys in getattr(module, "AGENT_CALLS", {}).items():
            found.update((name, key) for key in keys)
    return found


def manager_selectors() -> set[str]:
    """MoneyWizManager selectors the server calls."""
    used = {key[2:] for name, key in agent_calls() if name == "MoneyWizManager"}
    for call in invocations():
        if call["target"] and call["target"][0] == "manager" and call["selectors"]:
            used.update(call["selectors"])
    # Selectors held in constants that are passed on by helpers (Native.invoke on behalf of a module).
    for module in MODULES:
        for values in _strings(module).values():
            used.update(v for v in values if v in native.MANAGER_SIGNATURES and not v.startswith("_"))
    return used


def object_calls() -> set[tuple[str, str]]:
    """(name, "+ selector" / "- selector") for calls on anything but the manager,
    keyed as the code names the receiver (an entity for ref(), a class for cls())."""
    used = set()
    for call in invocations():
        target, selectors = call["target"], call["selectors"]
        if not target or target[0] == "manager" or not selectors:
            continue
        prefix = "+ " if target[0] == "cls" else "- "
        used.update((target[1], prefix + s) for s in selectors)
    return used | {(name, key) for name, key in agent_calls() if name != "MoneyWizManager"}


def quiet_handlers() -> set[tuple[str, str]]:
    """Observers the server mutes."""
    found = set(native.ALWAYS_QUIET)
    for module in MODULES:
        for value in vars(module).values():
            if isinstance(value, tuple) and value and all(isinstance(h, tuple) and len(h) == 2 for h in value):
                found.update(value)
    return found
