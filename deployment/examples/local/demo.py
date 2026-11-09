#!/usr/bin/env python3
"""Exercise real HTTP requests and verify an actual worker restart."""

import argparse
import json
import time
import urllib.error
import urllib.request


def request(base, path, method="GET"):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(base + path, method=method), timeout=3
        ) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return json.load(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://localhost:18081")
    args = parser.parse_args()
    request(args.gateway, "/work")
    before = request(args.gateway, "/state")
    request(args.gateway, "/inject/crash-local", "POST")
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        request(args.gateway, "/work")
        state = request(args.gateway, "/state")
        if (
            state["generation"] > before["generation"]
            and state["local_service_healthy"]
            and state["syntony_restart_count"] > before["syntony_restart_count"]
        ):
            print(json.dumps({"verified": True, "before": before, "after": state}, indent=2))
            return
        time.sleep(0.2)
    raise SystemExit("No verified Syntony restart within 35 seconds; inspect service logs")


if __name__ == "__main__":
    main()
