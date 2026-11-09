# Tests

Run `make test` from the repository root after installing `.[dev]`. Install `redis-server` or set `REDIS_SERVER` to its executable to include the transaction and complete-pipeline tests.

| File | Coverage |
|---|---|
| `test_runtime.py` | Catalog validation, watermark admission, local rules, stable correlation, command execution and feedback |
| `test_runtime_contracts.py` | Event envelopes, temporal boundaries, configuration snapshots, documentation and reservation identity |
| `test_control_state.py` | Atomic admission, exclusive plans, configured state transitions, blocking/release and actual Redis/HTTP process recovery |
| `test_application_and_grid.py` | HTTP/process adapters, monitor examples and compact Grid deployment profiles |
| `test_distribution.py` | Package interfaces, example configurations, source bundles and generated Ceph/Syntony layouts |

Tests create temporary services, files and Redis state. The Redis tests start a dedicated server bound to loopback. Docker image execution is exercised separately through `make integration`; Grid deployment is checked in the intended reservation.

## Temporal and coordination scenarios

Run the following command with `redis-server` on `PATH` or set `REDIS_SERVER` to its executable:

```bash
python -m unittest discover -s tests -p test_control_state.py -v
```

| Scenario | Assertion |
|---|---|
| Watermarks and stable rounds | The CEP waits for the required domains before closing a round. It resumes from the saved Round Table and cursors after restart without republishing committed rounds. |
| Domain isolation | A domain-scoped lease blocks one domain's restart plan. Another domain dispatches the same action type while the lease is active. The blocked plan can dispatch after explicit release. |
| Exclusivity | Concurrent plans compete for the same exclusive class. A single plan dispatches, and its reservation remains held until all its actions finish. |
| Feedback | The Executor emits success, failure and timeout observations. The CEP converts each feedback subtype into a matching follow-up intent after the corresponding round becomes stable. |

The isolation scenario measures admission and dispatch under a domain-scoped lease. The thesis's queue consumption and throughput measurements belong to its separate workload scenario.
