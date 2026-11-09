#!/usr/bin/env python3
"""Read selected numeric supervisor metrics for a command-source Monitor."""

import argparse
import json
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--fields", required=True)
    args = parser.parse_args()
    with urllib.request.urlopen(args.url, timeout=2.0) as response:
        metrics = json.loads(response.read().decode("utf-8"))
    selected = {field: metrics[field] for field in args.fields.split(",") if field}
    print(json.dumps(selected, sort_keys=True))


if __name__ == "__main__":
    main()
