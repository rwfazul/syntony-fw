# Cep module

See the [Cep interface and behavior](../../docs/modules.md) and [catalog extension guide](../../docs/extending-the-catalog.md).

Build from the repository root:

```bash
docker build -f runtime/cep/Dockerfile -t syntony-cep:dev .
```

The local Compose already uses this Dockerfile and `runtime/cep/requirements.txt`. Its runtime command is `python -m runtime cep`; Monitors and Composers also require `--domain` with a selected domain. The package `__init__.py` declares the public imports. Configuration is validated before a process starts.
