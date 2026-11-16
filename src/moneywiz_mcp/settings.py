"""D04 currencies, exchange rates and ledger-wide settings."""
from __future__ import annotations

from .native import Native, MoneyWizError, s, n, nb, ref, arr, currency_code
from .values import amount, money, clean


class Settings:
    def __init__(self, native: Native):
        self.n = native

    def _app_settings(self) -> dict:
        rows = self.n.query("AppSettings", fields=["defaultCurrency", "currenciesToDisplay", "currenciesManualRates",
                                                   "showCurrencySymbol", "clearTransactionOnCreation",
                                                   "groupTransactionsByStatus", "showRunningBalance",
                                                   "currentSelectedLanguage"])["items"]
        if not rows:
            raise MoneyWizError("MoneyWiz has no settings object for this user")
        return rows[0]

    def currencies(self) -> dict:
        row = self._app_settings()
        known = self.n.ask("manager", "currenciesArray", []) or []
        display = self.n.ask("manager", "currenciesToDisplayArray", []) or []
        rates = row.get("currenciesManualRates")
        return clean({
            "default_currency": row.get("defaultCurrency"),
            "display_currencies": display,
            "manual_rates": rates if isinstance(rates, dict) else None,
            "available_count": len(known) if isinstance(known, list) else None,
        })

    def set_default_currency(self, currency: str) -> dict:
        currency = self._check(currency)
        if self.currencies().get("default_currency") == currency:
            return self.currencies()
        self.n.invoke("manager", "setLocalCurrency:", [s(currency)])
        result = self.currencies()
        if result.get("default_currency") != currency:
            raise MoneyWizError(f"MoneyWiz did not accept {currency} as the default currency")
        return result

    def _check(self, currency: str) -> str:
        return currency_code(self.n, currency)

    def manual_rate(self, from_currency: str, to_currency: str) -> dict:
        source, target = self._check(from_currency), self._check(to_currency)
        has = self.n.ask("manager", "hasManualRateFromCurrency:toCurrency:", [s(source), s(target)])
        rate = self.n.ask("manager", "manualRateFromCurrency:toCurrency:", [s(source), s(target)])
        return {"from": source, "to": target, "manual": bool(has), "rate": money(rate)}

    def set_manual_rate(self, from_currency: str, to_currency: str, rate) -> dict:
        source, target = self._check(from_currency), self._check(to_currency)
        value = amount(rate, field="rate")
        self.n.invoke("manager", "setManualRate:fromCurrency:toCurrency:",
                      [n(float(value)), s(source), s(target)])
        result = self.manual_rate(source, target)
        if not result["manual"]:
            raise MoneyWizError("MoneyWiz did not store the manual rate")
        return result

    def clear_manual_rate(self, from_currency: str, to_currency: str) -> dict:
        source, target = self._check(from_currency), self._check(to_currency)
        if not self.manual_rate(source, target)["manual"]:
            return {"from": source, "to": target, "manual": False}
        self.n.invoke("manager", "removeManualRateFromCurrency:toCurrency:", [s(source), s(target)])
        return self.manual_rate(source, target)

    def set_display_currencies(self, add: list[str] | None = None, remove: list[str] | None = None) -> dict:
        for currency in add or []:
            code = self._check(currency)
            if not self.n.ask("manager", "isCurrencyInDisplayList:", [s(code)]):
                self.n.invoke("manager", "addCurrencyToDisplayList:", [s(code)])
        for currency in remove or []:
            code = self._check(currency)
            if self.n.ask("manager", "isCurrencyInDisplayList:", [s(code)]):
                self.n.invoke("manager", "removeCurrencyFromDisplayList:", [s(code)])
        return self.currencies()

    def networth(self, currency: str | None = None, include_archived: bool = False) -> dict:
        target = self._check(currency) if currency else self.currencies()["default_currency"]
        accounts = self.n.query("Account", where=[] if include_archived else [["archived", "ne", nb(True)]],
                                fields=["name"])["items"]
        refs = arr(ref("Account", a["gid"]) for a in accounts)
        total = self.n.ask("manager", "calculateNetWorthWithAccounts:currency:", [refs, s(target)])
        liabilities = self.n.ask("manager", "calculateTotalLiabilitiesWithAccounts:currency:", [refs, s(target)])
        cash = self.n.ask("manager", "calculateTotalCashWithAccounts:currency:", [refs, s(target)])
        return {"currency": target, "accounts": len(accounts), "net_worth": money(total),
                "liabilities": money(liabilities), "cash": money(cash)}
