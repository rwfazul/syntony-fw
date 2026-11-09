#!/usr/bin/env python3
"""Collect each configured source once and print metric envelopes without a broker."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from runtime.configuration import load_syntony_config
from runtime.monitor import collect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--domain", help="Collect only this configured domain")
    args = parser.parse_args()
    config = load_syntony_config(args.config)
    if args.domain and args.domain not in config["domains"]:
        parser.error("The requested domain is not configured")
    for domain, package in config["domains"].items():
        if args.domain and domain != args.domain:
            continue
        for source in package["monitor"]["sources"]:
            if source.get("enabled", True):
                timestamp, metrics = collect(source)
                print(
                    json.dumps(
                        dict(
                            domain=domain,
                            source=source["name"],
                            event_time=timestamp,
                            metrics=dict(metrics),
                        )
                    )
                )


if __name__ == "__main__":
    main()
