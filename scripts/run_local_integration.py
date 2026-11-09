#!/usr/bin/env python3
"""Build/start the local modules, verify real recovery, collect logs, and stop them."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-running", action="store_true")
    args = parser.parse_args()
    compose = ["docker", "compose"]
    existing = command([*compose, "ps", "--quiet"], capture_output=True).stdout.strip()
    if existing:
        raise SystemExit(
            "An instance is already running. Stop it or run deployment/examples/local/demo.py directly."
        )
    try:
        command([*compose, "up", "--build", "-d", "--wait", "--wait-timeout", "180"])
        command([sys.executable, "deployment/examples/local/demo.py"])
    finally:
        subprocess.run([*compose, "logs", "--tail", "100"], cwd=ROOT, check=False)
        if not args.keep_running:
            subprocess.run([*compose, "down"], cwd=ROOT, check=False)


if __name__ == "__main__":
    main()
