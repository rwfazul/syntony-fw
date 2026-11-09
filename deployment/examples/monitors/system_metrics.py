#!/usr/bin/env python3
"""Print a timestamped JSON snapshot of actual host load and disk capacity."""

import argparse
import json
import os
import shutil
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=".", help="Filesystem path whose capacity is measured")
    args = parser.parse_args()
    usage = shutil.disk_usage(args.path)
    print(
        json.dumps(
            {
                "timestamp": time.time(),
                "load_avg_1m": os.getloadavg()[0],
                "disk_free_bytes": usage.free,
                "disk_total_bytes": usage.total,
            }
        )
    )


if __name__ == "__main__":
    main()
