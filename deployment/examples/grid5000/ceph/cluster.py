#!/usr/bin/env python3
"""Distribute images and sources, start services, and check Ceph and Syntony nodes."""

import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

from prepare import HAPROXY, REMOTE, generate, validate_allocation

ROOT = Path(__file__).resolve().parents[4]


def command(args, **kwargs):
    """Run a local argument array and display captured diagnostics on failure."""
    try:
        return subprocess.run(args, check=True, text=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        if kwargs.get("capture_output"):
            if exc.stdout:
                print(exc.stdout, file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
        raise


def ssh(host, args, capture=False, timeout=None):
    """Execute an argument array as root over SSH without exposing a Docker TCP endpoint."""
    return command(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "root@" + host, shlex.join(args)],
        capture_output=capture,
        timeout=timeout,
    ).stdout


def compose(host, *args):
    """Run the Ceph Compose project assigned to one host."""
    return ssh(
        host,
        [
            "docker",
            "compose",
            "-p",
            "g5k-ceph",
            "-f",
            REMOTE + "/generated/" + host + ".json",
            *args,
        ],
        capture=True,
    )


def wait_for(check, seconds=180):
    """Retry a readiness callable until it succeeds or the deadline expires."""
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            return check()
        except (subprocess.CalledProcessError, AssertionError, ValueError) as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError("Readiness deadline exceeded") from last


def ceph(inv, *args):
    """Run a bounded Ceph administration command inside the first MON container."""
    return ssh(
        inv["storage"][0],
        ["docker", "exec", "g5k-mon", "timeout", "30", *args],
        capture=True,
        timeout=45,
    )


def health(inv):
    """Require healthy Ceph status, three active OSDs and a three-monitor quorum."""
    data = json.loads(ceph(inv, "ceph", "status", "--format", "json"))
    if data["health"]["status"] != "HEALTH_OK":
        raise ValueError("Ceph is not HEALTH_OK: " + json.dumps(data))
    if not data["osdmap"]["num_up_osds"] == data["osdmap"]["num_in_osds"] == 3:
        raise ValueError("Expected three up/in OSDs")
    quorum = json.loads(ceph(inv, "ceph", "quorum_status", "--format", "json"))
    if len(quorum["quorum"]) != 3:
        raise ValueError("Expected three quorum monitors")
    return data


def proxy_health(inv):
    """Check the HAProxy process and its local administration endpoint."""
    running = ssh(
        inv["app"],
        ["docker", "inspect", "--format", "{{.State.Running}}", "g5k-haproxy"],
        capture=True,
    )
    if running.strip() != "true":
        raise ValueError("HAProxy is not running")
    info = ssh(
        inv["app"],
        [
            "timeout",
            "5",
            "bash",
            "-c",
            "exec 3<>/dev/tcp/127.0.0.1/9999; printf 'show info\\n' >&3; cat <&3",
        ],
        capture=True,
        timeout=10,
    )
    if "Name: HAProxy" not in info:
        raise ValueError("HAProxy admin socket did not return show info")
    return info


def syntony(inv, host, *args):
    """Run the Syntony Compose project assigned to one host."""
    return ssh(
        host,
        [
            "docker",
            "compose",
            "-p",
            "g5k-syntony",
            "-f",
            REMOTE + "/generated/syntony-" + host + ".json",
            *args,
        ],
        capture=True,
    )


def verify_allocation(inv):
    """Reject inventories that do not match the current job and reserved nodes."""
    path = os.environ.get("OAR_NODEFILE")
    if not inv.get("allocation_id") or inv["allocation_id"] != os.getenv("OAR_JOB_ID"):
        raise ValueError("Inventory belongs to a different or unspecified OAR reservation")
    if not path or not set(inv["nodes"]).issubset(Path(path).read_text().split()):
        raise ValueError("All inventory nodes must belong to the current OAR_NODEFILE")


def sync_sources(output, inv):
    # Check every target before copying files to any node.
    """Check remote cluster identity, then copy sources and per-host descriptors."""
    for host in inv["nodes"]:
        prior = ssh(
            host,
            [
                "bash",
                "-c",
                "if test -f "
                + REMOTE
                + "/generated/deployment.json; then cat "
                + REMOTE
                + "/generated/deployment.json; fi",
            ],
            capture=True,
        ).strip()
        if prior and json.loads(prior).get("fsid") != inv["fsid"]:
            raise ValueError(
                "Existing Ceph state has another FSID on "
                + host
                + "; use a fresh deployment environment"
            )
    for host in inv["nodes"]:
        ssh(host, ["mkdir", "-p", REMOTE + "/source", REMOTE + "/generated"])
        command(
            [
                "rsync",
                "-a",
                "--no-owner",
                "--no-group",
                "--exclude=.git",
                "--exclude=.local",
                "--exclude=.env",
                "--exclude=.env.*",
                "--exclude=.ssh",
                "--exclude=.aws",
                "--exclude=.DS_Store",
                "--exclude=.venv",
                "--exclude=__pycache__",
                "--exclude=generated",
                "--exclude=dist",
                "--exclude=build",
                "--exclude=*.egg-info",
                "--exclude=*.tar",
                "--exclude=*.tar.gz",
                str(ROOT) + "/",
                "root@" + host + ":" + REMOTE + "/source/",
            ]
        )
        command(
            [
                "rsync",
                "-a",
                "--no-owner",
                "--no-group",
                "--exclude=*.tar",
                str(output) + "/",
                "root@" + host + ":" + REMOTE + "/generated/",
            ]
        )


def image_tags(output, inv):
    """Collect the Ceph, proxy and Syntony image tags required by the inventory."""
    tags = {"syntony-ceph-kv:19.2.3", "g5k-haproxy:frozen"}
    for host in inv["syntony_hosts"]:
        config = json.loads((output / ("syntony-" + host + ".json")).read_text())
        tags.update(s["image"] for s in config["services"].values())
    return sorted(tags)


def distribute_images(output, inv, archive=None):
    """Build or load images, verify transferred archives and load them on every node."""
    app = inv["app"]
    if archive:
        archive = Path(archive)
        expected = archive.with_suffix(archive.suffix + ".sha256").read_text().split()[0]
        if checksum(archive) != expected:
            raise ValueError("Image archive checksum mismatch")
        command(["scp", str(archive), "root@" + app + ":" + REMOTE + "/images.tar"])
        ssh(app, ["docker", "load", "-i", REMOTE + "/images.tar"])
    else:
        for role in ("monitor", "composer", "cep", "coordinator", "executor", "example"):
            dockerfile = (
                "runtime/" + role + "/Dockerfile"
                if role != "example"
                else "deployment/examples/local/Dockerfile"
            )
            tag = inv["image_template"].replace("{module}", role)
            ssh(
                app,
                [
                    "docker",
                    "build",
                    "-t",
                    tag,
                    "-f",
                    REMOTE + "/source/" + dockerfile,
                    REMOTE + "/source",
                ],
            )
        ssh(
            app,
            [
                "docker",
                "build",
                "--network",
                "host",
                "-t",
                "syntony-ceph-kv:19.2.3",
                "-f",
                REMOTE + "/source/deployment/examples/grid5000/ceph/Dockerfile.ceph",
                REMOTE + "/source",
            ],
        )
        ssh(app, ["docker", "pull", HAPROXY])
        ssh(app, ["docker", "tag", HAPROXY, "g5k-haproxy:frozen"])
        ssh(app, ["docker", "pull", "redis:7.4-alpine"])
        ssh(app, ["docker", "save", "-o", REMOTE + "/images.tar", *image_tags(output, inv)])
    local = output / "images.tar"
    command(["scp", "root@" + app + ":" + REMOTE + "/images.tar", str(local)])
    digest = checksum(local)
    local.with_suffix(".tar.sha256").write_text(digest + "  images.tar\n")
    for host in inv["nodes"][1:]:
        command(["scp", str(local), "root@" + host + ":" + REMOTE + "/images.tar"])
        actual = ssh(host, ["sha256sum", REMOTE + "/images.tar"], capture=True).split()[0]
        if actual != digest:
            raise ValueError("Transferred image checksum mismatch: " + host)
        ssh(host, ["docker", "load", "-i", REMOTE + "/images.tar"])
    for host in inv["nodes"]:
        ssh(
            host,
            ["docker", "image", "inspect", "--format", "{{.Id}}", *image_tags(output, inv)],
            capture=True,
        )


def checksum(path):
    """Compute the SHA-256 digest of an archive without loading it entirely into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def start_ceph(inv):
    """Start MON/MGR and OSD roles, initialize pools, then start gateways and the proxy."""
    for i, host in enumerate(inv["storage"]):
        mon = "mon" if i == 0 else "mon" + str(i)
        compose(host, "up", "-d", mon, "mgr" + str(i))
    wait_for(lambda: json.loads(ceph(inv, "ceph", "status", "--format", "json")))
    for i, host in enumerate(inv["storage"]):
        compose(host, "up", "-d", "osd" + str(i))

    def osds_ready():
        """Return native cluster status once all three OSDs are up."""
        report = json.loads(ceph(inv, "ceph", "status", "--format", "json"))
        if report["osdmap"]["num_up_osds"] != 3:
            raise ValueError("OSDs are not ready")
        return report

    wait_for(osds_ready)
    ceph(inv, "ceph", "config", "set", "mon", "mon_max_pg_per_osd", "512")
    ceph(inv, "ceph", "config", "set", "mon", "auth_allow_insecure_global_id_reclaim", "false")
    for key in (
        "rgw_zone_root_pool",
        "rgw_region_root_pool",
        "rgw_zonegroup_root_pool",
        "rgw_realm_root_pool",
        "rgw_period_root_pool",
    ):
        ceph(inv, "ceph", "config", "set", "client", key, "rgw.root")
    existing = set(json.loads(ceph(inv, "ceph", "osd", "pool", "ls", "--format", "json")))
    for pool in (
        "rgw.root",
        "default.rgw.control",
        "default.rgw.meta",
        "default.rgw.log",
        "default.rgw.buckets.index",
        "default.rgw.buckets.data",
    ):
        if pool not in existing:
            ceph(inv, "ceph", "osd", "pool", "create", pool, "32", "32")
        ceph(inv, "ceph", "osd", "pool", "set", pool, "pg_autoscale_mode", "off")
        ceph(inv, "ceph", "osd", "pool", "application", "enable", pool, "rgw")
    for name in ("rgw1", "rgw2", "haproxy"):
        compose(inv["services"][name], "up", "-d", name)
    wait_for(lambda: health(inv), 300)
    wait_for(lambda: proxy_health(inv), 60)


def main():
    """Dispatch a deployment operation after checking the active reservation."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["prepare", "sync", "images", "start", "start-syntony", "check", "logs", "stop"],
    )
    parser.add_argument("--nodes")
    parser.add_argument("--site")
    parser.add_argument("--cluster")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--layout", choices=["collocated", "distributed"], default="collocated")
    parser.add_argument("--image", default="syntony-{module}:dev")
    parser.add_argument("--image-archive")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.action == "prepare":
        if not all((args.nodes, args.site, args.cluster)):
            parser.error("prepare requires --nodes, --site and --cluster")
        nodes = sorted(set(Path(args.nodes).read_text().split()))
        validate_allocation(nodes, args.site, args.cluster)
        inv = generate(
            nodes,
            [socket.gethostbyname(n) for n in nodes],
            output,
            image=args.image,
            layout=args.layout,
        )
        print(json.dumps(inv, indent=2))
        return
    inv = json.loads((output / "deployment.json").read_text())
    verify_allocation(inv)
    if args.action == "sync":
        sync_sources(output, inv)
    elif args.action == "images":
        distribute_images(output, inv, args.image_archive)
    elif args.action == "start":
        start_ceph(inv)
    elif args.action == "start-syntony":
        # Redis/application dependencies are started before remote consumers.
        syntony(inv, inv["app"], "up", "-d", "--wait", "--wait-timeout", "120")
        for host in inv["syntony_hosts"]:
            if host != inv["app"]:
                syntony(inv, host, "up", "-d", "--wait", "--wait-timeout", "120")
    elif args.action == "check":
        report = {"ceph": health(inv), "proxy": proxy_health(inv), "syntony": {}}
        for host in inv["syntony_hosts"]:
            state = syntony(inv, host, "ps", "--all", "--format", "json")
            rows = (
                json.loads(state)
                if state.lstrip().startswith("[")
                else [json.loads(line) for line in state.splitlines() if line.strip()]
            )
            expected = set(
                json.loads((output / ("syntony-" + host + ".json")).read_text())["services"]
            )
            if {row["Service"] for row in rows if row["State"] == "running"} != expected:
                raise RuntimeError("Syntony services are missing or stopped: " + host)
            report["syntony"][host] = rows
        ping = syntony(
            inv,
            inv["app"],
            "exec",
            "-T",
            "redis",
            "redis-cli",
            "-h",
            inv["addresses"][inv["app"]],
            "ping",
        )
        if ping.strip() != "PONG":
            raise RuntimeError("Redis did not return PONG")
        print(json.dumps(report, indent=2))
    elif args.action == "logs":
        for host in inv["syntony_hosts"]:
            print(syntony(inv, host, "logs", "--tail", "100"))
        for host in inv["nodes"]:
            print(compose(host, "logs", "--tail", "100"))
    else:
        for host in inv["syntony_hosts"]:
            syntony(inv, host, "stop")
        for host in inv["nodes"]:
            compose(host, "stop")
        # Keep files, volumes and block-file state. Never format physical disks.


if __name__ == "__main__":
    main()
