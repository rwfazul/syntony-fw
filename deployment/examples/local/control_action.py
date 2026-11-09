#!/usr/bin/env python3
"""Apply one C2 command and verify its postcondition."""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def request_json(url, method="GET", timeout=5.0):
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload


def post(gateway, path, params=None):
    query = urllib.parse.urlencode(params or {})
    suffix = f"?{query}" if query else ""
    return request_json(f"{gateway}{path}{suffix}", method="POST")


def wait_for_restarted_worker(gateway, previous_generation, timeout_seconds=2.0):
    deadline = time.time() + timeout_seconds
    last_status, last_state = 0, {}
    while time.time() < deadline:
        last_status, last_state = request_json(f"{gateway}/state")
        if (
            last_status == 200
            and last_state.get("local_service_healthy") is True
            and last_state.get("generation", 0) > previous_generation
        ):
            return last_status, last_state
        time.sleep(0.1)
    return last_status, last_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="http://gateway:8080")
    parser.add_argument("--action", required=True)
    args = parser.parse_args()

    before_status, before = request_json(f"{args.gateway}/state")
    if args.action == "ENABLE_FALLBACK":
        status, _ = post(args.gateway, "/control/fallback", {"enabled": 1})
        state_status, state = request_json(f"{args.gateway}/state")
        verified = status == 200 and state_status == 200 and state.get("fallback_enabled") is True
        details = {"action": args.action, "verified": verified, "state": state}
    elif args.action == "DISABLE_FALLBACK":
        status, _ = post(args.gateway, "/control/fallback", {"enabled": 0})
        state_status, state = request_json(f"{args.gateway}/state")
        verified = status == 200 and state_status == 200 and state.get("fallback_enabled") is False
        details = {"action": args.action, "verified": verified, "state": state}
    elif args.action == "VERIFY_LOCAL_SERVICE":
        status, state = request_json(f"{args.gateway}/state")
        work_status, _ = request_json(f"{args.gateway}/work")
        verified = (
            status == 200 and work_status == 200 and state.get("local_service_healthy") is True
        )
        details = {"action": args.action, "verified": verified, "state": state}
    elif args.action == "RESTART_LOCAL_SERVICE":
        status, restart = post(args.gateway, "/control/restart-local")
        state_status, state = wait_for_restarted_worker(
            args.gateway,
            before.get("generation", 0),
        )
        verified = (
            status == 200
            and state_status == 200
            and restart.get("post_worker_healthy") is True
            and state.get("local_service_healthy") is True
            and state.get("generation", 0) > before.get("generation", 0)
        )
        details = {
            "action": args.action,
            "verified": verified,
            "pre_worker_healthy": before.get("local_service_healthy")
            if before_status == 200
            else None,
            "restart": restart,
            "state": state,
        }
    else:
        raise SystemExit(f"unsupported action: {args.action}")

    print(json.dumps(details, sort_keys=True))
    if not details["verified"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
