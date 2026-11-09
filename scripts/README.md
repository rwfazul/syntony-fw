# Operational scripts

Run these utilities from the repository root after installing the package.

| Script | Purpose | Command |
|---|---|---|
| `check_repository.py` | Check required assets, Python syntax, Markdown headings and local links | `make hygiene` |
| `bundle.py` | Package deployment sources with archive and per-file SHA-256 checksums | `python scripts/bundle.py --output dist/syntony-fw.tar.gz` |
| `inspect_streams.py` | Read stream lengths and recent envelopes from Redis | `python scripts/inspect_streams.py --redis-url redis://HOST:6379/0` |
| `run_local_integration.py` | Build/start a fresh Docker instance, verify process recovery, capture logs and stop services | `make integration` |

Grid node provisioning, source/image distribution and cluster lifecycle commands are documented under [deployment](../deployment/README.md).
