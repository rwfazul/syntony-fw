# Deployment

This directory contains container build definitions, runnable examples and Grid’5000 deployment tools.

| Directory | Purpose |
|---|---|
| [docker](docker/README.md) | Build an all-module toolbox image |
| [examples/local](examples/local/README.md) | Run the complete service recovery instantiation with one image per module |
| [examples/monitors](examples/monitors/README.md) | Collect actual HTTP, command and operating-system metrics |
| [examples/grid5000](examples/grid5000/README.md) | Deploy Syntony or CEP on reserved nodes and explore the optional Ceph example |

## Compose entry points

The service definitions are in `deployment/examples/local/compose.json`. The root `docker-compose.yml` includes that file so `docker compose up` and `make up` work directly from the checkout root. Keeping a root entry point is a convenience for command discovery; the deployment directory owns the service definitions.

Both commands select the same local services:

```bash
docker compose up --build -d --wait
docker compose -f deployment/examples/local/compose.json up --build -d --wait
```

Run either command from the repository root. JSON is accepted as Compose/YAML syntax. The [host-guard override](examples/local/host-guard.compose.json) adds an operating-system Monitor/Composer and selects the extended instance configuration.
