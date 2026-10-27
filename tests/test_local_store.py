"""Storage isolation and credential allowlist, using temporary local files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_store import AccountStore


class LocalStoreChecks(unittest.TestCase):
    def test_account_isolation_and_credential_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            first = AccountStore(Path(folder), "first@example.test", "86")
            second = AccountStore(Path(folder), "second@example.test", "86")
            first.save({"dev": {"devId": "dev", "name": "\u5ba2\u5385", "dps": {"1": True},
                                "_room": "\u4e3b\u5367", "_schema": {"switch": "1"}, "_channel_names":{"1":"\u706f\u5e26"},
                                "localKey": "must-not-persist", "password": "must-not-persist",
                                "sid": "must-not-persist", "token": "must-not-persist",
                                "moduleMap": {"wifi": {"isOnline": True, "secret": "must-not-persist"}}}}, 1234)
            loaded = AccountStore(Path(folder), "first@example.test", "86").load()
            self.assertEqual(loaded[0]["dev"]["name"], "\u5ba2\u5385")
            self.assertTrue(loaded[0]["dev"]["isOnline"])
            self.assertEqual(loaded[1], 1234)
            self.assertEqual(loaded[0]["dev"]["_channel_names"], {"1":"\u706f\u5e26"})
            self.assertEqual(second.load(), ({}, 0))
            self.assertNotIn("must-not-persist", first.path.read_text(encoding="utf-8"))
            self.assertNotIn("first@", first.path.name)

    def test_country_is_part_of_identity_and_corruption_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            first = AccountStore(Path(folder), "12345678", "86")
            second = AccountStore(Path(folder), "12345678", "852")
            self.assertNotEqual(first.path, second.path)
            first.path.write_text("{bad", encoding="utf-8")
            self.assertEqual(first.load(), ({}, 0))
            first.path.write_text(json.dumps({"format": 2, "devices": []}), encoding="utf-8")
            self.assertEqual(first.load(), ({}, 0))


if __name__ == "__main__":
    unittest.main()
