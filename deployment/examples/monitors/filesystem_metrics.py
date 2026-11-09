#!/usr/bin/env python3
"""Collect capacity from a real filesystem and emit a nested JSON snapshot."""

import argparse
import json
import shutil
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=".", help="A path on the filesystem to measure")
    args = parser.parse_args()
    usage = shutil.disk_usage(args.path)
    print(
        json.dumps(
            {
                "sample": {"timestamp": time.time()},
                "filesystem": {
                    "free_bytes": usage.free,
                    "used_percent": 100 * usage.used / usage.total,
                },
            }
        )
    )


if __name__ == "__main__":
    main()
