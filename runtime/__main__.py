"""python -m runtime validate|monitor|composer|cep|coordinator|executor."""

import argparse
import importlib
import json
import os

from .configuration import load_syntony_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "component", choices=["validate", "monitor", "composer", "cep", "coordinator", "executor"]
    )
    parser.add_argument("--config")
    parser.add_argument("--domain")
    args = parser.parse_args()
    if args.config:
        os.environ["SYNTONY_CONFIG_PATH"] = os.path.abspath(args.config)
    try:
        config = load_syntony_config()
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f"Invalid instance configuration: {error}")
    if args.component == "validate":
        print(
            json.dumps(
                {
                    "valid": True,
                    "domains": list(config["domains"]),
                    "patterns": [p["name"] for p in config.get("cep", {}).get("patterns", [])],
                }
            )
        )
        return
    if args.domain:
        os.environ["DOMAIN"] = args.domain
    if args.component in {"monitor", "composer"} and os.getenv("DOMAIN") not in config["domains"]:
        parser.error("--domain must select a configured domain")
    if args.component == "executor":
        os.environ.setdefault("DOMAIN", "all")
    module = importlib.import_module(f".{args.component}.{args.component}", "runtime")
    getattr(module, f"{args.component}_loop")()


if __name__ == "__main__":
    main()
