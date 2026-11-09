# Instance configurations

An instance configuration selects catalog packages and sets timing, required watermark domains and parameter bindings. JSON includes are resolved relative to the file containing them. Only included packages become active.

| File | Purpose | How to run |
|---|---|---|
| `syntony_config.json` | HTTP service recovery across application and dependency domains | `make up` |
| `service-resilience-host-guard.json` | The same application with an extra host-load domain that temporarily blocks restarts during host pressure | Use the host-guard Compose override in the catalog guide |
| `service-resilience-filesystem-guard.json` | Guard restarts using observed filesystem capacity | [Filesystem guard example](../docs/extending-the-catalog.md#compose-a-filesystem-guard) |
| `service-resilience-meta.json` | Switch policy profiles in response to dependency health | [Meta-reconfiguration example](../docs/meta-reconfiguration.md) |
| `examples/monitor-system.json` | Observe actual operating-system load | Monitor/Composer commands in the monitor guide |
| `examples/monitor-command.json` | Read actual host load and disk capacity through a JSON command | Monitor/Composer commands in the monitor guide |
| `examples/monitor-http.json` | Read metrics from the running example service over HTTP | Set `METRICS_URL` to an accessible service endpoint |

The host-guard configuration adds a Monitor and Composer for `operating_system`, a high-load observation, an `E_block` policy and that domain's watermark requirement. It extends the service recovery instance with host-pressure blocking and an explicit `E_unblock` event when load returns to the configured normal range.

The monitor-only examples collect observations without requesting actions. The complete recovery instances additionally include CEP patterns, policy state guards, exclusivity and verified action handlers.

```bash
python -m runtime validate --config config/syntony_config.json
python -m runtime validate --config config/service-resilience-host-guard.json
```

See [monitor examples](../deployment/examples/monitors/README.md), [Specification Catalog extension](../docs/extending-the-catalog.md) and [policy state](../docs/state-and-policies.md) for configuration and execution steps.

## Parameters and environment bindings

`syntony_config.json` selects the supplied **service recovery instantiation**. Its `GATEWAY_URL` parameter is the base URL of that example's managed HTTP service, named `gateway` in Compose. The service supervises the worker process and exposes metric, state and control endpoints.

Both domain Monitors read `${GATEWAY_URL}/metrics`; action handlers use the same address to inspect or control the service. `http://gateway:8080` is reachable by service name inside the Compose network. From a host Python process, use the published address instead:

```bash
export GATEWAY_URL=http://127.0.0.1:18081
python -m runtime validate --config config/syntony_config.json
```

`GATEWAY_URL` belongs to this application example. Another instantiation should declare parameters for its own endpoints and select the corresponding domain/policy packages. `REDIS_URL` separately selects Syntony's Redis transport and state store.

## Configuration fields

| Field | Purpose |
|---|---|
| `schema_version` | Selects configuration format version 1 |
| `includes` | Selects domain, event and policy packages; paths are relative to the containing JSON file |
| `parameters` | Declares string bindings such as service URLs; matching environment variables supply deployment-specific values |
| `timing` | Sets sampling interval, watermark delay and allowed lateness |
| `cep.watermark_domains` | Lists domains whose watermarks must progress to close control rounds |
| `cep.tick` | Sets control-round duration and retained history |

Objects merge recursively, lists concatenate and conflicting scalar values are rejected. To change typed thresholds or timing values, select a specialized package/configuration. Processes load a validated snapshot at startup; restart the instance to activate configuration changes.
