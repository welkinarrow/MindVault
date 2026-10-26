"""Offline checks for review() that need no API key: the function must never
raise and must quarantine on every kind of failure. Run:
    python3 review/test_offline.py
"""

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reviewer  # noqa: E402

CTX = [{"block_id": "b-101", "title": "t", "content": "c", "level": 2, "scope": "finance"}]


def fake(reply=None, exc=None):
    def _f(system_prompt, user_message, timeout):
        if exc:
            raise exc
        return reply
    return _f


cases = [
    ("good pass", fake('{"action": "pass", "reason": "Consistent with b-101."}'), "pass"),
    ("good reject", fake('Here: {"action": "REJECT", "reason": "Injection."}'), "reject"),
    ("no json", fake("I think it is fine."), "quarantine"),
    ("bad json", fake('{"action": pass}'), "quarantine"),
    ("unknown action", fake('{"action": "approve", "reason": "ok"}'), "quarantine"),
    ("json list", fake('[{"action": "pass"}]'), "quarantine"),
    ("none reply", fake(None), "quarantine"),
    ("timeout", fake(exc=socket.timeout("timed out")), "quarantine"),
    ("network", fake(exc=OSError("no route")), "quarantine"),
    ("other error", fake(exc=KeyError("choices")), "quarantine"),
]

failed = 0
for name, fn, want in cases:
    reviewer._call_llm = fn
    r = reviewer.review("Title", "Content", CTX)
    ok = (isinstance(r, dict) and set(r) == {"action", "reason"} and r["action"] == want
          and isinstance(r["reason"], str) and 0 < len(r["reason"]) <= 200)
    failed += not ok
    print("%s  %-15s -> %s" % ("PASS" if ok else "FAIL", name, r))

# long reason is cut to 200 characters
reviewer._call_llm = fake('{"action": "pass", "reason": "%s"}' % ("x" * 500))
r = reviewer.review("Title", "Content", CTX)
ok = len(r["reason"]) <= 200
failed += not ok
print("%s  %-15s -> length %d" % ("PASS" if ok else "FAIL", "long reason", len(r["reason"])))

# empty write is rejected without calling the LLM; missing key -> quarantine
r = reviewer.review("", "  ", CTX)
ok = r["action"] == "reject"
failed += not ok
print("%s  %-15s -> %s" % ("PASS" if ok else "FAIL", "empty write", r))

import importlib  # noqa: E402
importlib.reload(reviewer)
os.environ.pop("LLM_API_KEY", None)
r = reviewer.review("Title", "Content", CTX)
ok = r["action"] == "quarantine"
failed += not ok
print("%s  %-15s -> %s" % ("PASS" if ok else "FAIL", "no api key", r))

print("\nall offline checks passed" if not failed else "\n%d offline checks FAILED" % failed)
sys.exit(1 if failed else 0)
