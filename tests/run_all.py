"""Run every test suite and print a summary:   python tests/run_all.py

* test_engine.py / test_media.py / test_luts.py : real code, synthetic files (need only Pillow)
* smoke_gui_headless.py                          : the window's logic against a fake Qt (no Qt needed)
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = ["test_engine.py", "test_media.py", "test_luts.py", "smoke_gui_headless.py"]

failed = []
for name in SUITES:
    r = subprocess.run([sys.executable, str(HERE / name)], capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ["(no output)"]
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'}  {name:28} {tail[0]}")
    if r.returncode:
        failed.append(name)
        print((r.stdout + r.stderr)[-3000:])
print("\nAll test suites passed." if not failed else f"\nFailed: {', '.join(failed)}")
sys.exit(1 if failed else 0)
