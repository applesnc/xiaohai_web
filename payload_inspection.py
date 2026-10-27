"""Opt-in text search on read responses. Stores matching terms/paths only."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent / ".runtime"
CONFIG = ROOT / "payload-search-terms.json"
REPORT = ROOT / "payload-search-results.json"
RESULTS = {}
SENSITIVE = {"sid", "token", "passwd", "password", "localkey", "seckey", "devkey", "secret"}


def inspect(action, payload):
    if not CONFIG.exists() or any(word in action for word in ("login", "token", "passwd", "password", "publish")):
        return
    try:
        terms = json.loads(CONFIG.read_text(encoding="utf-8"))
        terms = [term for term in terms if isinstance(term, str) and 0 < len(term) <= 64]
        entry = RESULTS.setdefault(action, {"responses":0, "matches":[]})
        entry["responses"] += 1

        def visit(node, path, depth=0):
            if depth > 30:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    if str(key).lower() in SENSITIVE:
                        continue
                    safe = str(key) if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,40}|\d{1,4}", str(key)) else "<key>"
                    visit(value, path + "." + safe, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    visit(item, path + "[]", depth + 1)
            elif isinstance(node, str):
                for term in terms:
                    match = {"term":term, "field":path}
                    if term in node and match not in entry["matches"] and len(entry["matches"]) < 100:
                        entry["matches"].append(match)
                if node[:1] in {"{", "["}:
                    try:
                        visit(json.loads(node), path + ".json", depth + 1)
                    except (ValueError, TypeError):
                        pass

        visit(payload, "$result")
        temporary = REPORT.with_suffix(".tmp")
        temporary.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(REPORT)
    except (OSError, TypeError, ValueError):
        pass
