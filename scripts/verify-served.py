#!/usr/bin/env python3
"""Prove that what the browser gets is this tree.

A deploy can report success while the served page is a cached copy of last week's code; every
symptom of that reads as "my change did not happen". This walks the chain and exits non-zero on
any break:

  1. the root page is served and carries the version chip,
  2. /api/v1/version answers with the same version the manifest declares,
  3. the stamp reports a commit when this is a checkout or a stamped install,
  4. a marker string that only the current source has appears in the served JS.

    python3 scripts/verify-served.py 8775
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:%s" % (sys.argv[1] if len(sys.argv) > 1 else "8775")

# Strings that only a recent build carries. Update them when the work moves on.
MARKERS = ["id=\"app-version\"", "function showVersion", "wirePlayer"]


def get(path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(BASE + path, timeout=10) as answer:
        return answer.status, answer.read()


def main() -> int:
    problems = []

    status, body = get("/")
    page = body.decode("utf-8", "replace")
    if status != 200:
        problems.append("GET / answered %s" % status)
    for marker in MARKERS:
        if marker not in page:
            # The chip lives in index.html; the functions live in app.js, which the page names.
            _, script = get("/static/app.js") if "function" in marker or "wirePlayer" in marker else (200, page.encode())
            if marker.encode() not in script:
                problems.append("served content is missing marker %r" % marker)

    status, body = get("/api/v1/version")
    if status != 200:
        problems.append("GET /api/v1/version answered %s" % status)
    info = json.loads(body.decode("utf-8"))
    manifest = open("plugin.yaml", encoding="utf-8").read()
    declared = next(line.split(":", 1)[1].strip().strip('"') for line in manifest.splitlines()
                    if line.startswith("version:"))
    if not str(info.get("version", "")).startswith(declared):
        problems.append("version %r does not match the manifest %r" % (info.get("version"), declared))
    if not info.get("commit"):
        problems.append("no commit in the stamp — an install without a stamp cannot be identified")

    if problems:
        print("FAILED — what is served is not this tree:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("served: %s · %s · %s" % (info.get("version"), info.get("commit_short") or info.get("commit", "")[:8],
                                    (info.get("commit_date") or "")[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
