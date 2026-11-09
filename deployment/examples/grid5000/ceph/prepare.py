"""Generate per-host Compose files and an inventory without deploying anything."""

import ipaddress
import json
import os
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HAPROXY = (
    "haproxy:3.0.11-alpine@sha256:e49b7beb96f4dba79f9a671515b469a6e516a2b88ff0681a820b184bba1effbd"
)
REMOTE = "/opt/syntony-grid"


def validate_allocation(nodes, site, cluster):
    """Require six distinct nodes from the requested site and cluster."""
    pattern = re.compile(re.escape(cluster) + r"-[0-9]+\." + re.escape(site) + r"\.grid5000\.fr")
    if len(nodes) != 6 or len(set(nodes)) != 6 or any(not pattern.fullmatch(n) for n in nodes):
        raise ValueError(f"Expected six distinct {cluster} nodes in {site}")


def generate(
    nodes,
    addresses,
    output,
    interfaces=None,
    image="syntony-{module}:dev",
    layout="collocated",
):
    """Write Ceph services, Syntony placements and inventory for six node/IP pairs."""
    if len(nodes) != 6 or len(set(nodes)) != 6 or len(addresses) != 6 or len(set(addresses)) != 6:
        raise ValueError("Exactly six distinct reserved nodes and IPv4 addresses are required")
    if layout not in {"collocated", "distributed"} or "{module}" not in image:
        raise ValueError(
            "Select a supported layout and a distinct image template containing {module}"
        )
    for address in addresses:
        ipaddress.IPv4Address(address)
    output.mkdir(parents=True, exist_ok=False)
    app, gw1, gw2, *storage = nodes
    ips = dict(zip(nodes, addresses))
    fsid = str(uuid.uuid4())
    roles = {"haproxy": app, "rgw1": gw1, "rgw2": gw2, "mon": storage[0]}
    roles.update({"osd" + str(i): host for i, host in enumerate(storage)})
    base_conf = f"""[global]
fsid = {fsid}
mon initial members = a,b,c
mon host = {",".join(ips[n] for n in storage)}
ms bind msgr2 = false
ms bind ipv6 = false
auth cluster required = none
auth service required = none
auth client required = none
osd pool default size = 3
osd pool default min size = 2
osd pool default pg num = 32
osd pool default pgp num = 32
osd crush chooseleaf type = 1
osd memory target = 2147483648
osd memory target autotune = false
log to file = true
log to stderr = false
log file = /ceph/log/$name.log
admin socket = /ceph/run/$name.asok
[osd]
osd data = /ceph/osd
bluestore block path = /ceph/block
bluestore block size = 17179869184
bluestore block create = true
[client]
rgw frontends = beast port=8080
rgw thread pool size = 8
rgw enable ops log = true
rgw ops log rados = false
rgw ops log file path = /ceph/log/$name.ops.jsonl
rgw log http headers = http_x_experiment_request
"""
    for i, host in enumerate(storage):
        base_conf += f"[osd.{i}]\npublic addr = {ips[host]}\ncluster addr = {ips[host]}\n"
    (output / "ceph.conf").write_text(base_conf)
    hap = (Path(__file__).with_name("haproxy.cfg")).read_text()
    hap = hap.replace("ipv4@0.0.0.0:9999", "ipv4@127.0.0.1:9999").replace(
        "bind *:8080", "bind 127.0.0.1:18090"
    )
    for name, host in [("rgw1", gw1), ("rgw2", gw2)]:
        hap = hap.replace(f"{name}:8080", f"{ips[host]}:8080")
    (output / "haproxy.cfg").write_text(hap)
    per_host = {n: {"name": "g5k-ceph", "services": {}} for n in nodes}

    def ceph_service(name, host, command, data, extra=None):
        """Add a Ceph role with shared cluster configuration and persistent local state."""
        service = dict(
            image="syntony-ceph-kv:19.2.3",
            container_name="g5k-" + name,
            network_mode="host",
            entrypoint=["bash", "/grid/entry.sh"],
            command=[command],
            volumes=[
                f"{REMOTE}/state/{data}:/ceph",
                f"{REMOTE}/generated/ceph.conf:/ceph/ceph.conf:ro",
                f"{REMOTE}/source/deployment/examples/grid5000/ceph:/grid:ro",
                f"{REMOTE}/source/deployment/examples/grid5000/ceph/probe.py:/opt/syntony/examples/ceph/probe.py:ro",
            ],
            environment=dict(
                FSID=fsid, MON_A=ips[storage[0]], MON_B=ips[storage[1]], MON_C=ips[storage[2]]
            ),
            stop_grace_period="20s",
        )
        if extra:
            service.update(extra)
        per_host[host]["services"][name] = service
        return service

    for i, host in enumerate(storage):
        mon_name = "mon" if i == 0 else "mon" + str(i)
        for name, command, data in [
            (mon_name, "mon", "mon"),
            ("mgr" + str(i), "mgr", "mon"),
            ("osd" + str(i), "osd", "osd"),
        ]:
            s = ceph_service(name, host, command, data)
            s["environment"].update(
                MON_ID="abc"[i], OSD_ID=str(i), OSD_UUID=str(uuid.uuid4()), STORAGE_HOST=host
            )
            if command == "osd":
                s.update(cap_add=["NET_ADMIN"], cpus=4, mem_limit="4g")
    for name, host in [("rgw1", gw1), ("rgw2", gw2)]:
        ceph_service(name, host, name, name, dict(cpus=2, mem_limit="2g"))
    per_host[app]["services"]["haproxy"] = dict(
        image="g5k-haproxy:frozen",
        container_name="g5k-haproxy",
        network_mode="host",
        volumes=[f"{REMOTE}/generated/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro"],
    )
    if layout not in {"collocated", "distributed"}:
        raise ValueError("Layout must be collocated or distributed")
    syntony = json.loads((ROOT / "deployment/examples/local/compose.json").read_text())
    assignments = {name: app for name in syntony["services"]}
    if layout == "distributed":
        for name in assignments:
            if name.startswith(("monitor_application", "composer_application")):
                assignments[name] = gw1
            elif name.startswith(("monitor_external", "composer_external")):
                assignments[name] = gw2
            elif name in {"cep", "coordinator", "executor"}:
                assignments[name] = nodes[3 + ["cep", "coordinator", "executor"].index(name)]
    syntony_hosts = {host: {"name": "g5k-syntony", "services": {}, "volumes": {}} for host in nodes}
    for name, service in syntony["services"].items():
        service.pop("build", None)
        service.pop("ports", None)
        service["network_mode"] = "host"
        service["depends_on"] = {
            k: v
            for k, v in service.get("depends_on", {}).items()
            if assignments[k] == assignments[name]
        }
        if name == "redis":
            service["command"] = [
                "redis-server",
                "--bind",
                ips[app],
                "--protected-mode",
                "no",
                "--appendonly",
                "yes",
            ]
            service["healthcheck"]["test"] = ["CMD", "redis-cli", "-h", ips[app], "ping"]
            syntony_hosts[app]["volumes"] = syntony.get("volumes", {})
        else:
            role = name.split("_")[0] if name not in ("gateway", "dependency") else "example"
            service["image"] = image.replace("{module}", role)
            service.setdefault("environment", {}).update(
                REDIS_URL="redis://" + ips[app] + ":6379/0",
                GATEWAY_URL="http://" + ips[app] + ":8080",
            )
            if name == "gateway":
                service["environment"]["DEPENDENCY_URL"] = "http://127.0.0.1:8081"
            service["volumes"] = [
                REMOTE + "/source/config:/app/config:ro",
                REMOTE + "/source/specifications:/app/specifications:ro",
            ]
        syntony_hosts[assignments[name]]["services"][name] = service
    for host, config in per_host.items():
        (output / (host + ".json")).write_text(json.dumps(config, indent=2) + "\n")
    for host, config in syntony_hosts.items():
        if config["services"]:
            (output / ("syntony-" + host + ".json")).write_text(json.dumps(config, indent=2) + "\n")
    inventory = dict(
        nodes=nodes,
        addresses=ips,
        app=app,
        storage=storage,
        services=roles,
        fsid=fsid,
        allocation_id=os.getenv("OAR_JOB_ID"),
        replication=3,
        osd_count=3,
        remote_root=REMOTE,
        layout=layout,
        syntony_assignments=assignments,
        image_template=image,
        syntony_hosts=[host for host, c in syntony_hosts.items() if c["services"]],
        storage_interfaces=interfaces or [],
        site=nodes[0].split(".")[1],
    )
    (output / "deployment.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return inventory
