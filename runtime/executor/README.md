# Executor module

See the [Executor interface and behavior](../../docs/modules.md) and [catalog extension guide](../../docs/extending-the-catalog.md).

Build from the repository root:

```bash
docker build -f runtime/executor/Dockerfile -t syntony-executor:dev .
```

The local Compose already uses this Dockerfile and `runtime/executor/requirements.txt`. Its runtime command is `python -m runtime executor`; Monitors and Composers also require `--domain` with a selected domain. The package `__init__.py` declares the public imports. Configuration is validated before a process starts.
