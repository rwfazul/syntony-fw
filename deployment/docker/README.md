# Toolbox image

`Dockerfile` builds an image containing all Syntony modules, instance configurations, catalog packages and local adapters. Its default command validates the selected instance.

```bash
docker build -f deployment/docker/Dockerfile -t syntony-toolbox:dev .
docker run --rm syntony-toolbox:dev
```

Run these commands from the repository root. Select a component with `python -m runtime <component>` and supply `REDIS_URL`, instance configuration and the domain for Monitors/Composers.

The [local deployment](../examples/local/README.md) builds the individual Dockerfiles under `runtime/<module>/`. Use those images to deploy each module as its own service.
