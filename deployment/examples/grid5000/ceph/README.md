# Six-node Ceph and Syntony deployment example

This example deploys a six-node Ceph cluster with per-host Ceph configuration, explicit physical roles, SSH/rsync distribution, build/export/fan-out/import of Docker images, ordered startup, three-monitor quorum checks and three-OSD health checks.

Syntony runs the HTTP/process recovery instantiation. To control Ceph, configure metric sources, events and action handlers for the Ceph interfaces used by your deployment.

## Topology

Six distinct nodes from the same selected Grid site/cluster are required. Roles are assigned in sorted reservation order.

| Node | Ceph infrastructure | Syntony: collocated | Syntony: distributed |
|---|---|---|---|
| 1 | HAProxy | Redis, application/dependency and all modules | Redis and application/dependency |
| 2 | RGW1 | — | Application-domain Monitor/Composer |
| 3 | RGW2 | — | Dependency-domain Monitor/Composer |
| 4 | MON a, MGR a, OSD 0 | — | CEP |
| 5 | MON b, MGR b, OSD 1 | — | Coordinator |
| 6 | MON c, MGR c, OSD 2 | — | Executor |

Three monitors provide quorum. Managers use Ceph's active/standby behavior. Pools use replication 3, `min_size=2`, and host failure domains. Each OSD uses a 16 GiB BlueStore block file in the provisioned filesystem; no physical disk is formatted by these scripts. Capacity and resource settings must be reviewed for the actual reservation.

The generated Ceph/HAProxy and distributed Syntony descriptors use host networking and explicit node IPs, because Docker bridge DNS does not span hosts. Syntony Monitors query the real gateway on node 1. Redis binds that node's private IP, which is also used by the remote modules. HAProxy's frontend/admin interface remains local on node 1. This is a private lab configuration; Ceph authentication is disabled and Redis is unauthenticated. Do not expose this topology to a public network.

## 1. Transfer and provision

Follow the [common guide](../README.md) to package/transfer the Syntony source tree and obtain a six-node deploy reservation. Provision an environment with Docker Engine, Compose v2, rsync, SSH and synchronized clocks. Use the site and cluster selected for your reservation.

```bash
sort -u "$OAR_NODEFILE" > reserved-nodes.txt
python3 deployment/examples/grid5000/ceph/cluster.py prepare \
  --nodes reserved-nodes.txt --site YOUR_SITE --cluster YOUR_CLUSTER \
  --layout distributed --output generated/ceph-grid
```

`prepare` resolves node IPs and writes `ceph.conf`, HAProxy configuration, per-host Ceph Compose descriptors, per-host Syntony descriptors and `deployment.json`. It does not deploy anything. Use `--layout collocated` to run all Syntony modules on the application node. `--image 'ORG/syntony-{module}:VERSION'` changes image names without changing placement; keep `{module}` so distinct roles never overwrite each other's image tags.

Check the generated inventory and service-to-host assignments before continuing.

## 2. Distribute sources and images

```bash
python3 deployment/examples/grid5000/ceph/cluster.py sync --output generated/ceph-grid
python3 deployment/examples/grid5000/ceph/cluster.py images --output generated/ceph-grid
```

`sync` copies the Syntony sources and configuration and generated descriptors to each node under `/opt/syntony-grid`. It excludes generated outputs, virtual environments and repository internals.

`images` builds the six module/example images using their Dockerfiles on node 1, builds the Ceph image, and pulls HAProxy/Redis. It saves the required image set once, transfers the archive through the frontend, checks SHA-256 on each destination, and loads it on the other nodes. It does not require a public Docker registry or a Docker TCP socket. The frontend stores/transfers the archive; builds run on reserved resources.

The Ceph Dockerfile pins the base image and uses its native administration tools and Python librados bindings. Image builds and native tool availability must be checked in the selected reservation.

To reuse an existing image archive with its `.sha256` sidecar:

```bash
python3 deployment/examples/grid5000/ceph/cluster.py images \
  --output generated/ceph-grid --image-archive /path/to/images.tar
```

The archive must contain all generated image tags, including Ceph, HAProxy, Redis and each Syntony module and the example application. Do not reuse an archive for a different source/image version without checking its provenance.

## 3. Start and check

```bash
# MON/MGR, quorum, OSDs, pools, RGWs and HAProxy:
python3 deployment/examples/grid5000/ceph/cluster.py start --output generated/ceph-grid
# Redis/application first, then remote Syntony modules:
python3 deployment/examples/grid5000/ceph/cluster.py start-syntony --output generated/ceph-grid
python3 deployment/examples/grid5000/ceph/cluster.py check --output generated/ceph-grid
python3 deployment/examples/grid5000/ceph/cluster.py logs --output generated/ceph-grid
```

The check requires Ceph `HEALTH_OK`, three up/in OSDs, three quorum monitors, HAProxy admin availability, expected running Syntony services and Redis `PONG`. It does not substitute for functional reconfiguration verification.

On the application node, verify recovery in the distributed Syntony deployment:

```bash
ssh root@NODE1 docker compose -p g5k-syntony \
  -f /opt/syntony-grid/generated/syntony-NODE1.json exec -T gateway \
  python deployment/examples/local/demo.py --gateway http://127.0.0.1:8080
```

Replace both NODE1 occurrences with the fully qualified node 1 hostname. This verifies the HTTP catalog's cross-domain control path even when modules are physically distributed. It does not verify a Ceph reconfiguration policy.

## Optional native Ceph probe

`probe.py` is an optional librados read-latency collector. It reads object `probe` in pool `kv-probe`; the deployment does not generate payload data or start this collector automatically. To use it, create that pool with an appropriate RGW-independent RADOS application flag, upload an actual operator-provided file as `probe`, and execute the script in the MON container where librados and Ceph configuration are available. The script is mounted at `/opt/syntony/examples/ceph/probe.py`.

The probe reports an error if the pool or object is missing. To use its output in Syntony, configure a metric source and observed events in the Specification Catalog.

## Stop and state lifecycle

```bash
python3 deployment/examples/grid5000/ceph/cluster.py stop --output generated/ceph-grid
```

This stops only the generated Compose projects; it keeps block files, volumes, images and generated configuration. Use a fresh allocation/output directory for a new Ceph cluster identity and explicitly manage old state. Reusing initialized Ceph files with a new FSID is invalid. The CLI checks all inventory nodes against the active `$OAR_NODEFILE` before remote operations.

The generated topology and image build must still be validated in a real Grid reservation. No six-node deploy is automatically run by tests or by `prepare`.

## Scheduled job template

`job.sh` provides an OAR job template for this example. Configure `SYNTONY_SOURCE_DIR`, `GRID_ENVIRONMENT`, `GRID_SITE`, `GRID_CLUSTER`, and optionally `SYNTONY_LAYOUT=distributed`. It provisions the reserved nodes, verifies Docker/Compose, prepares a fresh inventory, distributes sources/images, starts both layers, checks health, and keeps an inspection window before stopping services. Adapt the resource request and environment to the site.

The infrastructure setup does not create a default S3 identity or package credentials. Create a Ceph RGW user and manage its keys explicitly when connecting a real S3 client.

## Reservation identity and existing state

Prepare the deployable inventory inside the active reservation so `OAR_JOB_ID` is recorded. An offline-generated inventory may be inspected/tested but must be regenerated inside the actual reservation before remote operations. Node membership alone is insufficient: the recorded job ID must match the active job. The Ceph source-sync step also refuses an existing remote deployment with a different FSID; stopping containers does not make an old block-file state compatible with a new cluster identity.
