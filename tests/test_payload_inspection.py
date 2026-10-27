"""Diagnostic text searches cannot persist surrounding payloads or auth data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import payload_inspection


class PayloadSearchChecks(unittest.TestCase):
    def test_nested_json_search_and_credential_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, report = root / "terms.json", root / "results.json"
            term = "\u5ba2\u5385\u89c2\u5f71"
            config.write_text(json.dumps([term]), encoding="utf-8")
            with patch.multiple(payload_inspection, CONFIG=config, REPORT=report, RESULTS={}):
                payload_inspection.inspect("tuya.m.user.mobile.passwd.login", {"name":term})
                self.assertFalse(report.exists())
                payload_inspection.inspect("tuya.m.device.get", {"config":json.dumps({"scene":{"name":term}}),
                    "localKey":term, "token":term, "secret":"never-serialize", "unrelated":"private-name"})
            data = report.read_text(encoding="utf-8")
            result = json.loads(data)
            paths = [match["field"] for match in result["tuya.m.device.get"]["matches"]]
            self.assertIn("$result.config.json.scene.name", paths)
            self.assertNotIn("localKey", data)
            self.assertNotIn("never-serialize", data)
            self.assertNotIn("private-name", data)


if __name__ == "__main__":
    unittest.main()
