"""D03 categories, payees and tags."""
from __future__ import annotations

from .native import Native, MoneyWizError, s, ref, arr, i, cls, ni, NIL, USER
from .values import clean

EXPENSE, INCOME = 1, 2
CATEGORY_TYPES = {"expense": EXPENSE, "income": INCOME}
TYPE_NAMES = {v: k for k, v in CATEGORY_TYPES.items()}
DEFAULT_ICON = "mw2_category_icon_179"

CATEGORY_FIELDS = ["name", "type", "parentCategory.GID", "imageFileName", "imageColorNum", "displayOrder"]
USAGE_FIELDS = {
    "Category": ["categoryAssigments", "childCategories", "connectedPaymentPlansForInterestCategory",
                 "connectedPaymentPlansForPrincipalCategory"],
    "Payee": ["transactions", "scheduledTransactions", "connectedPaymentPlans"],
    "Tag": ["transactions", "scheduledTransactions"],
}


def _name(value: str, field: str = "name") -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 200:
        raise MoneyWizError(f"{field}: non-empty text without leading/trailing spaces required")
    return value


class Catalog:
    def __init__(self, native: Native):
        self.n = native

    # ------------------------------------------------------------ categories

    def categories(self, kind: str | None = None) -> list[dict]:
        where = [["type", "eq", ni(CATEGORY_TYPES[kind])]] if kind else []
        rows = self.n.query("Category", where=where, fields=CATEGORY_FIELDS)["items"]
        by_id = {r["gid"]: r for r in rows}

        def path(r, depth=0):
            parent = by_id.get(r.get("parentCategory.GID"))
            return r["name"] if parent is None or depth > 20 else path(parent, depth + 1) + "/" + r["name"]

        out = [clean({"id": r["gid"], "name": r["name"], "path": path(r), "type": TYPE_NAMES.get(r["type"], r["type"]),
                      "parent_id": r.get("parentCategory.GID"), "icon": r.get("imageFileName"),
                      "color": r.get("imageColorNum"), "display_order": r.get("displayOrder")}) for r in rows]
        return sorted(out, key=lambda c: (c["type"], c["path"]))

    def category(self, category_id: str) -> dict:
        found = [c for c in self.categories() if c["id"] == category_id]
        if not found:
            raise MoneyWizError(f"Category not found: {category_id}")
        return found[0]

    def resolve_category(self, value: str, kind: str | None = None) -> dict:
        """Accept a category id or a full path such as 'Food/Coffee'."""
        cats = self.categories(kind)
        for c in cats:
            if c["id"] == value:
                return c
        matches = [c for c in cats if c["path"] == value] or [c for c in cats if c["name"] == value]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise MoneyWizError(f"Category {value!r} is ambiguous; use its id or full path: "
                                + ", ".join(m["path"] for m in matches))
        raise MoneyWizError(f"Category not found: {value!r}")

    def _validate_category_name(self, name, parent_id, type_raw, case_sensitive=False):
        selector = ("validateCategoryNameCaseSensitive:parentCategory:categoryType:" if case_sensitive
                    else "validateCategoryName:parentCategory:categoryType:")
        parent = ref("Category", parent_id) if parent_id else NIL
        ok = self.n.invoke(cls("DataValidatorHelper"), selector, [s(name), parent, i(type_raw)], save=False)["value"]
        if not ok:
            raise MoneyWizError(f"MoneyWiz rejected category name {name!r} (duplicate under the same parent?)")

    def create_category(self, name: str, kind: str, parent_id: str | None = None,
                        icon: str | None = None, color: int | None = None) -> dict:
        _name(name)
        if "/" in name:
            raise MoneyWizError("Category names cannot contain '/'")
        type_raw = CATEGORY_TYPES.get(kind)
        if type_raw is None:
            raise MoneyWizError("kind must be 'expense' or 'income'")
        if parent_id:
            parent = self.category(parent_id)
            if parent["type"] != kind:
                raise MoneyWizError("Parent category has a different kind")
            icon = icon or parent.get("icon")
            color = parent.get("color") if color is None else color
        self._validate_category_name(name, parent_id, type_raw)
        result = self.n.invoke("manager", "createCategoryForUser:withName:image:imageColorNum:parentCategory:type:",
                               [USER, s(name), s(icon or DEFAULT_ICON), i(color or 0),
                                ref("Category", parent_id) if parent_id else NIL, i(type_raw)])
        if not result["value"]:
            raise MoneyWizError("MoneyWiz did not create the category")
        return self.category(result["value"]["gid"])

    def update_category(self, category_id: str, *, name: str | None = None, parent_id: str | None | type(...) = ...,
                        icon: str | None = None, color: int | None = None) -> dict:
        current = self.category(category_id)
        new_name = _name(name) if name is not None else current["name"]
        if "/" in new_name:
            raise MoneyWizError("Category names cannot contain '/'")
        new_parent = current.get("parent_id") if parent_id is ... else parent_id
        if new_parent:
            parent = self.category(new_parent)
            if parent["type"] != current["type"]:
                raise MoneyWizError("Parent category has a different kind")
            # refuse cycles
            cats = {c["id"]: c for c in self.categories()}
            cursor = new_parent
            while cursor:
                if cursor == category_id:
                    raise MoneyWizError("A category cannot be moved under itself or its descendants")
                cursor = cats[cursor].get("parent_id")
        if new_name != current["name"] or new_parent != current.get("parent_id"):
            same_name_case = new_name.lower() == current["name"].lower() and new_parent == current.get("parent_id")
            self._validate_category_name(new_name, new_parent, CATEGORY_TYPES[current["type"]], case_sensitive=same_name_case)
        self.n.invoke("manager", "updateDontChangeOrderCategory:withName:image:imageColorNum:parentCategory:",
                      [ref("Category", category_id), s(new_name), s(icon or current.get("icon") or DEFAULT_ICON),
                       i(current.get("color", 0) if color is None else color),
                       ref("Category", new_parent) if new_parent else NIL])
        return self.category(category_id)

    def reorder_categories(self, ordered_ids: list[str]) -> list[dict]:
        """Set display order of sibling categories (same parent) to the given order."""
        cats = {c["id"]: c for c in self.categories()}
        missing = [x for x in ordered_ids if x not in cats]
        if missing:
            raise MoneyWizError(f"Unknown categories: {missing}")
        parents = {cats[x].get("parent_id") for x in ordered_ids} | {cats[x]["type"] for x in ordered_ids}
        if len(parents) != 2:
            raise MoneyWizError("Categories to reorder must share the same parent and kind")
        for position, category_id in enumerate(ordered_ids):
            if cats[category_id].get("display_order") != position:
                self.n.invoke("manager", "updateCategory:withDisplayOrder:", [ref("Category", category_id), ni(position)])
        return [self.category(x) for x in ordered_ids]

    # ------------------------------------------------------------ payees & tags

    def payees(self, text: str | None = None) -> list[dict]:
        where = [["name", "contains", s(text)]] if text else []
        rows = self.n.query("Payee", where=where, fields=["name"], sort=[["name", True]])["items"]
        return [{"id": r["gid"], "name": r["name"]} for r in rows]

    def tags(self) -> list[dict]:
        rows = self.n.query("Tag", fields=["name"], sort=[["name", True]])["items"]
        return [{"id": r["gid"], "name": r["name"]} for r in rows]

    def _named(self, entity: str, object_id: str) -> dict:
        found = self.n.get(entity, object_id, ["name"])
        if not found:
            raise MoneyWizError(f"{entity} not found: {object_id}")
        return {"id": found["gid"], "name": found["name"]}

    def _same_name(self, entity: str, name: str, exclude: str | None = None) -> list[dict]:
        rows = self.n.query(entity, where=[["name", "eq", s(name)]], fields=["name"])["items"]
        lowered = [r for r in self.n.query(entity, where=[["name", "begins", s(name)]], fields=["name"])["items"]
                   if r["name"].lower() == name.lower()]
        seen = {r["gid"]: r for r in rows + lowered if r["gid"] != exclude}
        return list(seen.values())

    def create_named(self, entity: str, name: str) -> dict:
        _name(name)
        if self._same_name(entity, name):
            raise MoneyWizError(f"A {entity.lower()} named {name!r} already exists")
        selector = {"Payee": "createPayeeForUser:withName:", "Tag": "createTagForUser:withName:"}[entity]
        result = self.n.invoke("manager", selector, [USER, s(name)])["value"]
        if not result:
            raise MoneyWizError(f"MoneyWiz did not create the {entity.lower()}")
        return self._named(entity, result["gid"])

    def rename_named(self, entity: str, object_id: str, name: str) -> dict:
        _name(name)
        self._named(entity, object_id)
        if self._same_name(entity, name, exclude=object_id):
            raise MoneyWizError(f"A {entity.lower()} named {name!r} already exists; merge instead")
        selector = {"Payee": "updatePayee:name:", "Tag": "updateTag:name:"}[entity]
        self.n.invoke("manager", selector, [ref(entity, object_id), s(name)])
        return self._named(entity, object_id)

    # ------------------------------------------------------------ shared delete / merge

    def usage(self, entity: str, object_id: str) -> dict:
        fields = USAGE_FIELDS[entity]
        found = self.n.get(entity, object_id, [f + ".GID" for f in fields])
        if not found:
            raise MoneyWizError(f"{entity} not found: {object_id}")
        return {f: len(found.get(f + ".GID") or []) for f in fields}

    def delete(self, entity: str, object_id: str) -> dict:
        used = {k: v for k, v in self.usage(entity, object_id).items() if v}
        if used:
            raise MoneyWizError(f"{entity} {object_id} is still in use {used}; merge it into another {entity.lower()} instead")
        selector = {"Category": "deleteCategory:", "Payee": "deletePayee:", "Tag": "deleteTag:"}[entity]
        result = self.n.invoke("manager", selector, [ref(entity, object_id)])
        if entity == "Category" and result["value"] is False:
            raise MoneyWizError("MoneyWiz refused to delete the category")
        if self.n.get(entity, object_id):
            raise MoneyWizError(f"{entity} still exists after delete")
        return {"deleted": object_id}

    def merge(self, entity: str, source_ids: list[str], target_id: str) -> dict:
        if not source_ids or target_id in source_ids:
            raise MoneyWizError("Provide source ids that differ from the target id")
        if entity == "Category":
            target = self.category(target_id)
            for source_id in source_ids:
                if self.category(source_id)["type"] != target["type"]:
                    raise MoneyWizError("Cannot merge expense and income categories")
        else:
            for object_id in [*source_ids, target_id]:
                self._named(entity, object_id)
        selector = {"Category": "mergeCategories:toCategory:", "Payee": "mergePayees:toPayee:", "Tag": "mergeTags:toTag:"}[entity]
        self.n.invoke("manager", selector, [arr(ref(entity, x) for x in source_ids), ref(entity, target_id)])
        left = [x for x in source_ids if self.n.get(entity, x)]
        if left:
            raise MoneyWizError(f"Merge finished but sources still exist: {left}")
        return {"merged": source_ids, "into": target_id}
