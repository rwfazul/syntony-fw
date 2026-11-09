# Grid’5000 deployment examples

These examples show how to deploy Syntony on reserved Grid’5000 nodes. They cover source and image distribution, per-host configuration and service checks.

Choose the example that matches the intended topology:

| Example | Tool | Physical placement |
|---|---|---|
| [Syntony](syntony/README.md) | `deploy.py --profile syntony` | All modules and the HTTP application on one reserved node |
| [CEP only](cep/README.md) | `deploy.py --profile cep` | CEP and Redis on one node; real external producers required |
| [Ceph and Syntony](ceph/README.md) | `ceph/cluster.py` | Three storage nodes, two RGW nodes and one application node; collocated or distributed Syntony |

The six-node example generates a Compose descriptor for each host and distributes the Syntony sources, configuration and container images to the reserved nodes.

## Files and entry points

| File | Responsibility |
|---|---|
| `deploy.py` | Generate a one-node payload and run its deploy, check, log and stop operations. |
| `remote.py` | Execute local commands and quoted SSH argument arrays with error reporting. |
| `job.sh` | Provision the selected environment and run the one-node profile during an OAR job. |
| `ceph/prepare.py` | Generate six-node Ceph configurations, Syntony placements and a reservation inventory. |
| `ceph/cluster.py` | Check reservation ownership, distribute sources/images, start services and validate cluster health. |
| `ceph/entry.sh` | Initialize the persistent state needed by a MON, MGR, OSD or RGW and start that daemon. |
| `ceph/job.sh` | Run the six-node deployment lifecycle within an OAR job. |
| `ceph/probe.py` | Read an existing RADOS object and report measured latency from a MON container. |
| `ceph/haproxy.cfg` | Define the gateway routing and administration endpoints used by generated proxy configuration. |

The Python entry points provide `--help`. `prepare` writes local files; the other deployment operations can execute commands on reserved nodes. The generated inventory records node roles, addresses, image bindings and the active job identity, so inspect it before invoking remote operations.

## Prerequisites and transfer

On the frontend: Python 3.11+, SSH, rsync, OAR/Kadeploy access and a usable SSH key. On each target: Docker Engine and Compose v2, reachable private IPs, sufficient disk/memory, and clock synchronization. Docker package names and Kadeploy environment names vary by site: provision an environment with these tools rather than assuming that a minimal OS image contains them.

Build a source bundle locally:

```bash
python3 scripts/bundle.py --output dist/syntony-fw.tar.gz
```

Transfer the archive and its `.sha256`/`.sha256.json` sidecars to the selected site frontend. Verify `sha256sum -c syntony-fw.tar.gz.sha256`, extract into a new directory, and run commands from the extracted directory. The bundle includes the operational scripts, module Dockerfiles, local Compose, catalog, license and docs; it excludes generated state and virtual environments.

Use the actual node list from `$OAR_NODEFILE`. Prepare a fresh output directory per allocation. For a deploy reservation, provision its nodes with a Kadeploy environment available at that site, initialize host keys for those freshly provisioned nodes, and verify SSH plus `docker compose version` before running the tools. Preparation alone never provisions a node or contacts Docker.

## One-node Syntony or CEP

From the frontend inside the reservation:

```bash
sort -u "$OAR_NODEFILE" > reserved-nodes.txt
python3 deployment/examples/grid5000/deploy.py prepare \
  --nodes reserved-nodes.txt --output generated/grid-single \
  --profile syntony --image 'REGISTRY/ORG/syntony-{module}:VERSION'
python3 deployment/examples/grid5000/deploy.py deploy --output generated/grid-single
python3 deployment/examples/grid5000/deploy.py check --output generated/grid-single
python3 deployment/examples/grid5000/deploy.py logs --output generated/grid-single
python3 deployment/examples/grid5000/deploy.py stop --output generated/grid-single
```

Replace the registry/organization/version with your actual published images. `{module}` resolves to `monitor`, `composer`, `cep`, `coordinator`, `executor` or `example`. This path pulls prebuilt images. Alternatively, a single explicit all-module toolbox image can be passed for experimentation; the normal local/release model uses module images.

`prepare` copies config/catalog into a self-contained payload and writes SHA-256 metadata. `deploy` checks that metadata and reservation membership, synchronizes files to `/opt/syntony`, pulls images, starts services and validates configuration. `check` verifies service presence/running state and Redis connectivity. It does not prove that a domain-specific reconfiguration occurred: run the functional verification for the selected profile.

The one-node OAR [job.sh](job.sh) is a template for environment provisioning and deployment. Configure `SYNTONY_SOURCE_DIR`, `SYNTONY_IMAGE` (including `{module}`), `GRID_ENVIRONMENT` and optional `SYNTONY_PROFILE`. It requires an environment that already supplies Docker/Compose. OAR resources and environment names must be selected for the current site. Do not use a previous reservation's inventory.

## Optional six-node deployment example

Follow [ceph/README.md](ceph/README.md) for topology, image fan-out, collocated versus distributed Syntony placement, start ordering and health checks. Unlike the simple profile, it can build images on a reserved node without publishing them to a registry.

The example deploys a Ceph cluster alongside the Syntony HTTP recovery instance. See the example guide for cluster health checks and application recovery verification.

## Operating scope

All remote actions require target membership in the active OAR reservation. Tools use SSH rather than exposing the Docker daemon over TCP. The Ceph lab configuration disables internal Ceph authentication; the distributed Syntony example exposes Redis on the application node's private IP without authentication so remote modules can connect. Use only isolated reserved resources and do not expose these endpoints publicly. Configure authenticated/private transport before using a different environment.

Stop operations preserve volumes, files and block-file state. Explicitly manage that state before a new instance. No live reservation was provisioned while preparing this distribution. Offline generation, schema checks, and unit tests are distinct from a successful deployment at a Grid site.

## Reservation identity and existing state

Prepare the deployable inventory inside the active reservation so `OAR_JOB_ID` is recorded. An offline-generated inventory may be inspected/tested but must be regenerated inside the actual reservation before remote operations. Node membership alone is insufficient: the recorded job ID must match the active job. The Ceph source-sync step also refuses an existing remote deployment with a different FSID; stopping containers does not make an old block-file state compatible with a new cluster identity.
