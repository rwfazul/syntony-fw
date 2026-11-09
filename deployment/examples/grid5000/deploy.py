#!/usr/bin/env python3
"""Prepare a self-contained Grid bundle, then explicitly deploy/check/stop it."""

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from remote import command, ssh

ROOT = Path(__file__).resolve().parents[3]
REMOTE = "/opt/syntony"


def prepare(nodes_file, output, image, profile):
    """Write a profile-specific Compose payload, source checksums and reservation inventory."""
    nodes = sorted(set(Path(nodes_file).read_text().split()))
    if not nodes or any(
        not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.grid5000\.fr", node) for node in nodes
    ):
        raise ValueError("Use fully qualified reserved Grid5000 node names")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    payload = output / "payload"
    payload.mkdir()
    for directory in ("config", "specifications"):
        shutil.copytree(ROOT / directory, payload / directory)
    compose = json.loads((ROOT / "deployment/examples/local/compose.json").read_text())
    if profile == "cep":
        compose["services"] = {
            k: v for k, v in compose["services"].items() if k in {"redis", "cep"}
        }
    for name, service in compose["services"].items():
        service.pop("build", None)
        if name != "redis":
            role = name.split("_")[0] if name not in ("dependency", "gateway") else "example"
            service["image"] = image.replace("{module}", role)
            service["volumes"] = [
                f"{REMOTE}/config:/app/config:ro",
                f"{REMOTE}/specifications:/app/specifications:ro",
            ]
    # The CEP profile intentionally keeps Redis private. Connect real producers
    # inside the same Docker network; access from the frontend via docker exec.
    (payload / "compose.json").write_text(json.dumps(compose, indent=2) + "\n")
    manifest = {
        str(p.relative_to(payload)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(payload.rglob("*"))
        if p.is_file()
    }
    inventory = dict(
        node=nodes[0],
        reserved_nodes=nodes,
        profile=profile,
        image=image,
        remote_root=REMOTE,
        sha256=manifest,
        allocation_id=os.getenv("OAR_JOB_ID"),
    )
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return inventory


def main():
    """Dispatch preparation, deployment, inspection or shutdown for a reserved node."""
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "deploy", "check", "logs", "stop"])
    parser.add_argument("--profile", choices=["syntony", "cep"], default="syntony")
    parser.add_argument("--nodes")
    parser.add_argument("--output", required=True)
    parser.add_argument("--image")
    args = parser.parse_args()
    folder = Path(args.output)
    if args.operation == "prepare":
        if not args.nodes or not args.image:
            parser.error("prepare requires --nodes and a reachable versioned --image")
        print(json.dumps(prepare(args.nodes, folder, args.image, args.profile), indent=2))
        return
    inv = json.loads((folder / "inventory.json").read_text())
    node = inv["node"]
    # Refuse accidental use of a prior allocation.
    import os

    allocated = os.getenv("OAR_NODEFILE")
    if not inv.get("allocation_id") or inv["allocation_id"] != os.getenv("OAR_JOB_ID"):
        parser.error("Inventory must be prepared in the current OAR_JOB_ID reservation")
    if not allocated or node not in Path(allocated).read_text().split():
        parser.error("Run inside the reservation; selected node must be in OAR_NODEFILE")
    compose = ["docker", "compose", "-p", "syntony", "-f", REMOTE + "/compose.json"]
    if args.operation == "deploy":
        for relative, expected in inv["sha256"].items():
            if hashlib.sha256((folder / "payload" / relative).read_bytes()).hexdigest() != expected:
                raise ValueError("Payload changed after preparation: " + relative)
        ssh(node, ["mkdir", "-p", REMOTE])
        command(
            [
                "rsync",
                "-a",
                "--no-owner",
                "--no-group",
                str(folder / "payload") + "/",
                "root@" + node + ":" + REMOTE + "/",
            ]
        )
        ssh(node, [*compose, "pull"])
        ssh(node, [*compose, "up", "-d", "--wait", "--wait-timeout", "120"])
        ssh(
            node,
            [*compose, "run", "--rm", "--no-deps", "cep", "python", "-m", "runtime", "validate"],
        )
    elif args.operation == "check":
        state = ssh(node, [*compose, "ps", "--all", "--format", "json"], capture=True)
        print(state)
        # Compose may return a JSON array or one JSON object per line.
        rows = (
            json.loads(state)
            if state.lstrip().startswith("[")
            else [json.loads(line) for line in state.splitlines() if line.strip()]
        )
        expected = set(json.loads((folder / "payload/compose.json").read_text())["services"])
        if {row["Service"] for row in rows if row["State"] == "running"} != expected:
            raise SystemExit("Some services are missing or stopped")
        pong = ssh(node, [*compose, "exec", "-T", "redis", "redis-cli", "ping"], capture=True)
        if pong.strip() != "PONG":
            raise SystemExit("Redis did not return PONG")
    elif args.operation == "logs":
        ssh(node, [*compose, "logs", "--tail", "100"])
    else:
        ssh(node, [*compose, "down"])


if __name__ == "__main__":
    main()
