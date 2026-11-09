# Monitor instance configurations

These configurations select actual metric sources and local interpretation rules for the [monitor examples](../../deployment/examples/monitors/README.md).

- `monitor-system.json`: kernel load average through the built-in system source.
- `monitor-command.json`: host load and filesystem capacity through a JSON-producing executable.
- `monitor-http.json`: service health/error metrics from an HTTP JSON endpoint bound through `METRICS_URL`.

Use the guide to inspect each source once or start its Monitor and Composer. See [instance configuration](../README.md) for include and parameter semantics.

## Composer examples

| Instance | Domain | Rules |
|---|---|---|
| [composer-filesystem.json](composer-filesystem.json) | `filesystem_capacity` | Count high-utilization samples in an event-time window, combine disk usage and load, and detect the pressure-to-headroom sequence |
| [composer-service-health.json](composer-service-health.json) | `service_health` | Detect an unhealthy worker or high error rate, require both health signals to declare recovery, and recognize the failure-to-recovery sequence |

The filesystem example combines two sources in one domain. A Python command reads the actual filesystem and the built-in system source reads kernel load. The Monitor maps nested JSON fields and uses `sample.timestamp` as event time. Set `CAPACITY_PATH` to an existing path in the Monitor's environment.

```bash
python -m runtime validate --config config/examples/composer-filesystem.json
CAPACITY_PATH=/tmp python deployment/examples/monitors/probe.py \
  --config config/examples/composer-filesystem.json
```

To publish metrics and observations, start a dedicated Redis instance and run the Monitor and Composer in separate terminals with the same `REDIS_URL`.

```bash
CAPACITY_PATH=/tmp python -m runtime monitor --domain filesystem_capacity \
  --config config/examples/composer-filesystem.json
```

```bash
python -m runtime composer --domain filesystem_capacity \
  --config config/examples/composer-filesystem.json
```

From a third terminal, run `python scripts/inspect_streams.py` with the same Redis URL.

For HTTP rules, select `service_health` and `config/examples/composer-service-health.json` in both commands, start the example service, and set `METRICS_URL` if it uses a different address. The demo gateway exposes failure and recovery endpoints described in the [local guide](../../deployment/examples/local/README.md).

For a complete catalog composition with a restart guard, see the [filesystem guard example](../../docs/extending-the-catalog.md#compose-a-filesystem-guard).
