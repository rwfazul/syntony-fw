#!/usr/bin/env python3
"""Read stream lengths and recent event envelopes from a Redis instance."""

import argparse
import json
import os

import redis


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("--count must be 1..100")
    client = redis.Redis.from_url(args.redis_url, decode_responses=True, socket_connect_timeout=5)
    streams = sorted(
        set(client.scan_iter("metrics:*"))
        | {
            "events:obs",
            "events:late",
            "events:coord",
            "executor:commands",
            "clock:watermark",
            "clock:tick",
        }
    )
    report = {
        name: {
            "length": client.xlen(name),
            "recent": [
                (ident, json.loads(fields["data"]))
                for ident, fields in client.xrevrange(name, count=args.count)
            ],
        }
        for name in streams
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
