# Real monitor examples

These examples exercise each supported acquisition protocol. They read actual host or service measurements. The configurations also define a local Composer rule, so they can produce observations when connected to Redis. They do not request reconfiguration actions.

Run commands from the repository root after installing the Python package in an activated environment. The command example invokes `python`, which should select that environment.

## Inspect a source without Redis

```bash
# Kernel load average through the built-in system source.
python deployment/examples/monitors/probe.py --config config/examples/monitor-system.json

# Host load and filesystem capacity through a JSON-producing executable.
python deployment/examples/monitors/probe.py --config config/examples/monitor-command.json

# Actual application health/error metrics; first start the local example with make up.
python deployment/examples/monitors/probe.py --config config/examples/monitor-http.json
```

`probe.py` uses the runtime's `collect` interface and prints each configured source once. A missing endpoint, invalid timestamp or missing metric fails the command. It does not substitute a value or start the control pipeline.

## Publish metrics and compose observations

Start a dedicated Redis instance, then use separate terminals for the Monitor and Composer:

```bash
export REDIS_URL=redis://127.0.0.1:6379/0
python -m runtime monitor --domain host_capacity --config config/examples/monitor-command.json
```

```bash
export REDIS_URL=redis://127.0.0.1:6379/0
python -m runtime composer --domain host_capacity --config config/examples/monitor-command.json
```

Inspect the output with:

```bash
python scripts/inspect_streams.py --redis-url redis://127.0.0.1:6379/0
```

Use `operating_system` with `monitor-system.json`, or `service_health` with `monitor-http.json`, to run the other examples. Set the same configuration and Redis URL for both processes. These are separate instance configurations; use separate Redis state from an existing recovery deployment. There are no CEP action patterns in these observation-only instances.

## Configure the acquisition interfaces

| Source | Configuration | Behavior |
|---|---|---|
| `system` | Explicit paths such as `load_avg_1m` | Reads kernel load; Linux also exposes `memory_available_bytes` |
| `command` | An `argv` array and JSON field paths | Runs the executable, checks its exit status and parses stdout |
| `http_json` | An HTTP(S) URL and JSON field paths | Fetches the endpoint and parses the response |

The supplied host examples use Unix load average, which is not CPU percentage. Inside Docker it reflects the kernel visible to the container; on Docker Desktop this is normally its Linux VM. Filesystem capacity reflects the selected path. Adapt paths and thresholds to the intended host. `system_metrics.py --path /your/mount` measures another filesystem.

The HTTP example binds `METRICS_URL` to `http://127.0.0.1:18081/metrics` by default. Override it with an endpoint reachable from the Monitor:

```bash
METRICS_URL=http://service-host:8080/metrics \
  python deployment/examples/monitors/probe.py --config config/examples/monitor-http.json
```

The Monitor image includes these example scripts. For a custom command adapter, copy/install its executable and dependencies into your image or supply a mount. Command paths are resolved from the process working directory; the supplied relative path works from the repository root and `/app` in the image.

The [filesystem Composer example](../../../config/examples/README.md#composer-examples) combines a nested JSON command source with kernel load in one domain.

## Connect observations to adaptation

Use [the catalog extension guide](../../../docs/extending-the-catalog.md) to add CEP patterns, policy mappings and real action handlers. [The host-guard instance](../../../config/service-resilience-host-guard.json) demonstrates this: high host load produces an `E_block` lease that temporarily prevents application restarts. Its [Compose override](../local/host-guard.compose.json) starts the added Monitor and Composer.

For Ceph acquisition, the [Grid Ceph guide](../grid5000/ceph/README.md#optional-native-ceph-probe) explains the optional real librados probe and its required pool/object setup. It is a platform-specific adapter with Ceph dependencies.
