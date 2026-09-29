"""Run every backend test suite (tests/test_*.py), each in its own process.

    python tests/run_all.py            # all suites
    python tests/run_all.py labs auth  # only suites whose name contains "labs" or "auth"

Each suite is also a standalone script (python tests/test_labs.py). Suites use
create_app("testing"): a fresh in-memory SQLite database per run, seeded with the synthetic
demo data — no local database or server is touched. Exit code 1 if any suite fails.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # suites print non-ASCII text


def main(filters):
    suites = sorted(p for p in HERE.glob("test_*.py") if not filters or any(f in p.stem for f in filters))
    if not suites:
        print("no matching suites")
        return 1
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    rows, failed = [], 0
    for suite in suites:
        start = time.monotonic()
        proc = subprocess.run([sys.executable, str(suite)], cwd=HERE.parent, env=env, capture_output=True, text=True, encoding="utf-8")
        elapsed = time.monotonic() - start
        m = re.search(r"(\d+)/(\d+) checks passed", proc.stdout)
        passed = proc.returncode == 0 and m and m.group(1) == m.group(2)
        failed += not passed
        rows.append((suite.stem, m.group(0) if m else "no summary", "PASS" if passed else "FAIL", elapsed))
        if not passed:
            print(f"\n----- {suite.name} -----")
            print("\n".join(line for line in proc.stdout.splitlines() if not line.startswith("[PASS]")))
            print(proc.stderr[-3000:])
    print()
    for name, summary, status, elapsed in rows:
        print(f"{status}  {name:24s} {summary:22s} {elapsed:5.1f}s")
    total = sum(int(re.match(r"(\d+)", r[1]).group(1)) for r in rows if r[1][0].isdigit())
    print(f"\n{len(rows) - failed}/{len(rows)} suites passed, {total} checks")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
