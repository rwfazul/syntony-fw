# Local deployment

Run `docker compose up --build -d --wait` from the repository root. The root Compose file includes `compose.json` here. Each Syntony module has its own Dockerfile; the managed application runs separately.

## Services and images

| Services | Image role | Inputs/outputs |
|---|---|---|
| `monitor_application_service`, `monitor_external_dependency` | Monitor | HTTP metrics → `metrics:<domain>` |
| `composer_application_service`, `composer_external_dependency` | Composer | Per-domain metrics → observations/watermarks |
| `cep` | CEP | Observations/watermarks → coordination events |
| `coordinator` | Coordinator | Coordination events → mapped commands |
| `executor` | Executor | Real commands → feedback and domain execution state |
| `gateway`, `dependency` | Example | Real HTTP service, supervised child process, dependency and real cached response |
| `redis` | Redis | Streams, pending delivery, decisions, action results |

Each runtime role uses `runtime/<role>/Dockerfile` and its requirements file. Build context is the repository root, so shared code and validation contracts remain available. Two services using the same role reuse its image. The managed application has its own Dockerfile rather than borrowing a Monitor image.

`IMAGE_PREFIX=ghcr.io/ORG/syntony IMAGE_TAG=0.1.0 docker compose build` names the six images as `.../syntony-monitor:0.1.0`, `.../syntony-cep:0.1.0`, etc. Building does not publish anything. Publish deliberately to your registry before selecting those tags on remote nodes. For prebuilt images use `docker compose pull` followed by `docker compose up -d --no-build --wait`.

## Configuration and module commands

`config/` and `specifications/` are mounted read-only. The commands explicitly select each component and, for Monitors/Composers, its domain. `REDIS_URL` supplies transport access. Global timing comes from the instance JSON. CEP tick duration, retention and correlation windows come from the validated instance JSON; legacy timing environment overrides are not used.

To run one component for debugging after the dependencies are available:

```bash
docker compose run --rm --no-deps cep python -m runtime validate
docker compose logs -f cep coordinator executor
docker compose exec redis redis-cli XRANGE events:coord - + COUNT 5
```

Do not start a second Coordinator/Executor against the same instance. To replace a role for development, stop its normal service first. Module images include the sources; code changes need a rebuild. For development only, a Compose override can bind `./runtime:/app/runtime:ro`; when invoking from this directory adjust that host path relative to this directory. Preserve the image's configuration/catalog paths when adding mounts.

## Readiness, ports and verification

Redis, gateway and dependency have health checks. Monitors wait for the gateway. Only the gateway is published, on `127.0.0.1:${GATEWAY_PORT:-18081}`. Redis stays on the Compose network. Example administrative/fault endpoints have no authentication and must remain local or inside a reserved lab.

```bash
make up
python3 deployment/examples/local/demo.py
# Or a fresh, automatically stopped verification:
make down
make integration
```

The demonstration warms the actual response cache, terminates the local child process, sends real HTTP requests, and waits for Syntony to restart the worker. It fails if no verified new generation appears. `make integration` captures logs and stops the services even on failure; it preserves volumes. Use `--keep-running` with its Python script when inspecting a fresh run interactively.

`docker compose down` preserves Redis state. `docker compose down --volumes` explicitly resets this project's state. Restart semantics and uncertainty handling are documented in the root README; replay is not a global checkpoint protocol.

## Policy state and recovery

The HTTP recovery example reserves `service_reconfiguration` while a service-changing plan runs. Known failure sets a recovery-needed guard; timeout or interrupted execution additionally retains exclusivity and uncertain state. Availability observations can trigger `VERIFY_LOCAL_SERVICE`, whose real health check must succeed before the guard is cleared. See [policy state and recovery](../../../docs/state-and-policies.md) for transitions and configuration.

For the optional host-pressure guard, use `config/service-resilience-host-guard.json` and `host-guard.compose.json` as described in the [catalog guide](../../../docs/extending-the-catalog.md).
