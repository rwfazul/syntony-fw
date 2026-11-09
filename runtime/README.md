# Runtime

The runtime implements acquisition, local interpretation, event-time correlation, policy admission, execution and feedback. Start a component with `python -m runtime <component>`; its configuration is validated before the loop starts.

| Package | Responsibility |
|---|---|
| [monitor](monitor/README.md) | Collect metrics from configured sources |
| [composer](composer/README.md) | Apply temporal admission and compose domain-local observations |
| [cep](cep/README.md) | Evaluate patterns in stable rounds and emit coordination events |
| [coordinator](coordinator/README.md) | Manage action guards and admit complete action plans |
| [executor](executor/README.md) | Run commands, persist outcomes and emit feedback |
| [internal_state](internal_state/README.md) | Store flags, plans, exclusive reservations and outcome transitions |

`configuration.py` assembles and validates the Specification Catalog. `common.py` provides Redis access, configuration snapshots and logging. `streams.py` manages pending Coordinator/Executor deliveries. `__main__.py` provides the command-line entry point.

See [module interfaces](../docs/modules.md), [event contracts](../docs/event-contracts.md) and [policy state](../docs/state-and-policies.md) for behavior and extension points.
