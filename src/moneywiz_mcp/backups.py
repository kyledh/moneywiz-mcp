"""A backup of the ledger, made as MoneyWiz's automatic backups make one.

MoneyWiz zips its database (for an iCloud ledger, the iCloud store's SQLite
files) on a background queue and the agent moves the zip into MoneyWiz's own
backups folder, where its Settings > Backup can restore it. Nothing is pruned,
unlike MoneyWiz's automatic run, and nothing in the ledger changes.
"""
from __future__ import annotations

from .native import Native, MoneyWizError

# Methods the agent calls itself (checked against the recorded signatures first).
AGENT_CALLS = {
    "MWBackupsManager": ["+ defaultManager", "- backupCreationInProgress", "- shouldUseICloudBackup",
                         "+ backupsFolderURL"],
    "MoneyWizManager": ["- createMoneyWizBackupFile:", "- createMoneyWizICloudBackupFile:"],
}
TIMEOUT = 300


class Backups:
    def __init__(self, native: Native):
        self.n = native

    def create(self) -> dict:
        result = self.n.backup(AGENT_CALLS, timeout=TIMEOUT)
        if not result.get("path"):
            raise MoneyWizError("MoneyWiz did not report where the backup went")
        return result | {"restore": "MoneyWiz > Settings > Backup lists this folder's backups to restore from"}
